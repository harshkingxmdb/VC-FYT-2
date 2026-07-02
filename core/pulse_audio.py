"""
PulseAudio bridge.

Telegram voice chats are WebRTC calls: there is no public API that hands
you raw decoded PCM of what a group call is currently playing. The
standard, well documented trick (used by tgcalls' `GroupCallDevice` and
every VC-relay project built on it) is to route the assistant's audio
through real PulseAudio devices:

    1. The assistant joins the LOGGER_GROUP voice chat using a virtual
       PulseAudio *sink* as its playback ("output") device. Whatever is
       spoken there is decoded by tgcalls and physically played into
       that sink.
    2. That sink exposes a *monitor* source, which is just its audio
       flowing the other way. FFmpeg reads from the monitor with the
       `pulse` input device, applies the volume/bass filters, and feeds
       the result into a named pipe.
    3. The assistant joins TARGET_GROUP's voice chat using that named
       pipe as its input stream, so whatever came out of the monitor is
       streamed live into the target call.

This module only owns step 1: creating/destroying the null-sink used as
the bridge. Steps 2-3 live in `ffmpeg_utils.py` and `call_manager.py`.
"""

import asyncio
import shutil

import config
from core.logger import get_logger

log = get_logger(__name__)

MODULE_OWNER_DESCRIPTION = "vc_forward_bot_bridge"


async def _run(*cmd: str) -> "asyncio.subprocess.Process":
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return process, stdout.decode().strip(), stderr.decode().strip()


def pulseaudio_available() -> bool:
    return shutil.which("pactl") is not None


async def ensure_virtual_sink(sink_name: str = config.PULSE_SINK_NAME) -> str:
    """
    Creates (idempotently) a null-sink named `sink_name` and returns the
    device name of its monitor source, e.g. "vcrelay.monitor".
    """
    if not pulseaudio_available():
        log.warning(
            "pactl not found on this host. Install PulseAudio "
            "(apt install pulseaudio pulseaudio-utils) for VC-to-VC "
            "forwarding to work."
        )
        return f"{sink_name}.monitor"

    _, existing_sinks, _ = await _run("pactl", "list", "short", "sinks")
    if not any(line.split("\t")[1] == sink_name for line in existing_sinks.splitlines() if line):
        _, out, err = await _run(
            "pactl",
            "load-module",
            "module-null-sink",
            f"sink_name={sink_name}",
            f"sink_properties=device.description={MODULE_OWNER_DESCRIPTION}",
        )
        if err:
            log.error("Failed to create PulseAudio sink '%s': %s", sink_name, err)
        else:
            log.info("Created PulseAudio virtual sink '%s' (module id=%s).", sink_name, out)
    else:
        log.info("PulseAudio virtual sink '%s' already exists.", sink_name)

    return f"{sink_name}.monitor"


async def teardown_virtual_sink(sink_name: str = config.PULSE_SINK_NAME) -> None:
    if not pulseaudio_available():
        return
    _, modules, _ = await _run("pactl", "list", "short", "modules")
    for line in modules.splitlines():
        if not line:
            continue
        parts = line.split("\t")
        module_id, module_name, args = parts[0], parts[1], parts[2] if len(parts) > 2 else ""
        if module_name == "module-null-sink" and f"sink_name={sink_name}" in args:
            await _run("pactl", "unload-module", module_id)
            log.info("Unloaded PulseAudio sink '%s' (module id=%s).", sink_name, module_id)
      
