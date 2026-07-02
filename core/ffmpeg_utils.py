"""
FFmpeg helpers: building the `-af` filter graph for volume/bass and
spawning the capture process that bridges the PulseAudio monitor into
the named pipe consumed by the outgoing voice chat stream.
"""

import asyncio
from typing import Optional

import config
from core.logger import get_logger

log = get_logger(__name__)

SAMPLE_RATE = 48000
CHANNELS = 2


def build_audio_filters(level: int, bass: int, muted: bool = False) -> str:
    """
    Translates the user-facing /level (1-25) and /bass (0-15) scales into
    an FFmpeg `-af` filter chain.

    level 1-25  -> volume multiplier 0.2x - 5.0x (level / 5)
    bass  0-15  -> low-shelf boost of 0-30 dB centered at 110 Hz
    """
    if muted:
        return "volume=0"

    level = max(config.MIN_LEVEL, min(config.MAX_LEVEL, level))
    bass = max(config.MIN_BASS, min(config.MAX_BASS, bass))

    volume_multiplier = round(level / 5, 3)
    bass_gain_db = bass * 2

    filters = [f"volume={volume_multiplier}"]
    if bass_gain_db > 0:
        filters.append(f"bass=g={bass_gain_db}:f=110:w=0.6")
    # Keep the signal from clipping after volume/bass boosts.
    filters.append("alimiter=limit=0.95")

    return ",".join(filters)


def build_capture_command(
    monitor_source: str,
    output_pipe_path: str,
    level: int,
    bass: int,
    muted: bool = False,
) -> list:
    """
    ffmpeg command that reads the PulseAudio monitor of the bridge sink,
    applies volume/bass filters, and writes raw PCM into the FIFO used as
    input for the outgoing voice chat stream.
    """
    filters = build_audio_filters(level, bass, muted)
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-f",
        "pulse",
        "-i",
        monitor_source,
        "-af",
        filters,
        "-ac",
        str(CHANNELS),
        "-ar",
        str(SAMPLE_RATE),
        "-f",
        "wav",
        "-y",
        output_pipe_path,
    ]


def build_silence_command(output_pipe_path: str) -> list:
    """
    The assistant must still send *something* while listening in the
    Logger Group's voice chat. We feed silence so the call stays open
    without echoing anything back into that chat.
    """
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-f",
        "wav",
        "-y",
        output_pipe_path,
    ]


def build_record_command(monitor_source: str, output_file_path: str) -> list:
    """
    Records the forwarded (post-filter) audio to a compressed file for
    /startrecord ... /stoprecord.
    """
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-f",
        "pulse",
        "-i",
        monitor_source,
        "-ac",
        str(CHANNELS),
        "-ar",
        str(SAMPLE_RATE),
        "-b:a",
        "192k",
        "-y",
        output_file_path,
    ]


async def spawn(cmd: list) -> asyncio.subprocess.Process:
    log.info("Spawning ffmpeg process: %s", " ".join(cmd))
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    return process


async def terminate(process: Optional[asyncio.subprocess.Process]) -> None:
    if process is None or process.returncode is not None:
        return
    try:
        process.terminate()
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
    except ProcessLookupError:
        pass
  
