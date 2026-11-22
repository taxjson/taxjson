"""Shared test helpers: number strings and a float key.

Shared from: test_model_evidence
"""
import struct


def _key(x):
    """Floats compared bit for bit (a NaN equals itself)."""
    if isinstance(x, float):
        return ("f", struct.pack("<d", x))
    if isinstance(x, (list, tuple)):
        return tuple(_key(y) for y in x)
    return x


NUMBERS = ["1", "1.5", "-2", "0", "-0", "1e-05", "nan", "inf", "-inf", "1e-400", "1e400",
           "abc", "1_000", "+3", ".5", "5.", "0.000", "20001.00", "sNaN", "Infinity",
           "1e999999999999999999999", "-1e-999999999999999999999"]
