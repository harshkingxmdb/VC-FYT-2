"""
MongoDB persistence layer.

Two collections are used:

    sessions  -> one document per active forwarding session
                 { chat_id, logger_group, joined_at, status }

    settings  -> one document per chat_id holding the audio configuration
                 { chat_id, level, bass, muted, screenshare }

All access goes through the async `Database` class below, backed by
Motor. The module exposes a single lazily-initialized `db` instance
that the rest of the application imports.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorClient

import config
from core.logger import get_logger

log = get_logger(__name__)


class Database:
    def __init__(self, mongo_uri: str, db_name: str):
        self._client = AsyncIOMotorClient(mongo_uri)
        self._db = self._client[db_name]
        self.sessions = self._db["sessions"]
        self.settings = self._db["settings"]

    async def ensure_indexes(self) -> None:
        await self.sessions.create_index("chat_id", unique=True)
        await self.settings.create_index("chat_id", unique=True)
        log.info("MongoDB indexes ensured on 'sessions' and 'settings'.")

    # ------------------------------------------------------------------ #
    # Sessions
    # ------------------------------------------------------------------ #
    async def add_session(self, chat_id: int, logger_group: int) -> None:
        await self.sessions.update_one(
            {"chat_id": chat_id},
            {
                "$set": {
                    "chat_id": chat_id,
                    "logger_group": logger_group,
                    "status": "active",
                    "joined_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )

    async def remove_session(self, chat_id: int) -> None:
        await self.sessions.delete_one({"chat_id": chat_id})

    async def get_session(self, chat_id: int) -> Optional[Dict[str, Any]]:
        return await self.sessions.find_one({"chat_id": chat_id})

    async def get_all_sessions(self) -> List[Dict[str, Any]]:
        return await self.sessions.find().to_list(length=None)

    async def clear_all_sessions(self) -> int:
        result = await self.sessions.delete_many({})
        return result.deleted_count

    # ------------------------------------------------------------------ #
    # Settings (per playback chat)
    # ------------------------------------------------------------------ #
    async def get_settings(self, chat_id: int) -> Dict[str, Any]:
        doc = await self.settings.find_one({"chat_id": chat_id})
        if doc is None:
            doc = {
                "chat_id": chat_id,
                "level": config.DEFAULT_LEVEL,
                "bass": config.DEFAULT_BASS,
                "muted": False,
                "screenshare": False,
            }
            await self.settings.update_one(
                {"chat_id": chat_id}, {"$set": doc}, upsert=True
            )
        return doc

    async def update_settings(self, chat_id: int, **fields: Any) -> None:
        fields["chat_id"] = chat_id
        await self.settings.update_one(
            {"chat_id": chat_id}, {"$set": fields}, upsert=True
        )


db = Database(config.MONGO_URI, config.DB_NAME)
      
