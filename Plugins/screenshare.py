"""
/screenshare, /screenshareoff
"""

from pyrogram import Client, filters
from pyrogram.types import Message

from core.call_manager import CallManager
from core.logger import get_logger
from core.permissions import owner_only

log = get_logger(__name__)


def register(bot: Client, call_manager: CallManager) -> None:
    @bot.on_message(filters.command("screenshare") & owner_only)
    async def screenshare_cmd(_, message: Message):
        if len(call_manager.sessions) != 1:
            await message.reply_text(
                "Screen share requires exactly one active session. "
                "Join a Voice Chat first with `/join <chat_id>`."
            )
            return
        chat_id = next(iter(call_manager.sessions.keys()))

        status = await message.reply_text("🖥 Starting screen share...")
        try:
            started = await call_manager.start_screenshare(chat_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("Failed to start screen share for chat_id=%s", chat_id)
            await status.edit_text(f"❌ Failed to start screen share: `{exc}`")
            return

        if started:
            await status.edit_text("✅ Screen share started.")
        else:
            await status.edit_text("Screen share is already running or no session is active.")

    @bot.on_message(filters.command("screenshareoff") & owner_only)
    async def screenshare_off_cmd(_, message: Message):
        if len(call_manager.sessions) != 1:
            await message.reply_text("No single active session found.")
            return
        chat_id = next(iter(call_manager.sessions.keys()))

        stopped = await call_manager.stop_screenshare(chat_id)
        if stopped:
            await message.reply_text("🛑 Screen share stopped.")
        else:
            await message.reply_text("Screen share was not running.")
          
