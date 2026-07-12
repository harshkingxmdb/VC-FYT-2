import asyncio
from dataclasses import dataclass, field
from typing import Dict, Optional

from pyrogram import Client
from pyrogram.errors import FloodWait
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream, AudioQuality
from pytgcalls.exceptions import NoActiveGroupCall, NotInCallError

import config
from core import ffmpeg_utils, pipe_manager, pulse_audio
from core.audio_server import AudioHTTPBridge
from core.db import db
from core.logger import get_logger

log = get_logger(__name__)

RECONNECT_BASE_DELAY = 3
RECONNECT_MAX_DELAY = 60
WATCHDOG_INTERVAL = 10
STREAM_SETTLE_TIME = 0.6


@dataclass
class ForwardSession:
    target_chat_id: int
    logger_chat_id: int
    level: int = config.DEFAULT_LEVEL
    bass: int = config.DEFAULT_BASS
    muted: bool = False
    silence_key: str = ""
    capture_key: str = ""
    screenshare_process: Optional[asyncio.subprocess.Process] = None
    recording_process: Optional[asyncio.subprocess.Process] = None
    recording_path: Optional[str] = None
    watchdog_task: Optional[asyncio.Task] = None
    connected: bool = False
    monitor_source: str = ""
    stopping: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class CallManager:
    def __init__(self, assistant: Client, audio_bridge: AudioHTTPBridge):
        self.assistant = assistant
        self.pytgcalls = PyTgCalls(assistant)
        self.audio_bridge = audio_bridge
        self.sessions: Dict[int, ForwardSession] = {}
        self._bridge_ready = False
        self._register_update_handlers()

    async def start(self) -> None:
        await self.pytgcalls.start()
        log.info("PyTgCalls client started for the assistant account.")

    async def stop(self) -> None:
        for chat_id in list(self.sessions.keys()):
            await self.leave(chat_id)
        await pulse_audio.teardown_virtual_sink()
        log.info("CallManager shut down cleanly.")

    async def _ensure_bridge(self) -> str:
        if not self._bridge_ready:
            self._monitor_source = await pulse_audio.ensure_virtual_sink()
            self._bridge_ready = True
        return self._monitor_source

    def _register_update_handlers(self) -> None:
        for handler_name, callback in (
            ("on_kicked", self._on_kicked),
            ("on_left", self._on_left),
            ("on_closed_voice_chat", self._on_closed),
        ):
            decorator = getattr(self.pytgcalls, handler_name, None)
            if decorator is None:
                continue
            decorator()(callback)

    async def _on_kicked(self, _client, chat_id: int) -> None:
        log.warning("Assistant was kicked from voice chat in chat_id=%s", chat_id)
        await self._handle_disconnect(chat_id)

    async def _on_left(self, _client, chat_id: int) -> None:
        log.warning("Assistant left voice chat in chat_id=%s", chat_id)
        await self._handle_disconnect(chat_id)

    async def _on_closed(self, _client, chat_id: int) -> None:
        log.warning("Voice chat was closed in chat_id=%s", chat_id)
        await self._handle_disconnect(chat_id)

    async def _handle_disconnect(self, chat_id: int) -> None:
        for session in self.sessions.values():
            if chat_id in (session.target_chat_id, session.logger_chat_id):
                session.connected = False

    async def _resolve_peer(self, chat_id: int, label: str) -> None:
        try:
            await self.assistant.get_chat(chat_id)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Could not resolve {label} (chat_id={chat_id}) with the "
                f"assistant account. Make sure the assistant account "
                f"(STRING_SESSION) is a member of that chat, and that the "
                f"ID is correct (it should start with -100 for a "
                f"supergroup/channel). Underlying error: {exc}"
            ) from exc

    async def join(self, target_chat_id: int) -> ForwardSession:
        if target_chat_id in self.sessions:
            log.info("Session for chat_id=%s already active; re-using it.", target_chat_id)
            return self.sessions[target_chat_id]

        await self._resolve_peer(target_chat_id, "target chat")
        await self._resolve_peer(config.LOGGER_GROUP, "Logger Group")

        monitor_source = await self._ensure_bridge()
        settings = await db.get_settings(target_chat_id)

        session = ForwardSession(
            target_chat_id=target_chat_id,
            logger_chat_id=config.LOGGER_GROUP,
            level=settings.get("level", config.DEFAULT_LEVEL),
            bass=settings.get("bass", config.DEFAULT_BASS),
            muted=settings.get("muted", False),
            monitor_source=monitor_source,
            silence_key=f"{target_chat_id}_in",
            capture_key=f"{target_chat_id}_out",
        )
        self.sessions[target_chat_id] = session

        await self._connect_session(session)
        await db.add_session(target_chat_id, config.LOGGER_GROUP)

        session.watchdog_task = asyncio.create_task(self._watchdog(session))
        log.info(
            "Join complete: forwarding Logger Group %s -> Target chat %s",
            config.LOGGER_GROUP,
            target_chat_id,
        )
        return session

    async def _connect_session(self, session: ForwardSession) -> None:
        async with session.lock:
            silence_url = await self.audio_bridge.register_stream(
                session.silence_key,
                ffmpeg_utils.build_silence_command_stdout(),
                name="silence-feed",
            )
            await asyncio.sleep(STREAM_SETTLE_TIME)
            if not self.audio_bridge.is_stream_alive(session.silence_key):
                raise RuntimeError("silence-feed ffmpeg exited immediately after starting.")

            try:
                await self.pytgcalls.play(
                    session.logger_chat_id,
                    MediaStream(
                        silence_url,
                        AudioQuality.STUDIO,
                        video_flags=MediaStream.Flags.IGNORE,
                    ),
                )
                log.info("Joined Logger Group voice chat (chat_id=%s).", session.logger_chat_id)
            except NoActiveGroupCall:
                log.error(
                    "No active voice chat in Logger Group (chat_id=%s). "
                    "Start the voice chat there first.",
                    session.logger_chat_id,
                )
                raise

            capture_url = await self.audio_bridge.register_stream(
                session.capture_key,
                ffmpeg_utils.build_capture_command_stdout(
                    session.monitor_source, session.level, session.bass, session.muted
                ),
                name="audio-capture",
            )
            await asyncio.sleep(STREAM_SETTLE_TIME)
            if not self.audio_bridge.is_stream_alive(session.capture_key):
                raise RuntimeError("audio-capture ffmpeg exited immediately after starting.")

            try:
                await self.pytgcalls.play(
                    session.target_chat_id,
                    MediaStream(
                        capture_url,
                        AudioQuality.STUDIO,
                        video_flags=MediaStream.Flags.IGNORE,
                    ),
                )
                log.info("Joined target voice chat (chat_id=%s).", session.target_chat_id)
            except NoActiveGroupCall:
                log.error(
                    "No active voice chat in target chat_id=%s. Start the "
                    "voice chat there first.",
                    session.target_chat_id,
                )
                raise

            session.connected = True

    async def leave(self, target_chat_id: int, *, keep_logger: bool = False) -> bool:
        session = self.sessions.get(target_chat_id)
        if session is None:
            return False

        session.stopping = True
        if session.watchdog_task:
            session.watchdog_task.cancel()

        async with session.lock:
            try:
                await self.pytgcalls.leave_call(session.target_chat_id)
            except (NotInCallError, Exception) as exc:  # noqa: BLE001
                log.debug("leave_call(target) raised %s (already left?).", exc)

            if not keep_logger and not self._logger_still_needed(target_chat_id):
                try:
                    await self.pytgcalls.leave_call(session.logger_chat_id)
                except (NotInCallError, Exception) as exc:  # noqa: BLE001
                    log.debug("leave_call(logger) raised %s (already left?).", exc)

            await self.audio_bridge.remove_stream(session.capture_key)
            await self.audio_bridge.remove_stream(session.silence_key)
            await ffmpeg_utils.terminate(session.screenshare_process)
            await ffmpeg_utils.terminate(session.recording_process)
            pipe_manager.cleanup_pipe(target_chat_id, "screen")

        del self.sessions[target_chat_id]
        await db.remove_session(target_chat_id)
        log.info("Left voice chat(s) for target chat_id=%s.", target_chat_id)
        return True

    def _logger_still_needed(self, excluding_chat_id: int) -> bool:
        return any(
            chat_id != excluding_chat_id for chat_id in self.sessions.keys()
        )

    async def leave_playback_only(self, target_chat_id: int) -> bool:
        return await self.leave(target_chat_id, keep_logger=True)

    async def leave_record_only(self) -> int:
        count = 0
        try:
            await self.pytgcalls.leave_call(config.LOGGER_GROUP)
            count = 1
        except Exception as exc:  # noqa: BLE001
            log.debug("leave_call(logger, global) raised %s", exc)
        for session in self.sessions.values():
            await self.audio_bridge.remove_stream(session.silence_key)
        return count

    async def leave_all(self) -> int:
        chat_ids = list(self.sessions.keys())
        for chat_id in chat_ids:
            await self.leave(chat_id)
        return len(chat_ids)

    async def _watchdog(self, session: ForwardSession) -> None:
        delay = RECONNECT_BASE_DELAY
        while not session.stopping:
            await asyncio.sleep(WATCHDOG_INTERVAL)
            if session.stopping:
                return

            healthy = (
                session.connected
                and self.audio_bridge.is_stream_alive(session.capture_key)
                and self.audio_bridge.is_stream_alive(session.silence_key)
            )
            if healthy:
                delay = RECONNECT_BASE_DELAY
                continue

            log.warning(
                "Session for chat_id=%s appears unhealthy, attempting "
                "automatic voice-chat recovery (retry in %ss).",
                session.target_chat_id,
                delay,
            )
            try:
                await self._reconnect(session)
                delay = RECONNECT_BASE_DELAY
            except FloodWait as exc:
                wait_seconds = exc.value + 2
                log.warning(
                    "Telegram FloodWait while reconnecting chat_id=%s: "
                    "waiting %ss as instructed before trying again.",
                    session.target_chat_id,
                    wait_seconds,
                )
                await asyncio.sleep(wait_seconds)
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "Reconnect attempt failed for chat_id=%s: %s",
                    session.target_chat_id,
                    exc,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX_DELAY)

    async def _reconnect(self, session: ForwardSession) -> None:
        async with session.lock:
            await self.audio_bridge.remove_stream(session.capture_key)
            await self.audio_bridge.remove_stream(session.silence_key)
            for chat_id in (session.logger_chat_id, session.target_chat_id):
                try:
                    await self.pytgcalls.leave_call(chat_id)
                except Exception:  # noqa: BLE001
                    pass
        await self._connect_session(session)
        log.info("Session for chat_id=%s recovered successfully.", session.target_chat_id)

    async def set_level(self, target_chat_id: int, level: int) -> Optional[ForwardSession]:
        session = self.sessions.get(target_chat_id)
        if session is None:
            return None
        session.level = level
        await db.update_settings(target_chat_id, level=level)
        await self._restart_capture(session)
        return session

    async def set_bass(self, target_chat_id: int, bass: int) -> Optional[ForwardSession]:
        session = self.sessions.get(target_chat_id)
        if session is None:
            return None
        session.bass = bass
        await db.update_settings(target_chat_id, bass=bass)
        await self._restart_capture(session)
        return session

    async def set_mute(self, target_chat_id: int, muted: bool) -> Optional[ForwardSession]:
        session = self.sessions.get(target_chat_id)
        if session is None:
            return None
        session.muted = muted
        await db.update_settings(target_chat_id, muted=muted)
        await self._restart_capture(session)
        return session

    async def _restart_capture(self, session: ForwardSession) -> None:
        async with session.lock:
            await self.audio_bridge.register_stream(
                session.capture_key,
                ffmpeg_utils.build_capture_command_stdout(
                    session.monitor_source,
                    session.level,
                    session.bass,
                    session.muted,
                ),
                name="audio-capture",
            )
            await asyncio.sleep(STREAM_SETTLE_TIME)
            if not self.audio_bridge.is_stream_alive(session.capture_key):
                raise RuntimeError("audio-capture ffmpeg exited immediately after restart.")
        log.info(
            "Applied audio settings for chat_id=%s: level=%s bass=%s muted=%s",
            session.target_chat_id,
            session.level,
            session.bass,
            session.muted,
        )

    async def start_screenshare(self, target_chat_id: int) -> bool:
        session = self.sessions.get(target_chat_id)
        if session is None or session.screenshare_process is not None:
            return False

        screen_pipe = pipe_manager.create_pipe(target_chat_id, "screen")
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "x11grab",
            "-framerate",
            "30",
            "-i",
            config.SCREEN_SHARE_DEVICE,
            "-f",
            "matroska",
            "-vcodec",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-y",
            screen_pipe,
        ]
        session.screenshare_process = await ffmpeg_utils.spawn(cmd)

        from pytgcalls.types import VideoQuality

        await self.pytgcalls.play(
            target_chat_id,
            MediaStream(
                screen_pipe,
                AudioQuality.STUDIO,
                VideoQuality.FHD_1080p,
            ),
        )
        log.info("Screen share started for chat_id=%s.", target_chat_id)
        return True

    async def stop_screenshare(self, target_chat_id: int) -> bool:
        session = self.sessions.get(target_chat_id)
        if session is None or session.screenshare_process is None:
            return False

        await ffmpeg_utils.terminate(session.screenshare_process)
        session.screenshare_process = None
        pipe_manager.cleanup_pipe(target_chat_id, "screen")

        capture_url = self.audio_bridge.url_for(session.capture_key)
        await self.pytgcalls.play(
            target_chat_id,
            MediaStream(
                capture_url,
                AudioQuality.STUDIO,
                video_flags=MediaStream.Flags.IGNORE,
            ),
        )
        log.info("Screen share stopped for chat_id=%s.", target_chat_id)
        return True

    async def start_recording(self, target_chat_id: int) -> Optional[str]:
        session = self.sessions.get(target_chat_id)
        if session is None or session.recording_process is not None:
            return None

        import os
        import time

        filename = f"vc_recording_{target_chat_id}_{int(time.time())}.mp3"
        output_path = os.path.join(config.RECORDINGS_DIR, filename)

        session.recording_process = await ffmpeg_utils.spawn(
            ffmpeg_utils.build_record_command(session.monitor_source, output_path)
        )
        session.recording_path = output_path
        log.info("Recording started for chat_id=%s -> %s", target_chat_id, output_path)
        return output_path

    async def stop_recording(self, target_chat_id: int) -> Optional[str]:
        session = self.sessions.get(target_chat_id)
        if session is None or session.recording_process is None:
            return None

        await ffmpeg_utils.terminate(session.recording_process)
        session.recording_process = None
        path = session.recording_path
        session.recording_path = None
        log.info("Recording stopped for chat_id=%s -> %s", target_chat_id, path)
        return path