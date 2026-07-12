"""
FFmpeg helpers: building the `-af` filter graph for volume/bass and
spawning the capture process that bridges the PulseAudio monitor into
the named pipe consumed by the outgoing voice chat stream.
"""

import asyncio
import collections
from typing import Optional

import config
from core.logger import get_logger

log = get_logger(__name__)

SAMPLE_RATE = 48000
CHANNELS = 2


def build_audio_filters(level: int, bass: int, muted: bool = False) -> str:
    """
    Translates the user-facing /level (1-25) and /bass (0-15) scales into
    an FFmpeg `-af` filter chain.

    level 1-25  -> volume multiplier 0.2x - 5.0x (level / 5)
    bass  0-15  -> low-shelf boost of 0-30 dB centered at 110 Hz
    """
    if muted:
        return "volume=0"

    level = max(config.MIN_LEVEL, min(config.MAX_LEVEL, level))
    bass = max(config.MIN_BASS, min(config.MAX_BASS, bass))

    volume_multiplier = round(level / 5, 3)
    bass_gain_db = bass * 2

    filters = [f"volume={volume_multiplier}"]
    if bass_gain_db > 0:
        filters.append(f"bass=g={bass_gain_db}:f=110:w=0.6")
    # Keep the signal from clipping after volume/bass boosts.
    filters.append("alimiter=limit=0.95")

    return ",".join(filters)


def build_capture_command(
    monitor_source: str,
    output_pipe_path: str,
    level: int,
    bass: int,
    muted: bool = False,
) -> list:
    """
    ffmpeg command that reads the PulseAudio monitor of the bridge sink,
    applies volume/bass filters, and writes raw PCM into the FIFO used as
    input for the outgoing voice chat stream.

    IMPORTANT: pytgcalls/tgcalls always expects raw PCM, 16-bit, 48kHz on
    its audio input -- never a container format. Wrapping the stream in
    a WAV header makes pytgcalls' internal reader try to probe/seek the
    stream to read format info, which fails on a non-seekable named pipe
    and surfaces as "Broken pipe" almost immediately after joining.
    """
    filters = build_audio_filters(level, bass, muted)
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-f",
        "pulse",
        "-i",
        monitor_source,
        "-af",
        filters,
        "-ac",
        str(CHANNELS),
        "-ar",
        str(SAMPLE_RATE),
        "-f",
        "s16le",
        "-y",
        output_pipe_path,
    ]


def build_silence_command(output_pipe_path: str) -> list:
    """
    The assistant must still send *something* while listening in the
    Logger Group's voice chat. We feed silence so the call stays open
    without echoing anything back into that chat. Raw PCM, same reason
    as build_capture_command above.
    """
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-ac",
        str(CHANNELS),
        "-ar",
        str(SAMPLE_RATE),
        "-f",
        "s16le",
        "-y",
        output_pipe_path,
    ]


def build_capture_command_stdout(
    monitor_source: str,
    level: int,
    bass: int,
    muted: bool = False,
) -> list:
    """
    Same as build_capture_command, but writes raw PCM to stdout
    (pipe:1) instead of a FIFO file, for use with AudioHTTPBridge.
    """
    filters = build_audio_filters(level, bass, muted)
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-f",
        "pulse",
        "-i",
        monitor_source,
        "-af",
        filters,
        "-ac",
        str(CHANNELS),
        "-ar",
        str(SAMPLE_RATE),
        "-f",
        "s16le",
        "pipe:1",
    ]


def build_silence_command_stdout() -> list:
    """
    Same as build_silence_command, but writes raw PCM to stdout
    (pipe:1) instead of a FIFO file, for use with AudioHTTPBridge.
    """
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-ac",
        str(CHANNELS),
        "-ar",
        str(SAMPLE_RATE),
        "-f",
        "s16le",
        "pipe:1",
    ]


def build_record_command(monitor_source: str, output_file_path: str) -> list:
    """
    Records the forwarded (post-filter) audio to a compressed file for
    /startrecord ... /stoprecord.
    """
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-f",
        "pulse",
        "-i",
        monitor_source,
        "-ac",
        str(CHANNELS),
        "-ar",
        str(SAMPLE_RATE),
        "-b:a",
        "192k",
        "-y",
        output_file_path,
    ]


async def _drain_stderr(name: str, process: asyncio.subprocess.Process) -> None:
    """
    Continuously reads ffmpeg's stderr and logs it. This is required, not
    optional: if nobody reads a subprocess's PIPE, the OS pipe buffer
    fills up once ffmpeg writes enough output and ffmpeg blocks trying to
    write more, which looks exactly like a mysterious hang on /join. This
    also gives us the real reason a capture process died (e.g. PulseAudio
    not running, wrong monitor name, permission errors).
    """
    if process.stderr is None:
        return
    try:
        while True:
            line = await process.stderr.readline()
            if not line:
                break
            text = line.decode(errors="ignore").rstrip()
            if not text:
                continue
            process.stderr_lines.append(text)
            log.warning("[ffmpeg:%s] %s", name, text)
    except Exception:  # noqa: BLE001
        pass


async def spawn(cmd: list, name: str = "ffmpeg") -> asyncio.subprocess.Process:
    log.info("Spawning %s: %s", name, " ".join(cmd))
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    process.stderr_lines = collections.deque(maxlen=40)  # type: ignore[attr-defined]
    asyncio.create_task(_drain_stderr(name, process))
    return process


async def ensure_alive(process: asyncio.subprocess.Process, name: str, settle_time: float = 0.6) -> None:
    """
    Gives a freshly spawned ffmpeg process a brief moment to fail fast
    (bad input device, missing PulseAudio, permission errors, etc.) and
    raises a clear error with the actual ffmpeg output if it already
    exited, instead of silently proceeding into a pipe that will never
    receive data.
    """
    await asyncio.sleep(settle_time)
    if process.returncode is not None:
        tail = "\n".join(getattr(process, "stderr_lines", [])) or "(no ffmpeg output captured)"
        raise RuntimeError(
            f"{name} exited immediately with code {process.returncode}.\n{tail}"
        )


async def terminate(process: Optional[asyncio.subprocess.Process]) -> None:
    if process is None or process.returncode is not None:
        return
    try:
        process.terminate()
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
    except ProcessLookupError:
        pass
    
