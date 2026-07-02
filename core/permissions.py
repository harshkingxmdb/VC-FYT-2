"""
Access control. Only OWNER_ID may issue commands to the bot; every other
user's commands are silently ignored, as required by the spec.
"""

from pyrogram import filters
from pyrogram.types import Message

import config
from core.logger import get_logger

log = get_logger(__name__)


async def _owner_filter_func(_, __, message: Message) -> bool:
    if message.from_user is None:
        return False
    is_owner = message.from_user.id == config.OWNER_ID
    if not is_owner:
        log.info(
            "Ignored command from non-owner user_id=%s in chat_id=%s",
            message.from_user.id,
            message.chat.id,
        )
    return is_owner


owner_only = filters.create(_owner_filter_func)
