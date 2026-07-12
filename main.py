"""
Core startup/shutdown logic for the Telegram VC-to-VC Audio Forwarding
Userbot.

Two Telegram clients are used:

  * `bot`       - a normal Bot API client (BOT_TOKEN) that the owner
                  talks to; it only relays commands and never joins any
                  Voice Chat itself.
  * `assistant` - a user client (STRING_SESSION) that actually joins the
                  Logger Group's and the target group's Voice Chats and
                  streams audio between them via PyTgCalls.

Recommended way to run the bot:  python3 start.py
(start.py prints the startup banner, runs pre-flight checks, then calls
main() below.) Running `python3 main.py` directly still works too.
"""

import asyncio
import contextlib
import signal

from pyrogram import Client

import config
from core.audio_server import AudioHTTPBridge
from core.call_manager import CallManager
from core.db import db
from plugins import audio_controls, join_leave, recording, screenshare, utility
from core.logger import get_logger
from validate_startup import run_checks

log = get_logger(__name__)


async def _resume_previous_sessions(call_manager: CallManager) -> None:
    """Automatic Voice Chat recovery after a full process restart: any
    session that was active in MongoDB before the last shutdown/crash is
    rejoined automatically."""
    sessions = await db.get_all_sessions()
    if not sessions:
        return
    log.info("Resuming %d session(s) found in MongoDB from a previous run.", len(sessions))
    for doc in sessions:
        chat_id = doc["chat_id"]
        try:
            await call_manager.join(chat_id)
            log.info("Resumed session for chat_id=%s.", chat_id)
        except Exception:  # noqa: BLE001
            log.exception("Failed to resume session for chat_id=%s.", chat_id)


async def _hydrate_peer_cache(assistant: Client) -> None:
    """
    Pyrogram (and pytgcalls, which reuses the same session) can only
    operate on a chat once it has that chat's access_hash cached, which
    only happens after the client has "seen" it via get_chat/get_dialogs
    or an incoming update. Walking the dialog list once at startup
    pre-caches every chat the assistant is currently a member of, so
    /join doesn't fail with a cryptic "Peer id invalid" the first time
    it's used against a chat the assistant hasn't interacted with yet.
    """
    count = 0
    try:
        async for _dialog in assistant.get_dialogs():
            count += 1
    except Exception:  # noqa: BLE001
        log.exception("Failed to hydrate assistant peer cache from dialogs.")
        return
    log.info("Hydrated assistant peer cache from %d dialog(s).", count)


async def main() -> None:
    log.info("Starting VC-to-VC Audio Forwarding Userbot...")

    run_checks()
    await db.ensure_indexes()

    bot = Client(
        config.SESSION_NAME_BOT,
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        bot_token=config.BOT_TOKEN,
        in_memory=True,
    )
    assistant = Client(
        config.SESSION_NAME_ASSISTANT,
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        session_string=config.STRING_SESSION,
        in_memory=True,
    )

    await bot.start()
    await assistant.start()
    log.info("Bot and assistant Telegram clients started.")

    await _hydrate_peer_cache(assistant)

    audio_bridge = AudioHTTPBridge()
    await audio_bridge.start()

    call_manager = CallManager(assistant, audio_bridge)
    await call_manager.start()

    join_leave.register(bot, call_manager)
    audio_controls.register(bot, call_manager)
    screenshare.register(bot, call_manager)
    recording.register(bot, call_manager)
    utility.register(bot)

    await _resume_previous_sessions(call_manager)

    try:
        me = await bot.get_me()
        await bot.send_message(
            config.OWNER_ID,
            f"✅ **{me.first_name}** is online and ready.\n"
            f"Logger Group: `{config.LOGGER_GROUP}`\n"
            f"Use `/join <chat_id>` to start forwarding.",
        )
    except Exception:  # noqa: BLE001
        log.warning("Could not send startup notification to OWNER_ID.")

    log.info("Startup complete. Listening for owner commands.")

    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        log.info("Shutdown signal received.")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _signal_handler)

    await stop_event.wait()

    log.info("Shutting down...")
    with contextlib.suppress(Exception):
        await bot.send_message(config.OWNER_ID, "🛑 Bot is shutting down.")

    await call_manager.stop()
    await audio_bridge.stop()
    await assistant.stop()
    await bot.stop()
    log.info("Shutdown complete.")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
