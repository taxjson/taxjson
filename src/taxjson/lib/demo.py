"""The `taxjson init --demo` project: a Canadian year folder for 2024
built from made-up broker exports, ready to run (no network beyond the
exchange rates, nothing to answer).

The exports ship in src/taxjson/data/demo/<account>/ (package data):
the broker demos of examples/ (the same files; the two Interactive
Brokers statements also carry a Cash Report, so their money reconciles)
and one more IB statement with the getting-started scenarios:

  * missing history: a sale of SAMPG with no purchase in the files
    (`taxjson find-missing-history`, getting-started step 5);
  * an election: PARNT spins off SPNCO; the election is saved in the
    account's manifest.json (`taxjson spinoffs`);
  * a superficial loss: NVDA sold at a loss and bought back within 30
    days (account ib, `taxjson wash-sales`);
  * a written option: a SAMPW call sold to open in November and still
    open at the year end, its premium a 2024 gain under grant timing
    (`taxjson option-boundary`).

Every account id, ticker and amount is synthetic.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

YEAR = 2024
COUNTRY = "canada"
TIMEZONE = "America/Toronto"

# The accounts, in the order taxjson.toml lists them: (name, its table).
ACCOUNTS: Tuple[Tuple[str, Dict[str, object]], ...] = (
    ("margin", {"type": "taxable"}),
    ("ib", {"type": "taxable"}),
    ("questrade", {"type": "taxable"}),
    ("webull", {"type": "taxable"}),
    ("tfsa", {"type": "sheltered", "transfers": True}),
    ("crypto", {"type": "taxable", "crypto": True}),
)

# What each scenario is and the command that shows it.
SCENARIOS: Tuple[Tuple[str, str], ...] = (
    ("a sale with no purchase (SAMPG, margin)", "find-missing-history"),
    ("a spin-off and its saved election (PARNT, margin)", "spinoffs"),
    ("a superficial loss (NVDA, ib)", "wash-sales"),
    ("a written option (SAMPW, margin)", "option-boundary"),
)


def data_dir() -> Path:
    """The packaged demo exports."""
    return Path(__file__).resolve().parents[1] / "data" / "demo"


def files() -> List[Tuple[str, Path]]:
    """[(account, packaged file)] of every demo input, sorted."""
    out = []
    for name, _cfg in ACCOUNTS:
        d = data_dir() / name
        out += [(name, p) for p in sorted(d.iterdir()) if p.is_file()]
    return out


def settings() -> Dict[str, object]:
    """The demo's [settings] beyond the scaffold's: its zone (it has a
    crypto account) and the first year filed under grant timing (it
    writes an option)."""
    return {"local_timezone": TIMEZONE, "option_grant_timing_since": YEAR}
