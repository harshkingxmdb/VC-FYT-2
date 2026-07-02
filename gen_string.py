"""
Run this once locally to generate the STRING_SESSION for the assistant
(userbot) account:

    python3 gen_string.py

It will ask for your API_ID / API_HASH (or read them from .env if
present) and then walk you through Telegram's login flow (phone number,
login code, and 2FA password if enabled). The resulting string session
should be copied into your .env file as STRING_SESSION.

Never share the printed string with anyone -- it grants full access to
the account, just like the account's password.
"""

import asyncio
import os

from dotenv import load_dotenv
from pyrogram import Client

load_dotenv()


async def main() -> None:
    api_id = os.environ.get("API_ID") or input("Enter your API_ID: ").strip()
    api_hash = os.environ.get("API_HASH") or input("Enter your API_HASH: ").strip()

    print("\nLogging in as the assistant (userbot) account...")
    print("You will be asked for your phone number, the login code Telegram")
    print("sends you, and your 2FA password if you have one enabled.\n")

    async with Client(
        "gen_string_session",
        api_id=int(api_id),
        api_hash=api_hash,
        in_memory=True,
    ) as app:
        session_string = await app.export_session_string()

    print("\n" + "=" * 70)
    print("STRING_SESSION generated successfully. Copy the line below into")
    print("your .env file:")
    print("=" * 70)
    print(f"\nSTRING_SESSION={session_string}\n")
    print("=" * 70)
    print("Keep this value secret -- do not commit it or share it publicly.")


if __name__ == "__main__":
    asyncio.run(main())
  
