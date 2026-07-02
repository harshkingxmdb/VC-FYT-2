"""
/startrecord, /stoprecord
"""

import os

from pyrogram import Client, filters
from pyrogram.types import Message

from core.call_manager import CallManager
from core.logger import get_logger
from core.permissions import owner_only

log = get_logger(__name__)


def register(bot: Client, call_manager: CallManager) -> None:
    @bot.on_message(filters.command("startrecord") & owner_only)
    async def startrecord_cmd(_, message: Message):
        if len(call_manager.sessions) != 1:
            await message.reply_text("No single active session found. Join a Voice Chat first.")
            return
        chat_id = next(iter(call_manager.sessions.keys()))

        path = await call_manager.start_recording(chat_id)
        if path is None:
            await message.reply_text("A recording is already in progress, or no session is active.")
            return
        log.info("Recording session started by owner for chat_id=%s -> %s", chat_id, path)
        await message.reply_text(f"🔴 Recording started: `{os.path.basename(path)}`")

    @bot.on_message(filters.command("stoprecord") & owner_only)
    async def stoprecord_cmd(_, message: Message):
        if len(call_manager.sessions) != 1:
            await message.reply_text("No single active session found.")
            return
        chat_id = next(iter(call_manager.sessions.keys()))

        status = await message.reply_text("⏹ Stopping recording and uploading...")
        path = await call_manager.stop_recording(chat_id)
        if path is None or not os.path.exists(path):
            await status.edit_text("No recording was in progress.")
            return

        try:
            await bot.send_audio(
                message.chat.id,
                path,
                caption=f"Recorded forwarded audio for chat `{chat_id}`.",
            )
            log.info("Recording uploaded for chat_id=%s -> %s", chat_id, path)
            await status.edit_text("✅ Recording stopped and uploaded.")
        except Exception as exc:  # noqa: BLE001
            log.exception("Failed to upload recording %s", path)
            await status.edit_text(f"❌ Recording saved locally but upload failed: `{exc}`")
  
