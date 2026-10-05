"""Which parser reads a broker CSV — decided by the file's CONTENT.

Order (`detect`):

1. An explicit generic mapping is configuration: a `<file>.csv.toml`
   sidecar beside the CSV, or a `generic_*.csv` file with the folder's
   shared `generic.toml` (lib/brokerages/generic.mapping_path) — the
   file is read by the generic importer whatever its content.
2. Content: every supported export has a structural signature, taken
   from the parser's OWN header test (one definition, so detection and
   parsing cannot drift):
     ib          the section,Header/Data shape (`Statement,Header` ...)
     questrade   questrade.missing_columns (all _QT_COLUMNS, first row)
     webull      WebullBrokerage.label_hits (every required label)
     rbc_direct  rbc_direct.header_missing (Date, Activity, ... and
                 Value/Amount)
     coinbase    coinbase.resolve_header (every required field through
                 the synonym table; preamble lines above it allowed)
     kraken      kraken.header_kind (the trades or ledgers columns)
   The signatures are mutually exclusive (tests/test_fix_detect.py
   checks every sample against every detector); a file matching two is
   refused, naming both — never a silent pick.
   A positions-ONLY report (an RBC "Holdings Export": what is held on a
   date, not activity — lib/positions_reports) matches no trade
   detector: `detect` returns it with `positions` set and no parser,
   and `taxjson run` skips it with a note (`taxjson sanity` and
   `taxjson opening` read it).
3. The file name, only when no content signature matched: a `cb_` /
   `kr_` / `generic_` prefix, or the word coinbase / kraken in the name.
   When the content matched and the name suggests another broker, the
   content wins and a note says so.
"""

import csv
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from taxjson.lib.brokerages.base import (BrokerageParseError,
                                         decode_broker_text, shown_name)

# How far into the file a header row may sit (an RBC / Webull / Coinbase
# preamble, blank spacer lines).
SCAN_ROWS = 40

DISPLAY_NAMES: Dict[str, str] = {
    'ib': 'Interactive Brokers',
    'questrade': 'Questrade',
    'webull': 'Webull',
    'rbc_direct': 'RBC Direct Investing',
    'coinbase': 'Coinbase',
    'kraken': 'Kraken',
    'generic': 'generic',
}

# The file-name fallback (step 3). Underscore prefixes match only at the
# START of the name: a substring match routed ibkr_statement.csv
# (contains "kr_") to the Kraken parser (REVIEW-2026-07-ui #3). Words
# match anywhere. Prefixes are tried before words, so
# kr_trades_moved_from_coinbase.csv is Kraken's.
NAME_PREFIXES: Tuple[Tuple[str, str], ...] = (
    ("cb_", "coinbase"),
    ("kr_", "kraken"),
    # The column-mapped importer for unsupported brokers; a generic_ file
    # with no mapping still goes there so its "no mapping" message says
    # which file to write.
    ("generic_", "generic"),
)
NAME_WORDS: Tuple[Tuple[str, str], ...] = (
    ("coinbase", "coinbase"),
    ("kraken", "kraken"),
)

# Sections an IB Activity Statement (or a Flex query with section codes)
# can start with. A file whose first row is `<one of these>,Header,...`
# has the section,Header/Data shape the IB parser reads — whether or not
# a BrokerName row follows (audit R1-57: a Trades-first Flex download, a
# statement without the BrokerName row, or a Title row before it all
# parsed, but `taxjson run` stopped with "cannot detect broker").
IB_SECTIONS = frozenset({
    'Statement', 'Account Information', 'Trades', 'Dividends',
    'Withholding Tax', 'Interest', 'Fees', 'Transfers', 'Corporate Actions',
    'Cash Report', 'Open Positions', 'Financial Instrument Information',
    'Change in Dividend Accruals', 'Commission Adjustments',
    'Options Expirations', 'Net Asset Value', 'Change in NAV',
    'Mark-to-Market Performance Summary',
    'Realized & Unrealized Performance Summary', 'Deposits & Withdrawals',
    'Transaction Fees'})


class AmbiguousBroker(BrokerageParseError):
    """A file whose content matches two broker exports' signatures."""


