"""
/speedtest
"""

import asyncio

from pyrogram import Client, filters
from pyrogram.types import Message

from core.logger import get_logger
from core.permissions import owner_only

log = get_logger(__name__)


def _run_speedtest_sync() -> dict:
    import speedtest

    st = speedtest.Speedtest()
    st.get_best_server()
    download_bps = st.download()
    upload_bps = st.upload()
    ping_ms = st.results.ping

    return {
        "ping_ms": ping_ms,
        "download_mbps": download_bps / 1_000_000,
        "upload_mbps": upload_bps / 1_000_000,
        "server": st.results.server.get("sponsor", "Unknown"),
        "isp": st.results.client.get("isp", "Unknown"),
    }


def register(bot: Client) -> None:
    @bot.on_message(filters.command("speedtest") & owner_only)
    async def speedtest_cmd(_, message: Message):
        status = await message.reply_text("📡 Running speed test, this can take a moment...")
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, _run_speedtest_sync)
        except Exception as exc:  # noqa: BLE001
            log.exception("Speed test failed")
            await status.edit_text(f"❌ Speed test failed: `{exc}`")
            return

        text = (
            "📡 **Speed Test Results**\n\n"
            f"**Server:** {result['server']}\n"
            f"**ISP:** {result['isp']}\n"
            f"**Ping:** {result['ping_ms']:.2f} ms\n"
            f"**Download:** {result['download_mbps']:.2f} Mbps\n"
            f"**Upload:** {result['upload_mbps']:.2f} Mbps"
        )
        await status.edit_text(text)
      
