""".tt lines whose total disagrees with their own quantity, price and fee.

A `.tt` BUYSELL / ASSIGN line carries `qty price total [fee]`, and the
TOTAL is what the engine books: a purchase's cost, a sale's proceeds.
taxjson-convert-tt compares it with qty x price (x the contract size) +
fee for a purchase, - fee for a sale, and warns when the two differ by
more than 1% of qty x price, with a floor of 0.05 so a cent of rounding
on a tiny line is never a typo (TOLERANCE). A line with no price (price
0: the total alone states the amount) and a futures line without its
size are not compared: the line has nothing to check the total against.
A sale whose commission exceeds its gross written with a 0 total is
said too (the loss would miss the excess commission).

The warning is captured in the stage's .diag (work/<acct>_tt_<stem>.json
.diag, the .sum DIAGNOSTICS). This module reads it back, so that

* `taxjson run` shows each line on its console as a `Warning:` naming
  file:line, the written total, qty x price + fee and that the total is
  what is booked — on every run, cached or not — and `run --strict`
  stops on one (lib/out house style; QA F2);
* `taxjson checklist` lists them in its run-clean step.

A total that is right as written (a charge the line does not show)
agrees once the difference is in the line's fee column.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple

# The 1% of qty x price (x size) a total may differ by, with its floor
# in the line's currency: a cent of rounding on a tiny total is never a
# typo, a 5x total always is.
TOLERANCE_PCT = 0.01
TOLERANCE_FLOOR = 0.05


def tolerance(expected: float) -> float:
    """How far a total may be from qty x price +/- fee (`expected`)."""
    return max(TOLERANCE_FLOOR, TOLERANCE_PCT * max(abs(expected), 1.0))


# taxjson-convert-tt's captured lines (its _where(source) is
# `<file>:<line>: `).
_TOTAL_RE = re.compile(
    r"^(?:[\w./-]+: )?warning: (?P<where>.+?:\d+): \.tt line total "
    r"(?P<total>-?[\d.]+) differs from (?P<formula>\S+) = "
    r"(?P<expected>-?[\d.]+) by more than 1%: (?P<line>.+?) — check for "
    r"a typo")
_SELL0_RE = re.compile(
    r"^(?:[\w./-]+: )?warning: (?P<where>.+?:\d+): \.tt sell total 0 on a "
    r"sale whose commission (?P<fee>[\d.]+) exceeds its gross "
    r"(?P<gross>[\d.]+): the proceeds are (?P<expected>-?[\d.]+)"
    r".*?: (?P<line>'.*'|\".*\")\s*$")


@dataclass(frozen=True)
class Mismatch:
    """One .tt line whose total is not its qty x price +/- fee."""
    where: str          # `<file>:<line>` as the .diag names it
    total: str          # the total as written (2 decimals)
    formula: str        # `qty*price+fee`, `qty*price*100-fee` ...
    expected: str       # what the formula gives (2 decimals)
    line: str           # the .tt line, quoted as the .diag quotes it
    sell_zero: bool = False

    def line_text(self) -> str:
        """The .tt line as written (the .diag quotes it)."""
        t = self.line
        if len(t) >= 2 and t[0] == t[-1] and t[0] in "'\"":
            t = t[1:-1]
        return t

    def formula_text(self) -> str:
        """The formula as a person reads it (`qty x price + fee`)."""
        f = self.formula.replace("*", " x ")
        return f.replace("+", " + ").replace("-", " - ")

    def message(self, where: str = "") -> Tuple[str, List[str]]:
        """(headline, details) of the console Warning; `where` names
        the line (default: the .diag's `<file>:<line>`)."""
        where = where or self.where
        if self.sell_zero:
            return (f"{where}: a .tt sale's total is 0 but its "
                    f"commission exceeds its gross: the proceeds are "
                    f"{self.expected}",
                    ["0 is what is booked, which leaves the excess "
                     "commission out of the loss:", self.line_text(),
                     f"Write the negative total ({self.expected}). `run "
                     f"--strict` stops on it."])
        return (f"{where}: a .tt line's total {self.total} is not "
                f"{self.formula_text()} = {self.expected}",
                ["The total is what is booked (a purchase's cost, a "
                 "sale's proceeds), so check it for a typo:",
                 self.line_text(),
                 "If it is right as written, put the difference in the "
                 "line's fee column so the two agree. `run --strict` "
                 "stops on it."])


def parse_lines(lines: Iterable[str]) -> List[Mismatch]:
    """The mismatches among captured convert-tt lines."""
    out: List[Mismatch] = []
    for ln in lines:
        m = _TOTAL_RE.match(ln)
        if m:
            out.append(Mismatch(m["where"], m["total"], m["formula"],
                                m["expected"], m["line"]))
            continue
        m = _SELL0_RE.match(ln)
        if m:
            out.append(Mismatch(m["where"], "0.00", "qty*price-fee",
                                m["expected"], m["line"], sell_zero=True))
    return out


def read_diag(tt_json: Path) -> List[Mismatch]:
    """The mismatches the .tt stage that wrote `tt_json` captured in its
    .diag ([] when it has none or it cannot be read)."""
    p = Path(tt_json).with_name(Path(tt_json).name + ".diag")
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return parse_lines(text.splitlines())


def project_mismatches(root: Path, accounts: Iterable[str]
                       ) -> List[Tuple[str, str, Mismatch]]:
    """[(account, .tt file name, Mismatch)] over every configured
    account's .tt inputs, read from the last run's work/ (what `taxjson
    checklist` lists)."""
    from taxjson.lib.pipeline import tt_json_path
    root = Path(root)
    out: List[Tuple[str, str, Mismatch]] = []
    for acct in accounts:
        d = root / "inputs" / str(acct)
        try:
            tts = sorted(p for p in d.iterdir()
                         if p.is_file() and p.suffix.lower() == ".tt")
        except OSError:
            continue
        for tt in tts:
            for m in read_diag(tt_json_path(root / "work", str(acct),
                                            tt.name)):
                out.append((str(acct), tt.name, m))
    return out