@dataclass(frozen=True)
class Detection:
    """How one CSV was routed. broker is a parser id (None: undetected);
    how is 'mapping' | 'content' | 'name' | ''; reason is the text shown
    in parentheses on the run's per-file line; note is a one-line
    remark (content vs name disagreement, name-only routing); hint names
    the closest export layout when nothing matched; error is a decoding
    failure."""
    path: Path
    broker: Optional[str]
    how: str = ''
    reason: str = ''
    note: str = ''
    hint: str = ''
    error: str = ''
    # A positions-only report (lib/positions_reports kind id, e.g.
    # 'rbc_holdings') and its as-of date: never parsed as activity.
    positions: str = ''
    as_of: str = ''

    @property
    def display(self) -> str:
        if self.positions:
            return 'positions report'
        return DISPLAY_NAMES.get(self.broker or '', self.broker or '?')

    def line(self, shown: Optional[str] = None) -> str:
        """`<file> → <Broker> (<reason>)` — the run's per-file line."""
        where = shown if shown is not None else str(self.path)
        if self.positions:
            return (f"{where} → positions report ({self.reason}) — not "
                    f"activity; skipped (read it with `taxjson sanity` "
                    f"or `taxjson opening`)")
        return f"{where} → {self.display} ({self.reason})"


# ------------------------------------------------------------ reading

def csv_rows(text: str, limit: Optional[int] = None) -> List[List[str]]:
    rows: List[List[str]] = []
    try:
        for row in csv.reader(io.StringIO(text, newline="")):
            rows.append(row)
            if limit and len(rows) >= limit:
                break
    except csv.Error:
        pass
    return rows


def _first_nonempty(rows) -> Optional[List[str]]:
    return next((r for r in rows if r and any(c.strip() for c in r)), None)


def _cols(cells, n: int = 3) -> str:
    """The first n header cells named in a reason, as the file spells
    them, + '…' when there are more."""
    shown = [c.strip() for c in cells if c and c.strip()]
    return ",".join(shown[:n]) + ("…" if len(shown) > n else "")


# ------------------------------------------------------------ IB

def looks_like_ib_rows(rows) -> bool:
    """The IB section,Header/Data shape: the first non-empty row is the
    Header row of a known IB section, and the file is IB's — it names
    Interactive Brokers, or has Header rows of two IB sections, or is a
    Trades-only Flex download with IB's Trades columns. (A lone
    `Statement,Header` table of some other export is not IB.)"""
    first = _first_nonempty(rows)
    if not (first and len(first) >= 2 and first[1] == "Header"
            and first[0].strip() in IB_SECTIONS):
        return False
    sections = set()
    for row in rows:
        if len(row) >= 2 and row[1] == "Header" \
                and row[0].strip() in IB_SECTIONS:
            sections.add(row[0].strip())
        if any("Interactive Brokers" in c for c in row):
            return True
    if len(sections) >= 2:
        return True
    return (first[0].strip() == "Trades"
            and {"Asset Category", "Symbol"} <= {c.strip() for c in first})


def looks_like_ib_text(text: str) -> bool:
    """looks_like_ib_rows over CSV text (`taxjson fetch` checks a Flex
    download with it, so fetch accepts exactly what detection routes)."""
    return looks_like_ib_rows(csv_rows(text))


# ------------------------------------------------------------ detectors
# Each takes (rows, head) — every CSV record, and the first SCAN_ROWS —
# and returns (reason, None) on a match, (None, hint) on a near miss
# (the broker's anchor columns without all the required ones), or
# (None, None).

_Result = Tuple[Optional[str], Optional[str]]


def _ib(rows, head) -> _Result:
    if looks_like_ib_rows(rows):
        first = _first_nonempty(rows)
        return f'content: "{first[0].strip()},Header" preamble', None
    first = _first_nonempty(head)
    if first and len(first) >= 2 and first[1] == "Header" \
            and first[0].strip() in IB_SECTIONS:
        return None, ("an Interactive Brokers section row "
                      f"(\"{first[0].strip()},Header\") but no second IB "
                      f"section and no \"Interactive Brokers\" name")
    return None, None


def _questrade(rows, head) -> _Result:
    from taxjson.lib.brokerages.questrade import _QT_COLUMNS, missing_columns
    first = rows[0] if rows else []
    missing = missing_columns(first)
    if not missing:
        return f"content: columns {_cols(_QT_COLUMNS)}", None
    if len(missing) < len(_QT_COLUMNS) // 2 and \
            "Transaction Date" not in missing:
        return None, (f"a Questrade header lacking column(s) "
                      f"{', '.join(missing)}")
    return None, None


def _webull(rows, head) -> _Result:
    from taxjson.lib.brokerages.webull import WebullBrokerage as W
    near = None
    for cells in head:
        hits = W.label_hits(cells)
        missing = [k for k in W._REQUIRED if k not in hits]
        if not missing:
            # The canonical labels (a bilingual cell reads
            # "Currency Devise"): the needles the parser matches.
            labels = dict(W._HEADER_LABELS)
            return ("content: Trading Summary header "
                    + ",".join(labels[k].title()
                               for k in ("currency", "date", "action"))
                    + "…"), None
        if near is None and "action" in hits:
            near = (f"a Webull header (Action Code) lacking "
                    f"{', '.join(missing)}")
    return None, near


