"""
start.py - primary entry point for VCFight.

This is the file you actually run (and the one Procfile / Dockerfile /
systemd point at). It prints a startup banner, runs the pre-flight
checks, then hands off to main.main() which does the real work (starts
the bot + assistant clients, registers plugins, joins any sessions that
were active before a restart, and idles until shutdown).

    python3 start.py
"""

import os


def _fix_apt_library_paths() -> None:
    """
    On Heroku, apt-buildpack-installed packages (ffmpeg, pulseaudio, and
    all their transitive dependencies -- libblas, libprotocol-native,
    libpulsecommon, etc.) live under /app/.apt/... instead of the
    standard system library paths the dynamic linker searches by
    default. Rather than discover and patch each missing library one at
    a time as it's first needed (which is what kept happening), find
    every directory under /app/.apt that actually contains a shared
    library and add all of them to LD_LIBRARY_PATH once, before any
    other code runs or spawns a subprocess. No-ops instantly on
    non-Heroku hosts (VPS, Docker) where /app/.apt doesn't exist.
    """
    apt_root = "/app/.apt"
    if not os.path.isdir(apt_root):
        return
    lib_dirs = set()
    for dirpath, _dirnames, filenames in os.walk(apt_root):
        if any(".so" in f for f in filenames):
            lib_dirs.add(dirpath)
    if not lib_dirs:
        return
    existing = os.environ.get("LD_LIBRARY_PATH", "")
    os.environ["LD_LIBRARY_PATH"] = ":".join(
        sorted(lib_dirs) + ([existing] if existing else [])
    )
    print(f"[start] Added {len(lib_dirs)} apt library directory(ies) to LD_LIBRARY_PATH.")


_fix_apt_library_paths()

import asyncio
import contextlib
import sys
from datetime import datetime

import config
from core.logger import get_logger
from validate_startup import run_checks

log = get_logger(__name__)

VERSION = "2.0.0"

BANNER = r"""
██╗   ██╗ ██████╗███████╗██╗ ██████╗ ██╗  ██╗████████╗
██║   ██║██╔════╝██╔════╝██║██╔════╝ ██║  ██║╚══██╔══╝
██║   ██║██║     █████╗  ██║██║  ███╗███████║   ██║
╚██╗ ██╔╝██║     ██╔══╝  ██║██║   ██║██╔══██║   ██║
 ╚████╔╝ ╚██████╗██║     ██║╚██████╔╝██║  ██║   ██║
  ╚═══╝   ╚═════╝╚═╝     ╚═╝ ╚═════╝ ╚═╝  ╚═╝   ╚═╝
"""


def print_banner() -> None:
    print(BANNER)
    print("        Telegram VC-to-VC Audio Forwarding Userbot")
    print("=" * 58)
    print(f"  Version       : {VERSION}")
    print(f"  Owner ID      : {config.OWNER_ID}")
    print(f"  Logger Group  : {config.LOGGER_GROUP}")
    print(f"  Record Group  : {config.RECORD_GROUP}")
    print(f"  PulseAudio    : {config.PULSE_SINK_NAME}")
    print(f"  Boot time     : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 58 + "\n")


def boot() -> None:
    print_banner()
    log.info("VCFight v%s booting up...", VERSION)

    run_checks()

    # Imported after the banner/checks so a missing dependency fails
    # fast with a readable message instead of a half-started client.
    from main import main as run_bot

    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        log.info("Interrupted by user (Ctrl+C). Exiting.")
    finally:
        log.info("VCFight has stopped.")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        boot()
        sys.exit(0)
