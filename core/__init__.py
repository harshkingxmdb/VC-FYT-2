"""
core - the engine room of VCFight.

Holds everything that isn't a Telegram command handler: the MongoDB
layer, logging setup, the owner-only permission filter, the PulseAudio
bridge, FFmpeg process/filter helpers, named-pipe management, and the
CallManager that ties them all together into working VC-to-VC
forwarding sessions.

Re-exports the two symbols almost every other module needs
(`get_logger`, `owner_only`) so callers can do:

    from core import get_logger, owner_only

instead of reaching into the submodules directly.
"""

from core.logger import get_logger
from core.permissions import owner_only

__all__ = ["get_logger", "owner_only"]

__version__ = "2.0.0"