def _rbc(rows, head) -> _Result:
    from taxjson.lib.brokerages.rbc_direct import header_missing
    near = None
    for cells in head:
        missing = header_missing(cells)
        if missing is None:
            continue
        if not missing:
            return f"content: activity header {_cols(cells, 4)}", None
        near = near or (f"an RBC activity header lacking "
                        f"{', '.join(missing)}")
    # A "Holdings Export" (positions, not activity) is NOT an RBC
    # activity match: detect() routes it as a positions report.
    return None, near


def _coinbase(rows, head) -> _Result:
    from taxjson.lib.brokerages.coinbase import (_REQUIRED_FIELDS,
                                                 is_header_row,
                                                 resolve_header)
    for cells in head:
        if not is_header_row(cells):
            continue
        # The parser takes the FIRST Timestamp row as its header.
        hmap, missing = resolve_header(cells)
        if not missing:
            return (f"content: columns "
                    f"{','.join(cells[hmap[f]].strip() for f in _REQUIRED_FIELDS[:3])}"
                    f"…"), None
        if len(missing) < len(_REQUIRED_FIELDS) - 1:
            return None, (f"a Coinbase header lacking "
                          f"{', '.join(missing)}")
        return None, None
    return None, None


def _kraken(rows, head) -> _Result:
    from taxjson.lib.brokerages.kraken import header_kind
    first = rows[0] if rows else []
    kind, missing = header_kind(first)
    if not kind:
        return None, None
    what = "ledger" if kind == "ledgers" else "trades"
    if not missing:
        return f"content: {what} columns {_cols(first)}", None
    have = {(c or '').strip().lower() for c in first}
    if 'txid' in have:
        return None, (f"a Kraken {what} header lacking "
                      f"{', '.join(missing)}")
    return None, None


# Every content detector, by parser id. Order only fixes the order
# matches are NAMED in (an ambiguity is an error, never a pick).
DETECTORS: Dict[str, Callable[[list, list], _Result]] = {
    'ib': _ib,
    'questrade': _questrade,
    'webull': _webull,
    'rbc_direct': _rbc,
    'coinbase': _coinbase,
    'kraken': _kraken,
}


def content_matches(text: str) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
    """([(broker, reason)] matches, [(broker, hint)] near misses) of the
    decoded text against every detector."""
    rows = csv_rows(text)
    head = rows[:SCAN_ROWS]
    hits: List[Tuple[str, str]] = []
    near: List[Tuple[str, str]] = []
    for broker, fn in DETECTORS.items():
        reason, hint = fn(rows, head)
        if reason:
            hits.append((broker, reason))
        elif hint:
            near.append((broker, hint))
    return hits, near


# ------------------------------------------------------------ names

def name_hint(name: str) -> Optional[Tuple[str, str]]:
    """(token, broker) the file name suggests, or None."""
    lower = name.lower()
    for tok, broker in NAME_PREFIXES:
        if lower.startswith(tok):
            return tok, broker
    for tok, broker in NAME_WORDS:
        if tok in lower:
            return tok, broker
    return None


def generic_mapping(path: Path) -> Optional[Path]:
    """The generic mapping that configures this CSV, or None: its own
    `<name>.toml` sidecar (any file name), or — for a generic_ file —
    the folder's shared generic.toml (generic.mapping_path's rule). A
    sidecar that is a dangling link still counts: the generic importer
    then refuses it with its own message."""
    sidecar = path.with_name(path.name + ".toml")
    if sidecar.exists() or sidecar.is_symlink():
        return sidecar
    shared = path.parent / "generic.toml"
    if path.name.lower().startswith("generic_") and shared.is_file():
        return shared
    return None


# ------------------------------------------------------------ detect

def ambiguity_message(path: Path, hits) -> str:
    named = " and ".join(f"{DISPLAY_NAMES[b]} ({r})" for b, r in hits)
    return (f"{shown_name(path)}: its content matches {len(hits)} broker "
            f"exports — {named}; refusing to guess which parser reads it. "
            f"Is it two exports pasted into one file? Keep one export per "
            f"CSV (or describe it with a generic mapping, "
            f"{path.name}.toml).")


# (resolved path, mtime_ns, size) -> (hits, near, error): the parsers ask
# for a file's folder siblings once per file they parse; the content of
# an unchanged file is matched once.
_MATCH_CACHE: Dict[tuple, tuple] = {}


