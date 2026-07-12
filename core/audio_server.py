"""
Local HTTP bridge for streaming live, ffmpeg-generated audio into
pytgcalls' MediaStream.

Why this exists: pytgcalls runs `ffprobe` against its source once to
detect format (see MediaStream.check_stream()) BEFORE it ever opens the
source for real playback. A named pipe (FIFO) can only be read
start-to-finish exactly once by exactly one reader -- so ffprobe's
probe read consumes (or, for headerless raw PCM, simply can't parse)
the single producer's output before the real playback connection ever
gets a chance to read anything. That surfaces as "Broken pipe" (WAV
case) or a JSONDecodeError from ffprobe's empty output (raw PCM case).

An HTTP server sidesteps this entirely: ffprobe's probe and pytgcalls'
real playback become two independent HTTP requests, each served fresh
from whatever's currently arriving from the live ffmpeg source, with a
freshly-synthesized WAV header written at the start of every new
connection. The ffmpeg producer itself never touches ffprobe/pytgcalls
directly -- it only ever talks to our own code, which fans its output
out to however many HTTP clients happen to be connected.
"""

import asyncio
import contextlib
import struct
from typing import Dict, List, Optional

from aiohttp import web

from core.logger import get_logger

log = get_logger(__name__)

SAMPLE_RATE = 48000
CHANNELS = 2
BITS_PER_SAMPLE = 16
CHUNK_SIZE = 3840  # 20ms of stereo 16-bit 48kHz audio


def _wav_header(data_size: int = 0x7FFFFFFF) -> bytes:
    byte_rate = SAMPLE_RATE * CHANNELS * BITS_PER_SAMPLE // 8
    block_align = CHANNELS * BITS_PER_SAMPLE // 8
    return b"".join(
        [
            b"RIFF",
            struct.pack("<I", data_size + 36),
            b"WAVE",
            b"fmt ",
            struct.pack(
                "<IHHIIHH", 16, 1, CHANNELS, SAMPLE_RATE, byte_rate, block_align, BITS_PER_SAMPLE
            ),
            b"data",
            struct.pack("<I", data_size),
        ]
    )


class _LiveStream:
    def __init__(self, cmd: List[str], name: str):
        self.cmd = cmd
        self.name = name
        self.process: Optional[asyncio.subprocess.Process] = None
        self.subscribers: List[asyncio.Queue] = []
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        log.info("Spawning live stream '%s': %s", self.name, " ".join(self.cmd))
        self.process = await asyncio.create_subprocess_exec(
            *self.cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._pump())
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _pump(self) -> None:
        if self.process is None or self.process.stdout is None:
            return
        try:
            while True:
                chunk = await self.process.stdout.read(CHUNK_SIZE)
                if not chunk:
                    break
                for q in list(self.subscribers):
                    with contextlib.suppress(asyncio.QueueFull):
                        q.put_nowait(chunk)
        except Exception:  # noqa: BLE001
            log.exception("Live stream reader for '%s' crashed.", self.name)

    async def _drain_stderr(self) -> None:
        if self.process is None or self.process.stderr is None:
            return
        try:
            while True:
                line = await self.process.stderr.readline()
                if not line:
                    break
                text = line.decode(errors="ignore").rstrip()
                if text:
                    log.warning("[ffmpeg:%s] %s", self.name, text)
        except Exception:  # noqa: BLE001
            pass

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self.subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self.subscribers:
            self.subscribers.remove(q)

    def is_alive(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def stop(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
        if self._stderr_task:
            self._stderr_task.cancel()
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()


class AudioHTTPBridge:
    def __init__(self, host: str = "127.0.0.1", port: int = 8600):
        self.host = host
        self.port = port
        self._streams: Dict[str, _LiveStream] = {}
        self._app = web.Application()
        self._app.router.add_get("/audio/{key}", self._handle)
        self._runner: Optional[web.AppRunner] = None

    def url_for(self, key: str) -> str:
        return f"http://{self.host}:{self.port}/audio/{key}"

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        log.info("Audio HTTP bridge listening on http://%s:%s", self.host, self.port)

    async def stop(self) -> None:
        for stream in list(self._streams.values()):
            await stream.stop()
        self._streams.clear()
        if self._runner:
            await self._runner.cleanup()

    async def register_stream(self, key: str, cmd: List[str], name: str) -> str:
        await self.remove_stream(key)
        stream = _LiveStream(cmd, name)
        await stream.start()
        self._streams[key] = stream
        return self.url_for(key)

    async def remove_stream(self, key: str) -> None:
        stream = self._streams.pop(key, None)
        if stream:
            await stream.stop()

    def is_stream_alive(self, key: str) -> bool:
        stream = self._streams.get(key)
        return stream.is_alive() if stream else False

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        key = request.match_info["key"]
        stream = self._streams.get(key)
        if stream is None:
            raise web.HTTPNotFound()

        response = web.StreamResponse(status=200, headers={"Content-Type": "audio/wav"})
        await response.prepare(request)
        await response.write(_wav_header())

        q = stream.subscribe()
        try:
            while True:
                chunk = await q.get()
                await response.write(chunk)
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            stream.unsubscribe(q)
        return response
