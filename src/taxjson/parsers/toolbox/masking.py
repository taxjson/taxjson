"""How a message prints a broker's identifier: `mask_id`, the privacy rule
of `taxjson.lib.privacy`, which the parsers read from here (step 5a of the
engine refactor, PLAN.md; one function for every spelling, backlog
TJ-2063)."""
from taxjson.lib.privacy import mask_id

__all__ = ["mask_id"]
