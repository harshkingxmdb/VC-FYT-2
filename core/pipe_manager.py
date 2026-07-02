"""
Named-pipe (FIFO) management.

Each active playback session gets two FIFOs:

    <chat_id>_out.raw  -> what the assistant streams INTO that chat's
                           voice call (the forwarded, filtered audio).
    <chat_id>_in.raw   -> what the assistant streams into the Logger
                           Group's voice call (silence, so it can listen
                           without talking).

FIFOs are created lazily and cleaned up when a session ends so restarts
never crash on "file exists".
"""

import os

import config
from core.logger import get_logger

log = get_logger(__name__)


def _pipe_path(chat_id: int, suffix: str) -> str:
    return os.path.join(config.PIPE_DIR, f"{chat_id}_{suffix}.raw")


def create_pipe(chat_id: int, suffix: str) -> str:
    path = _pipe_path(chat_id, suffix)
    if os.path.exists(path):
        os.remove(path)
    os.mkfifo(path)
    log.debug("Created FIFO %s", path)
    return path


def get_output_pipe(chat_id: int) -> str:
    return _pipe_path(chat_id, "out")


def get_silence_pipe(chat_id: int) -> str:
    return _pipe_path(chat_id, "in")


def cleanup_pipe(chat_id: int, suffix: str) -> None:
    path = _pipe_path(chat_id, suffix)
    if os.path.exists(path):
        try:
            os.remove(path)
            log.debug("Removed FIFO %s", path)
        except OSError as exc:
            log.warning("Could not remove FIFO %s: %s", path, exc)


def cleanup_all(chat_id: int) -> None:
    cleanup_pipe(chat_id, "out")
    cleanup_pipe(chat_id, "in")
          
