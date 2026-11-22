"""What a carryover reads besides the books: the claimed-amounts file
(load_claimed: `YEAR AMOUNT` lines) and the figures a US lock says were
filed (us_lock_gains: the Form 8949 gains with the section 1256 net split
40/60; us_lock_1256). bin/taxjson_carryover.py builds the carryover; the
carryforward ledger and the project migration read them from here.
"""
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from taxjson.lib.cli_diag import read_text_utf8
from taxjson.lib.tax_estimate import section_1256_split


# A grouped amount's lead group has no leading zero ('0,125' is not
# 125): the same rule the S055-08 fix put in the broker parsers
# (audit A2-0667).
_AMOUNT_RE = re.compile(
    r"\$?([1-9]\d{0,2}(?:,\d{3})+|\d+)(\.\d+)?|\$?\.\d+")


def _parse_amount(text: str) -> float:
    """A claimed amount as the T1 / notice of assessment prints it:
    plain `1234.56`, grouped `1,234.56`, optionally with a leading `$`
    (S027-13: those forms were dropped with a warning, overstating the
    carryforward). A malformed grouping (`12,34`) is refused — never
    guessed."""
    if _AMOUNT_RE.fullmatch(text) is None:
        return float(text)          # 'nan', '-5', '1e3' -> float()'s rules
    return float(text.lstrip("$").replace(",", ""))


# A claim's YEAR must be a plausible return year — the range
# `tjs init` / [settings] year accept. A typo ('2205', '0') became a
# spurious ledger row that silently consumed the claim (S027-23).
CLAIM_MIN_YEAR = 1900


def _claim_max_year() -> int:
    from datetime import date as _date
    return _date.today().year + 1


def load_claimed(path: Optional[Path],
                 ignored: Optional[List[str]] = None) -> Dict[int, float]:
    """`YEAR AMOUNT` lines -> {year: amount}. A line that cannot be read
    is warned about and skipped; when `ignored` is given each skipped
    line is appended to it (`path:lineno: text`) so the caller can
    report the ledger as incomplete (S001-04: the checklist said
    'present' over a file whose only line was dropped). A UTF-8 BOM
    (Notepad) is not part of the first line (S027-18)."""
    claimed: Dict[int, float] = {}
    if path is None:
        return claimed
    import math
    hi = _claim_max_year()
    for lineno, line in enumerate(read_text_utf8(path).splitlines(), 1):
        stripped = line.split('#', 1)[0].strip()
        if not stripped:
            continue
        parts = stripped.split()
        why = "expected `YEAR AMOUNT` (amount >= 0)"
        try:
            year, amount = int(parts[0]), _parse_amount(parts[1])
            # not-isfinite: 'nan' passed the `< 0` check and poisoned
            # the ledger — APPLIED ballooned to the whole balance with
            # zero diagnostics (REVIEW #38).
            if len(parts) != 2 or amount < 0 or not math.isfinite(amount):
                raise ValueError
            if not CLAIM_MIN_YEAR <= year <= hi:
                why = (f"YEAR {year} is not a plausible tax year "
                       f"({CLAIM_MIN_YEAR}..{hi})")
                raise ValueError
        except (ValueError, IndexError):
            from taxjson.lib.out import warn as _warn
            _warn(f"{path.name}:{lineno}: {why}, got {stripped!r} — line "
                  f"ignored")
            if ignored is not None:
                ignored.append(f"{path.name}:{lineno}: {stripped}")
            continue
        claimed[year] = claimed.get(year, 0.0) + amount
    return claimed


def _finite(v: Any, what: str) -> float:
    import math
    x = float(v)
    if not math.isfinite(x):
        raise ValueError(f"{what} is not a finite number ({v!r})")
    return x


def _form_lines(doc: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """The lock's return lines (every account's form_lines added up),
    None when an account records none. ValueError on a line that is not
    a finite number."""
    accts = doc.get('accounts')
    if not (isinstance(accts, dict) and accts and all(
            isinstance(a, dict) and isinstance(a.get('form_lines'), dict)
            for a in accts.values())):
        return None
    lines: Dict[str, float] = {}
    for a in accts.values():
        for k, v in a['form_lines'].items():
            lines[str(k)] = lines.get(str(k), 0.0) + _finite(
                v, f"form_lines {k}")
    return lines


def us_lock_1256(doc: Dict[str, Any]) -> Tuple[float, bool]:
    """(the §1256 net a US lock records — its Form 6781 line, form_lines
    `6781_gain`; 0.0 for a lock without one — and whether its carryover
    took that net all short-term: a carry-forward block without the
    Form 6781 parts, `section_1256_long_term`, was closed before taxjson
    split it 60/40 (US-FUT-03; backlog TJ-0611)). Only a lock that
    carried a loss out counts: the split moves a carryover between its
    terms, never its total, so one that carried none is right as it
    is. ValueError / TypeError on a figure that is not a number."""
    lines = _form_lines(doc)
    s = float((lines or {}).get('6781_gain') or 0.0)
    cl = (doc.get('carryforwards') or {}).get('capital_loss') \
        if isinstance(doc.get('carryforwards'), dict) else None
    unsplit = (isinstance(cl, dict) and abs(s) > 0.005
               and 'section_1256_long_term' not in cl
               and (float(cl.get('st_closing') or 0.0)
                    + float(cl.get('lt_closing') or 0.0)) > 0.005)
    return s, unsplit


def us_lock_gains(doc: Dict[str, Any]) -> Tuple[float, float, str]:
    """(short-term, long-term, source) a US lock says was FILED: the Form
    8949 Part I / Part II gains with the §1256 net of Form 6781 (form
    lines `6781_gain`) added 40% short-term and 60% long-term (lines 8
    and 9; US-FUT-03), or all short-term where the lock's carryover took
    it so (closed before the split: us_lock_1256) — the figures its
    carryover was built on (backlog TJ-0611: the row left the §1256 net
    out, and did not add up to the balance carried); else its totals (a
    gain with no term short-term, US-RPT-07). KeyError / TypeError /
    ValueError on a lock without usable figures."""
    lines = _form_lines(doc)
    if lines is not None:
        st, lt = lines.get('I_gain', 0.0), lines.get('II_gain', 0.0)
        s, unsplit = us_lock_1256(doc)
        if unsplit:
            st += s
        elif abs(s) > 0.005:
            s_st, s_lt = section_1256_split(s)
            st, lt = st + s_st, lt + s_lt
        return st, lt, 'form_lines'
    totals = doc['totals']
    realized = _finite(totals['realized'], 'totals.realized')
    st = _finite(totals.get('st_gain') or 0.0, 'totals.st_gain')
    lt = _finite(totals.get('lt_gain') or 0.0, 'totals.lt_gain')
    st += realized - (st + lt)
    return st, lt, 'realized'
