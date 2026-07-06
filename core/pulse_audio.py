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
import os
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


async def pulseaudio_daemon_reachable() -> "tuple[bool, str]":
    """Distinguishes 'pactl not installed' from 'installed but the daemon
    isn't running/reachable for this user' -- the single most common
    reason /join appears to hang or fail with no useful error."""
    _, out, err = await _run("pactl", "info")
    if err:
        return False, err
    return True, out


def _prepare_runtime_environment() -> None:
    """
    PulseAudio needs $XDG_RUNTIME_DIR to create its socket, and refuses
    to run as root unless $PULSE_ALLOW_ROOT is set. Neither exists by
    default on Heroku dynos or fresh VPS boots (there's no login session
    to create them), so this sets both up in the CURRENT process's
    environment before we try to spawn the daemon -- using /tmp instead
    of /run/user/<uid> since /tmp is reliably writable everywhere,
    including restricted containers like Heroku dynos.
    """
    uid = os.getuid()
    if not os.environ.get("XDG_RUNTIME_DIR"):
        runtime_dir = f"/tmp/pulse-runtime-{uid}"
        os.makedirs(runtime_dir, mode=0o700, exist_ok=True)
        os.environ["XDG_RUNTIME_DIR"] = runtime_dir
    if uid == 0:
        os.environ["PULSE_ALLOW_ROOT"] = "1"


_daemon_process: "asyncio.subprocess.Process | None" = None


async def _drain_daemon_stderr(process: "asyncio.subprocess.Process") -> None:
    if process.stderr is None:
        return
    try:
        while True:
            line = await process.stderr.readline()
            if not line:
                break
            text = line.decode(errors="ignore").rstrip()
            if text:
                log.warning("[pulseaudio] %s", text)
    except Exception:  # noqa: BLE001
        pass


async def _start_daemon() -> None:
    global _daemon_process
    _prepare_runtime_environment()
    log.info("Starting PulseAudio daemon automatically (XDG_RUNTIME_DIR=%s)...",
              os.environ.get("XDG_RUNTIME_DIR"))

    if _daemon_process is not None and _daemon_process.returncode is None:
        log.info("A PulseAudio process we started is already running; giving it a moment.")
        await asyncio.sleep(1.5)
        return

    # Deliberately NOT using -D (daemonize): that makes pulseaudio fork
    # and re-exec itself, which sandboxed containers (Heroku dynos, some
    # Docker seccomp profiles) block ("personality() failed: Permission
    # denied", "cannot self execute"). Running it as a plain foreground
    # process that we manage as a background task avoids that entirely.
    _daemon_process = await asyncio.create_subprocess_exec(
        "pulseaudio",
        "--exit-idle-time=-1",
        "--disallow-exit",
        "--disallow-module-loading=no",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    asyncio.create_task(_drain_daemon_stderr(_daemon_process))
    # Give it a moment to create its socket before the next check.
    await asyncio.sleep(1.5)


async def ensure_virtual_sink(sink_name: str = config.PULSE_SINK_NAME) -> str:
    """
    Creates (idempotently) a null-sink named `sink_name` and returns the
    device name of its monitor source, e.g. "vcrelay.monitor".
    """
    if not pulseaudio_available():
        raise RuntimeError(
            "pactl not found on this host. Install PulseAudio with "
            "`apt install pulseaudio pulseaudio-utils` and re-run "
            "./setup_pulseaudio.sh."
        )

    reachable, info_or_error = await pulseaudio_daemon_reachable()
    if not reachable:
        # No daemon running yet (fresh dyno/VPS boot with nothing having
        # started it) -- start it ourselves instead of requiring a human
        # to SSH in and run `pulseaudio --start`, which isn't possible on
        # platforms like Heroku.
        log.warning(
            "PulseAudio daemon not reachable (%s). Attempting to start it "
            "automatically...", info_or_error
        )
        await _start_daemon()
        reachable, info_or_error = await pulseaudio_daemon_reachable()

    if not reachable:
        raise RuntimeError(
            "PulseAudio daemon still isn't reachable after attempting to "
            "start it automatically. Underlying error: "
            f"{info_or_error}"
        )

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
