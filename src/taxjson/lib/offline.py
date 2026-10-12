"""TAXJSON_OFFLINE — the one switch that forbids taxjson's default
network egress (FX rates, crypto prices, the current-price chain; see
SECURITY.md).

Every reader goes through `offline_enabled()`. A bare truthiness test
(`if os.environ.get("TAXJSON_OFFLINE")`) treated `TAXJSON_OFFLINE=0`
and `=false` as ON, the opposite of what anyone writing them means.
Only 1/true/yes/on (any case) turn it on; 0/false/no/off/empty are off;
anything else is off with a one-time warning naming the accepted
values.
"""

from taxjson.lib.stage_msg import emit_line
import os
import sys
from typing import Mapping, Optional

ENV_VAR = "TAXJSON_OFFLINE"
_ON = frozenset({"1", "true", "yes", "on"})
_OFF = frozenset({"", "0", "false", "no", "off"})
_warned = set()


def offline_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """True when TAXJSON_OFFLINE is set to 1/true/yes/on."""
    raw = (env if env is not None else os.environ).get(ENV_VAR)
    if raw is None:
        return False
    val = raw.strip().lower()
    if val in _ON:
        return True
    if val not in _OFF and val not in _warned:
        _warned.add(val)
        emit_line(f"taxjson: warning: {ENV_VAR}={raw!r} is not recognised "
                  f"(use 1/true/yes/on to forbid downloads, 0/false/no/off "
                  f"to allow them); treating it as OFF.", file=sys.stderr)
    return False
