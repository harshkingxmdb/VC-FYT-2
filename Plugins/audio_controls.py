"""
/level, /bass, /mute, /unmute
"""

from __future__ import annotations

from pyrogram import Client, filters
from pyrogram.types import Message

import config
from core.call_manager import CallManager
from core.logger import get_logger
from core.permissions import owner_only

log = get_logger(__name__)


def _resolve_single_session_chat_id(call_manager: CallManager) -> int | None:
    if len(call_manager.sessions) == 1:
        return next(iter(call_manager.sessions.keys()))
    return None


def register(bot: Client, call_manager: CallManager) -> None:
    @bot.on_message(filters.command("level") & owner_only)
    async def level_cmd(_, message: Message):
        parts = message.text.split()
        if len(parts) < 2:
            await message.reply_text(f"Usage: `/level <{config.MIN_LEVEL}-{config.MAX_LEVEL}>`")
            return
        try:
            level = int(parts[1])
        except ValueError:
            await message.reply_text("Level must be an integer.")
            return
        if not (config.MIN_LEVEL <= level <= config.MAX_LEVEL):
            await message.reply_text(
                f"Level must be between {config.MIN_LEVEL} and {config.MAX_LEVEL}."
            )
            return

        chat_id = _resolve_single_session_chat_id(call_manager)
        if chat_id is None:
            await message.reply_text("No single active session found. Use `/join <chat_id>` first.")
            return

        session = await call_manager.set_level(chat_id, level)
        if session is None:
            await message.reply_text("No active session to update.")
            return
        await message.reply_text(f"🔊 Volume level set to {level}/{config.MAX_LEVEL}.")

    @bot.on_message(filters.command("bass") & owner_only)
    async def bass_cmd(_, message: Message):
        parts = message.text.split()
        if len(parts) < 2:
            await message.reply_text(f"Usage: `/bass <{config.MIN_BASS}-{config.MAX_BASS}>`")
            return
        try:
            bass = int(parts[1])
        except ValueError:
            await message.reply_text("Bass must be an integer.")
            return
        if not (config.MIN_BASS <= bass <= config.MAX_BASS):
            await message.reply_text(
                f"Bass must be between {config.MIN_BASS} and {config.MAX_BASS}."
            )
            return

        chat_id = _resolve_single_session_chat_id(call_manager)
        if chat_id is None:
            await message.reply_text("No single active session found. Use `/join <chat_id>` first.")
            return

        session = await call_manager.set_bass(chat_id, bass)
        if session is None:
            await message.reply_text("No active session to update.")
            return
        await message.reply_text(f"🎚 Bass level set to {bass}/{config.MAX_BASS}.")

    @bot.on_message(filters.command("mute") & owner_only)
    async def mute_cmd(_, message: Message):
        chat_id = _resolve_single_session_chat_id(call_manager)
        if chat_id is None:
            await message.reply_text("No single active session found.")
            return
        await call_manager.set_mute(chat_id, True)
        await message.reply_text("🔇 Muted playback.")

    @bot.on_message(filters.command("unmute") & owner_only)
    async def unmute_cmd(_, message: Message):
        chat_id = _resolve_single_session_chat_id(call_manager)
        if chat_id is None:
            await message.reply_text("No single active session found.")
            return
        await call_manager.set_mute(chat_id, False)
        await message.reply_text("🔊 Unmuted playback.")
          
