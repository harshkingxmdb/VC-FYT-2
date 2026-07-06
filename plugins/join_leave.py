"""
/join, /leave, /leaveall, /leaveplay, /leaverecord
"""

from pyrogram import Client, filters
from pyrogram.types import Message

from core.call_manager import CallManager
from core.db import db
from core.logger import get_logger
from core.permissions import owner_only

log = get_logger(__name__)


def register(bot: Client, call_manager: CallManager) -> None:
    @bot.on_message(filters.command("join") & owner_only)
    async def join_cmd(_, message: Message):
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            await message.reply_text(
                "Usage: `/join <chat_id>`\n\n"
                "Joins that chat's Voice Chat and starts forwarding live "
                "audio from the Logger Group's Voice Chat."
            )
            return

        try:
            target_chat_id = int(parts[1].strip())
        except ValueError:
            await message.reply_text("chat_id must be a valid integer, e.g. -1001234567890.")
            return

        status = await message.reply_text(f"Joining Voice Chat `{target_chat_id}`...")
        try:
            await call_manager.join(target_chat_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("Failed to join chat_id=%s", target_chat_id)
            await status.edit_text(f"❌ Failed to join: `{exc}`")
            return

        await status.edit_text(
            f"✅ Joined Voice Chat `{target_chat_id}`.\n"
            f"Now forwarding live audio from Logger Group Voice Chat."
        )

    @bot.on_message(filters.command("leave") & owner_only)
    async def leave_cmd(_, message: Message):
        parts = message.text.split(maxsplit=1)
        target_chat_id = None
        if len(parts) >= 2:
            try:
                target_chat_id = int(parts[1].strip())
            except ValueError:
                await message.reply_text("chat_id must be a valid integer.")
                return
        elif len(call_manager.sessions) == 1:
            target_chat_id = next(iter(call_manager.sessions.keys()))

        if target_chat_id is None:
            await message.reply_text(
                "Multiple sessions are active. Usage: `/leave <chat_id>` "
                "or use `/leaveall` to leave every session."
            )
            return

        left = await call_manager.leave(target_chat_id)
        if left:
            await message.reply_text(f"👋 Left both Voice Chats for `{target_chat_id}`.")
        else:
            await message.reply_text(f"No active session found for `{target_chat_id}`.")

    @bot.on_message(filters.command("leaveall") & owner_only)
    async def leaveall_cmd(_, message: Message):
        count = await call_manager.leave_all()
        await db.clear_all_sessions()
        await message.reply_text(f"👋 Left all active Voice Chat sessions ({count} total).")

    @bot.on_message(filters.command("leaveplay") & owner_only)
    async def leaveplay_cmd(_, message: Message):
        parts = message.text.split(maxsplit=1)
        target_chat_id = None
        if len(parts) >= 2:
            try:
                target_chat_id = int(parts[1].strip())
            except ValueError:
                await message.reply_text("chat_id must be a valid integer.")
                return
        elif len(call_manager.sessions) == 1:
            target_chat_id = next(iter(call_manager.sessions.keys()))

        if target_chat_id is None:
            await message.reply_text(
                "Multiple sessions are active. Usage: `/leaveplay <chat_id>`."
            )
            return

        left = await call_manager.leave_playback_only(target_chat_id)
        if left:
            await message.reply_text(f"👋 Left only the playback Voice Chat (`{target_chat_id}`).")
        else:
            await message.reply_text(f"No active session found for `{target_chat_id}`.")

    @bot.on_message(filters.command("leaverecord") & owner_only)
    async def leaverecord_cmd(_, message: Message):
        await call_manager.leave_record_only()
        await message.reply_text("👋 Left the Logger Group Voice Chat.")
      
