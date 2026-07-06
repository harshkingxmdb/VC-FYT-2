"""
plugins - every owner-only Telegram command lives here, one module per
command group. Each module exposes a single `register(bot, call_manager)`
function that main.py calls once at startup to attach its handlers to
the Bot API client.

    join_leave        /join /leave /leaveall /leaveplay /leaverecord
    audio_controls     /level /bass /mute /unmute
    screenshare        /screenshare /screenshareoff
    recording          /startrecord /stoprecord
    utility            /speedtest
"""

from plugins import audio_controls, join_leave, recording, screenshare, utility

__all__ = [
    "join_leave",
    "audio_controls",
    "screenshare",
    "recording",
    "utility",
]
