"""Shared numeric helpers for taxjson: the argparse number types, and
`D`, `round_half_up` and `round_floats`, whose home is model/money since
step 6c (the typed gains Result writes with them; model imports no lib
module). They are re-exported here for their importers."""
from taxjson.model.money import _QTY_FIELDS, D, round_floats, round_half_up

__all__ = ["D", "_QTY_FIELDS", "nonneg_float_arg", "positive_float_arg", "round_floats",
           "round_half_up"]


def nonneg_float_arg(text: str) -> float:
    """argparse type for a tolerance / threshold flag: a finite number
    >= 0. `type=float` took "nan", "-1" and "inf": every match became a
    MISMATCH (nan / negative) or a real gap was certified OK (inf), and
    a negative T1135 threshold read "FILING REQUIRED" (R1-333,
    S037-14, R1-242)."""
    import argparse
    import math
    try:
        v = float(text)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"not a number: {text!r}")
    if not math.isfinite(v) or v < 0:
        raise argparse.ArgumentTypeError(
            f"must be a finite number >= 0, got {text!r}")
    return v


def positive_float_arg(text: str) -> float:
    """argparse type for a threshold that must be finite and > 0."""
    import argparse
    v = nonneg_float_arg(text)
    if v <= 0:
        raise argparse.ArgumentTypeError(
            f"must be a finite number > 0, got {text!r}")
    return v