def _file_matches(path: Path):
    """(hits, near misses, decoding error, positions) of one file's
    content — positions is (kind, as_of) for a positions-only report
    (lib/positions_reports.positions_only_text), else None."""
    try:
        st = path.stat()
        key = (str(path.resolve()), st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    if key is not None and key in _MATCH_CACHE:
        return _MATCH_CACHE[key]
    hits: List[Tuple[str, str]] = []
    near: List[Tuple[str, str]] = []
    error = ''
    positions = None
    try:
        text = decode_broker_text(path.read_bytes(), shown_name(path))
        hits, near = content_matches(text)
        from taxjson.lib.positions_reports import positions_only_text
        positions = positions_only_text(text)
    except BrokerageParseError as e:
        error = str(e)
    except OSError as e:
        error = f"{shown_name(path)}: {e.strerror or e}"
    out = (hits, near, error, positions)
    if key is not None and not error:
        if len(_MATCH_CACHE) > 256:
            _MATCH_CACHE.clear()
        _MATCH_CACHE[key] = out
    return out


def detect(path: Path) -> Detection:
    """How `taxjson run` routes `path` (see the module docstring).
    Raises AmbiguousBroker when the content matches two exports."""
    path = Path(path)
    mapping = generic_mapping(path)
    hits, near, error, positions = _file_matches(path)
    if mapping is not None:
        note = ''
        if len(hits) == 1:
            note = (f"{shown_name(path)}: its content matches "
                    f"{DISPLAY_NAMES[hits[0][0]]}, but the generic mapping "
                    f"{mapping.name} configures it — read with the "
                    f"mapping.")
        return Detection(path, 'generic', 'mapping',
                         f"mapping {mapping.name}", note=note, error=error)
    if len(hits) > 1:
        raise AmbiguousBroker(ambiguity_message(path, hits))
    if positions and not hits:
        from taxjson.lib.positions_reports import kind_label
        kind, as_of = positions
        return Detection(
            path, None, 'positions',
            f"{kind_label(kind)}"
            + (f", as of {as_of}" if as_of else ""),
            positions=kind, as_of=as_of)
    named = name_hint(path.name)
    if hits:
        broker, reason = hits[0]
        note = ''
        if named and named[1] != broker:
            note = (f"{shown_name(path)}: the file name suggests "
                    f"{DISPLAY_NAMES[named[1]]} (\"{named[0]}\"), but its "
                    f"content matches {DISPLAY_NAMES[broker]} — read as "
                    f"{DISPLAY_NAMES[broker]}.")
        return Detection(path, broker, 'content', reason, note=note)
    if named:
        tok, broker = named
        return Detection(
            path, broker, 'name',
            f"file name \"{tok}\" — no content match",
            # A generic_ file without a mapping is not a guess to flag:
            # the generic importer stops on it, naming the mapping.
            note=(f"{shown_name(path)}: read as {DISPLAY_NAMES[broker]} "
                  f"by its file name (\"{tok}\") only — its header "
                  f"matches no supported export."
                  if broker != 'generic' else ''),
            hint="; ".join(h for _b, h in near), error=error)
    return Detection(path, None, '', '',
                     hint="; ".join(h for _b, h in near), error=error)


def detect_broker(path: Path) -> Optional[str]:
    """The parser id `taxjson run` uses for `path`, or None."""
    return detect(path).broker


# An inputs folder holds a handful of exports. A folder with more CSVs
# than this (a shared temporary directory) is not read file by file to
# find a parser's companions: there, only names suggesting the broker
# count, as before content detection.
SIBLING_SCAN_LIMIT = 200


def same_broker_siblings(path: Path, broker: str,
                         by_name: Optional[Callable[[str], bool]] = None
                         ) -> List[Path]:
    """The other CSVs in `path`'s folder that `taxjson run` routes to
    the same parser (a Kraken trades export's ledger, a Webull export's
    other years) — by the same detection, not by file name. `by_name`
    (lower-cased name -> bool) is the parser's old name test, used only
    in a folder of more than SIBLING_SCAN_LIMIT CSVs."""
    path = Path(path)
    try:
        me = path.resolve()
        cands = [p for p in path.parent.iterdir()
                 if p.suffix.lower() == '.csv'
                 and not p.name.startswith(('.', '~$'))]
    except OSError:
        return []
    cands = sorted(p for p in cands if p.name != path.name)
    scan = len(cands) <= SIBLING_SCAN_LIMIT
    out = []
    for p in cands:
        try:
            if not p.is_file() or p.resolve() == me:
                continue
            if scan:
                if detect(p).broker == broker:
                    out.append(p)
            elif by_name is not None and by_name(p.name.lower()):
                out.append(p)
        except (BrokerageParseError, OSError):
            continue            # reported when that file is detected
    return out
