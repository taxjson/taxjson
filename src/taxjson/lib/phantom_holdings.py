"""Deprecated name of taxjson.lib.missing_history (renamed 2026-10).

Kept so external imports keep working: `import taxjson.lib.phantom_holdings`
and `from taxjson.lib.phantom_holdings import detect_phantoms` resolve to
the SAME module object as taxjson.lib.missing_history (so a mock.patch on
either name patches both). The old function and class names are aliases
defined there (detect_phantoms, load_phantoms, PhantomCandidate, ...).
"""
import sys as _sys

from taxjson.lib import missing_history as _missing_history

_sys.modules[__name__] = _missing_history
