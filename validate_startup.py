"""
Pre-flight checks run before the bot starts.

Importing `config` already exits the process with a clear message if
any required environment variable is missing. This module adds a
second layer of checks for the system binaries the audio bridge depends
on (ffmpeg, pactl/PulseAudio), so a missing dependency shows up as one
readable message at startup instead of a confusing crash three layers
deep the first time /join is used.

Usage:
    python3 validate_startup.py       # standalone check, prints a report
    from validate_startup import run_checks   # called from main.py
"""

import shutil
import sys

import config
from core.logger import get_logger

log = get_logger(__name__)

REQUIRED_BINARIES = {
    "ffmpeg": "Required for all audio/video filtering and streaming. "
              "Install with `apt install ffmpeg`.",
    "pactl": "Required for the PulseAudio bridge that moves audio between "
             "the two Voice Chats. Install with "
             "`apt install pulseaudio pulseaudio-utils`.",
}

OPTIONAL_BINARIES = {
    "Xvfb": "Only required for /screenshare. Install with `apt install xvfb`.",
}


def _check_binaries(binaries: dict, *, required: bool) -> list:
    missing = []
    for binary, hint in binaries.items():
        if shutil.which(binary) is None:
            missing.append((binary, hint))
            level = log.error if required else log.warning
            level("Missing %s binary: %s -- %s", "required" if required else "optional", binary, hint)
    return missing


def run_checks(exit_on_failure: bool = True) -> bool:
    """Returns True if the environment is fully ready to run the bot."""
    log.info("Running startup validation...")

    # Importing config already validated every required env var; if we
    # got this far, credentials and IDs are present and well-formed.
    log.info("Environment variables OK (OWNER_ID=%s, LOGGER_GROUP=%s).",
              config.OWNER_ID, config.LOGGER_GROUP)

    missing_required = _check_binaries(REQUIRED_BINARIES, required=True)
    _check_binaries(OPTIONAL_BINARIES, required=False)

    if missing_required:
        log.error(
            "Startup validation failed: %d required system binary(ies) missing.",
            len(missing_required),
        )
        if exit_on_failure:
            sys.exit(
                "\n[validate_startup] Missing required dependencies:\n"
                + "\n".join(f"  - {b}: {hint}" for b, hint in missing_required)
                + "\n\nRun ./setup_pulseaudio.sh (or install manually) and try again."
            )
        return False

    log.info("Startup validation passed. All required dependencies are present.")
    return True


if __name__ == "__main__":
    ok = run_checks(exit_on_failure=False)
    print("OK" if ok else "FAILED")
    sys.exit(0 if ok else 1)
          
