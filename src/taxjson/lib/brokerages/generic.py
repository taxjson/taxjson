"""Generic column-mapped CSV importer — the escape hatch for brokers
without a dedicated parser.

Name the export `generic_<anything>.csv` and describe its layout in a
TOML mapping: either a sidecar (`generic_<anything>.csv.toml`, wins) or
one shared `generic.toml` in the same folder. Example:

    [columns]                   # CSV header names (case-insensitive)
    date     = "Trade Date"     # required; the TRADE date
    settle   = "Settle Date"    # optional; else computed (see below)
    action   = "Type"           # required unless defaults.action
    symbol   = "Ticker"         # required unless defaults.symbol
    symbol_new = "New Ticker"   # optional: a rename row's new ticker
    ratio    = "Ratio"          # optional: a split or rename row's ratio
    book_value = "Book Value"   # optional: a transfer-in's stated book value
    market_value = "Value"      # optional: a transfer's value at transfer
    lot_date = "Acquired"       # optional: an opening's acquisition date
    quantity = "Shares"
    price    = "Price"
    amount   = "Net Amount"     # fee-INCLUSIVE signed total
    fee      = "Commission"
    currency = "Currency"       # else defaults.currency
    account  = "Account"        # optional: the broker account of the row

    [formats]
    date = "%m/%d/%Y"           # strptime; default %Y-%m-%d
    settle = "%m/%d/%Y"         # optional; default = formats.date
    tax_sign = "cash"           # or "withheld" (positive = withheld)
    fee_sign = "cash"           # or "charged" (positive = charged)

    [actions]                   # CSV action value -> taxjson action
    "BUY"  = "buy"              # buy | sell | dividend |
    "SELL" = "sell"             #   dividend_in_lieu | tax |
                                #   interest | fee | split |
                                #   rename | transfer_in |
                                #   transfer_out | opening |
                                #   roc | adjust | assign_buy |
                                #   assign_sell | cash | skip
    "DIV"  = "dividend"
    "WHT"  = "tax"
    "JNL"  = "skip"             # explicit ignore (still counted)

    [defaults]
    currency = "CAD"            # required unless [columns].currency

    [options]
    allow_large_fees = false    # fee > 5% of gross refused unless true
    settle_on_trade_date = false  # true: settle on the trade date (the
                                  # date column is already settlement);
                                  # not for crypto (a security importer)

    [broker]
    name = "wealthsimple"       # optional: the real broker. tjs run
                                # parses each named broker on its own;
                                # its rows are recorded as
                                # generic:wealthsimple (fees report)
    account = "<id>"            # optional: the broker account every row
                                # belongs to (a mapped account column
                                # wins); cross-file dedup never collapses
                                # identical rows of two broker accounts

The mapping is validated strictly: an unknown section or key (a typo
such as `ammount`, `commission` or `[format]`) is refused with a
suggestion, dividend/tax/interest/fee targets require `amount`, and
buy/sell targets require `quantity` plus `price` or `amount`.

Conventions match the hand-written parsers: quantity is stored
magnitude-signed by direction (buys positive, sells negative), amounts
keep their CSV sign (a negative dividend is a reversal — never abs()).
A symbol written with an exchange suffix (.TO/.US/.AX/.L) keeps it,
and a Canadian venue (.V/.VN/.CN/.NE) is spelled .TO like every broker
parser spells it; a bare symbol takes the suffix of the row's currency
(CAD -> .TO, USD -> .US). Numbers accept a thousands comma (1,234.56)
and refuse a decimal comma (1234,56). Trade rows settle on the mapped
`settle` column when filled, else on the standard cycle from the
holiday-aware lib.dates.settlement_date (T+1 since May 2024, T+2
before, T+3 before 2017-09-05, T+5 before 1995-06-07 in USD and CAD;
options T+1) on the listing's market;
income rows are dated `date`. Symbols are upper-cased (`sample` and `SAMPLE`
are one security). Unmapped action values are counted and summarized,
never silently dropped — one that carries a quantity, an amount or a fee is an
UNBOOKED warning (echoed by `tjs run`, refused by `--strict`, a
failure under `taxjson-brokerage --lint`); map it, or map it to `skip`.
A `fee` row is booked positive = charged (the IB/Questrade/RBC
convention): a cash-signed CSV (negative = charged, the default
`[formats].fee_sign = "cash"`) is flipped; `fee_sign = "charged"` takes
the cell as is. A mapping that references columns the CSV doesn't have
refuses loudly.

Mis-mapped columns are the importer's worst failure mode — amounts in
the fee column can inflate a return by thousands without a word.
So every BUY/SELL row is cross-checked and the import REFUSES when:

* two logical fields name the same CSV header (e.g. fee = amount);
* |amount| is not |qty| × price × multiplier ± fee within 1% (+$0.05)
  (an explicit 0 in the amount cell is checked too: only a blank
  cell derives the amount) — the multiplier is 100 for an OCC option symbol; with no `fee`
  column mapped, the fee is INFERRED as the gap between |amount| and
  qty × price (a commission-inclusive Net column with no commission
  column) and a buy whose amount is below the gross is refused;
* fee is more than 5% of the gross (qty × price × multiplier), unless
  `[options] allow_large_fees = true`; a row with no price is checked
  against |amount| instead (fee >= a buy's whole amount is refused
  outright) — the missing price never switches the check off;
* a futures symbol (`F:` or `/` prefix) has no `amount` — the contract
  size is never guessed, so its fee-inclusive total must come from the
  CSV (futures rows skip the qty × price comparison);
* a record has more cells than the header, or a cell holding a line
  break (an unescaped quote swallowed the next row);
* a record has FEWER cells than the header, or the file's last record
  ends on a separator with no line break after it, or a currency cell
  is not a currency code (a cut-off export: [defaults] never fills a
  cell the cut took away);
* a mapped settle date more than 31 days after the trade (a typo that
  would move the sale into a later tax year); one more than 7 days
  after it is an ATTENTION line;

* the quantity is blank or zero, or neither price nor amount is given
  (the fee alone became the cost), or a stock buy costs exactly 0;
* the quantity/amount sign contradicts the mapped action (a negative
  quantity under a `buy` action, a positive quantity with a negative
  amount under `sell`, or a positive-quantity sell in a file whose
  other sells carry negative quantities);
* an income row's amount cell is blank;

and WARNS when amount equals qty × price to the cent while the fee is
nonzero (the GROSS column is probably mapped as `amount`). The
currency must be mapped or set in [defaults] — there is no silent USD.

A trade row's commission keeps its direction: with an amount column the
amount decides (a buy that cost less than qty × price was credited a
rebate), else an explicit [formats].fee_sign does; a rebate is booked as
a NEGATIVE fee (Questrade's convention). With neither, the fee cell is a
charge whatever its sign.

Futures (`F:`, `/` or `\\` prefix, all spelled `F:`) settle on the trade
date, or on the next settlement day under `futures_settle = "next_day"`
(the project setting, passed by taxjson-brokerage). An option closed at
$0 on its expiry day is dated and settled that day; one posted up to 7
days after the expiry is clamped to it. An exercise or an assignment is
the `assign_buy` / `assign_sell` targets' pair of ASSIGN legs (the
premium folds into the shares' cost or proceeds); a $0 option close
beside a stock trade at the strike under other targets is named on the
console (ATTENTION).
"""

from taxjson.project.toml_io import TOML_ERRORS, read_toml
from taxjson.lib.stage_msg import emit_line
from taxjson.lib.out import END_OF_RUN_MARK
from taxjson.lib.markets_data import known_suffixes as _known_sfx
import csv
from bisect import bisect_right
import difflib
import io
import os
import re
from functools import lru_cache
from dataclasses import dataclass, field as dc_field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple


from taxjson.parsers.toolbox.money import money_close, money_tolerance
from taxjson.lib.brokerages.base import (BaseBrokerage, BrokerageParseError,
                                         option_parts,
                                         check_account_plans,
                                         check_account_plans_across,
                                         canonical_ca_listing,
                                         is_ca_pref_series,
                                         decode_broker_text,
                                         parse_strict_number,
                                         refuse_pasted_overlap,
                                         shown_name)
from taxjson.lib.option_symbols import is_option_symbol, parse_option_expiry
from taxjson.lib.dates import settlement_date
from taxjson.parsers.protocol import Outcome, builds
from taxjson.model import money as M
from taxjson.lib.futures_prefixes import (FUTURES_PREFIX_RE, FUTURES_PREFIXES,
                                          FuturesSymbolError,
                                          canonical_futures_symbol)

# Row-level cross-check tolerance — the same 1% + $0.05 the .tt
# converter uses for hand-entered totals.
_REL_TOL = 0.01
_ABS_TOL = 0.05
# The most days after its expiry a $0 option close may post and still be
# the expiry (dated and settled on the expiry day).
_EXPIRY_POST_DAYS = 7
# A fee above this share of the gross is almost always a column mix-up
# (the amount or the gross in the fee column).
_MAX_FEE_SHARE = 0.05
_OPTIONS = ("allow_large_fees", "settle_on_trade_date")

_VALID_TARGETS = ("buy", "sell", "dividend", "dividend_in_lieu", "tax",
                  "interest", "fee", "split", "rename", "transfer_in",
                  "transfer_out", "opening", "roc", "adjust", "assign_buy",
                  "assign_sell", "skip", "cash")
_EVENT_TARGETS = ("split", "rename", "transfer_in", "transfer_out", "opening",
                  "roc", "adjust")
# Income-style targets book the amount cell as the money: it must be
# mapped and non-blank (an unmapped amount booked every dividend as 0).
_INCOME_TARGETS = ("dividend", "dividend_in_lieu", "tax", "interest", "fee")
# A leg of an exercise or an assignment, the units in or out: its money
# is a buy's or a sell's, its row the schema's ASSIGN.
_ASSIGN_SIDE = {"assign_buy": "buy", "assign_sell": "sell"}
# A mapped settle date this many calendar days after the trade is named
# (ATTENTION); past the hard limit it is refused: no listed security
# settles a month late, and the tax year follows the settle date
# (audit A2-1075).
_SETTLE_LATE_DAYS = 7
_SETTLE_REFUSE_DAYS = 31
# A currency cell: a 3-5 letter code (CAD, USD, USDC). A cut-off 'US'
# or 'C' is refused (audit A2-0030).
_CURRENCY_RE = re.compile(r"^[A-Z]{3,5}$")

# The mapping vocabulary. Anything else is a typo that used to be
# ignored without a word (`ammount`, `commission`, `[format]`,
# `tax_sgn`), so it is refused with a suggestion.
_SECTIONS = ("columns", "formats", "actions", "defaults", "options",
             "broker", "classify", "extract", "signature", "layout")
# [signature] headers: the header cells that identify this export, so a
# file in any folder, under any name, is read with the mapping that
# carries them (step 5d): two or more, enough to tell the export apart.
_SIGNATURE_KEYS = ("headers",)
# [layout] name: a mapping that stands for a named layout,
# <project>/layouts/<name>.toml or ~/.config/taxjson/layouts/<name>.toml
# (TAXJSON_LAYOUTS names another folder); its own [broker] keys win.
_LAYOUT_KEYS = ("name",)
LAYOUTS_DIR = "layouts"
ENV_LAYOUTS = "TAXJSON_LAYOUTS"
# The layouts taxjson ships, one per broker export it reads with no
# Python of its own (step 5, TJ-2322): the last folder, weighed only when
# no project or user layout fits (`_effective_mapping`), shadowed by a
# project's or user's layout of the same name; close-year copies the one
# a year is read with into the project, as it does a user's.
BUILTIN_LAYOUTS = Path(__file__).resolve().parents[2] / "data" / LAYOUTS_DIR
# A [[classify]] rule: `match` (a regex, searched case-insensitively) over
# the row's action cell, or its description cell when `over` says so,
# names the `target` of a row whose action is not in [actions]; with
# `action` (a regex) the action cell must match it too (a rule over the
# description scoped to one action, TJ-2575).
_CLASSIFY_KEYS = ("match", "target", "over", "action")
# [extract]: a regex per field, searched in the description cell; its
# capturing group the value when the field's own column is unmapped or
# blank (`ratio` may have two groups: new units, then old units).
_EXTRACT_KEYS = ("symbol", "symbol_new", "quantity", "ratio", "price", "option")
# `[extract] option`'s named groups: an option contract's parts, which
# make its OCC symbol (`format_occ_symbol`, as the broker parsers do).
_OPTION_GROUPS = ("root", "expiry", "right", "strike")
_OPTION_EXPIRY_FMT = "%m/%d/%y"
# `verified`: false for a mapping never run on a real export (a layout
# written from the export's documented shape, TJ-2573): every parse says
# so (`_say_unverified`); absent or true, nothing is said.
_BROKER_KEYS = ("name", "account", "verified")
# A broker name becomes part of a work/ file name and of the
# `generic:<name>` source label.
_BROKER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_COLUMN_KEYS = ("date", "settle", "action", "symbol", "symbol_new", "ratio",
                "book_value", "market_value", "lot_date", "quantity", "price",
                "amount", "fee", "currency", "account", "account_type",
                "description")
_FORMAT_KEYS = ("date", "settle", "tax_sign", "fee_sign", "option_expiry")
_DEFAULT_KEYS = ("currency", "action", "symbol")
# Common spellings difflib cannot guess.
_KEY_ALIASES = {
    "commission": "fee", "commissions": "fee", "fees": "fee",
    "qty": "quantity", "shares": "quantity", "units": "quantity",
    "ticker": "symbol", "security": "symbol",
    "new_symbol": "symbol_new", "new_ticker": "symbol_new",
    "split_ratio": "ratio", "acquired": "lot_date", "acquisition_date": "lot_date",
    "type": "action", "transaction_type": "action",
    "net": "amount", "net_amount": "amount", "total": "amount",
    "value": ("amount", "market_value"),
    "settlement": "settle", "settle_date": "settle",
    "settlement_date": "settle", "date_settle": "settle",
    "trade_date": "date", "ccy": "currency",
    "format": "formats", "action_map": "actions", "default": "defaults",
    "option": "options", "column": "columns",
    "classifier": "classify", "classifiers": "classify", "rules": "classify",
    "extracts": "extract", "extractor": "extract", "extractors": "extract",
    "pattern": "match", "regex": "match", "re": "match", "field": "over",
    "desc": "description", "memo": "description", "details": "description",
    "narrative": "description",
    "signatures": "signature", "header": "headers", "layouts": "layout",
    "use": "name",
}
# Exchange suffixes a user may write explicitly. One of these on the
# symbol is the LISTING and is kept; the row currency is only the
# settlement currency (SAMPLF.U.TO bought in USD is still SAMPLF.U.TO).
# The market data's one venue table (lib/markets): .VN was missing here.
from taxjson.lib.markets import canadian_suffixes as _ca_sfx  # noqa: E402
_KNOWN_SUFFIXES = tuple(sorted(_known_sfx(), key=lambda s: (-len(s), s)))


class _RowCells:
    """A row's mapped cells as the event builders read them (step 5b):
    `num(field)` the strict number, None for a blank or unmapped cell;
    `text(field)` the stripped text, '' when unmapped; `date_fmt` the
    mapping's date format; `amount_shared` whether a transfer's book or
    market value names the amount column's own header."""
    __slots__ = ("_row", "_where", "_num", "_cell", "date_fmt", "amount_shared")

    def __init__(self, row, where, num, cell, date_fmt, amount_shared):
        self._row, self._where, self._num, self._cell = row, where, num, cell
        self.date_fmt, self.amount_shared = date_fmt, amount_shared

    def num(self, field):
        return self._num(self._row, field, self._where)

    def text(self, field):
        return str(self._cell(self._row, field)).strip()


class _Cells:
    """A file's mapped cells as the parse loop reads them. `cell(row, field,
    default)` is the column's text or, for a blank or unmapped column, the
    value an [extract] pattern pulled for the row (`pulled`, set per row by
    `_classify_row`); `num(row, field, where)` the STRICT number, None for
    a blank or unmapped cell. The importer's charter is refuse-loudly:
    clean_number's garbage->0.0 silently booked $0-basis buys from
    'C$10.00'-style cells, and stripping every comma read a decimal comma
    ('12,50') as 1250 (audit R1-118). A comma is only a thousands
    separator here; accounting parentheses are negative ('(138.00)' ==
    -138.00). A refusal names the value's source: the column's cell, or
    the pattern when the cell was blank."""
    __slots__ = ("cols", "header", "pulled")

    def __init__(self, cols, header):
        self.cols, self.header, self.pulled = cols, header, {}

    def own(self, row, field) -> str:
        """The column's own text, '' when unmapped."""
        col = self.cols.get(field)
        return (row.get(self.header[col.strip().lower()]) or "") if col else ""

    def cell(self, row, field, default=""):
        v = self.own(row, field)
        return v if v.strip() else self.pulled.get(field) or default

    def num(self, row, field, where) -> Optional[float]:
        raw = str(self.cell(row, field, "")).strip()
        if not raw:
            return None
        s = raw
        for pre in ("CA$", "C$", "US$", "A$", "$", "€", "£"):
            s = s.replace(pre, "")
        s = s.strip()
        try:
            return parse_strict_number(s, field=field)
        except BrokerageParseError:
            why = (" — a decimal comma? Only a thousands comma "
                   "(1,234.56) is accepted; re-export with a "
                   "decimal point" if "," in s else "")
            src = (f"[extract].{field} pattern"
                   if field in self.pulled and not self.own(row, field).strip()
                   else f"[columns].{field} mapping")
            raise ValueError(
                f"generic importer: {where}: unparseable {field} value "
                f"{raw!r}{why} — fix the cell or the {src}.")


# The fields each target books, which an [extract] pattern may fill
# (every event names its symbol); a row nothing maps takes every
# pattern, for the UNBOOKED probe (TJ-2129).
_FIELDS_OF = {
    "buy": ("symbol", "quantity", "price", "option"),
    "sell": ("symbol", "quantity", "price", "option"),
    "opening": ("symbol", "quantity", "price", "option"),
    "split": ("symbol", "ratio"), "rename": ("symbol", "symbol_new", "ratio"),
    "transfer_in": ("symbol", "quantity", "option"),
    "transfer_out": ("symbol", "quantity", "option"),
    "assign_buy": ("symbol", "quantity", "price", "option"),
    "assign_sell": ("symbol", "quantity", "price", "option"),
    "skip": (), "cash": (),
}


def _amount_shared(cols) -> bool:
    """Whether a transfer's book or market value names the amount column's
    own header: then a transfer row's amount cell is read as that field;
    otherwise it is left unread and counted (TJ-2112)."""
    hdr = (cols.get("amount") or "").strip().lower()
    return bool(hdr) and any((cols.get(k) or "").strip().lower() == hdr
                             for k in ("book_value", "market_value"))


def _day_formats(date_fmt: str) -> tuple:
    """The formats an acquisition-date cell may be in: the mapping's date
    format, its date part when the format carries a clock (a lot date is
    a day, and an export's trade dates often carry a time), and ISO."""
    m = re.search(r"\s*%[HIMSXcfpzZ]", date_fmt)
    day_part = date_fmt[:m.start()].rstrip(" T,") if m else ""
    out = []
    for f in (date_fmt, day_part, "%Y-%m-%d"):
        if f and f not in out:
            out.append(f)
    return tuple(out)


def _did_you_mean(key: str, valid) -> str:
    k = str(key).strip().lower()
    alias = _KEY_ALIASES.get(k)
    names = [a for a in ((alias,) if isinstance(alias, str) else alias or ())
             if a in valid]
    if names:
        return f" Did you mean {' or '.join(repr(a) for a in names)}?"
    guess = difflib.get_close_matches(k, list(valid), n=1, cutoff=0.6)
    return f" Did you mean {guess[0]!r}?" if guess else ""


def _shared_wrongly(fields) -> bool:
    """Two logical fields on one CSV header, except a transfer's book or
    market value on the amount column's own (a transfer row books no
    amount, so its one cell is read once, as the field the mapping names;
    TJ-2112) and the description, text read beside any field, never
    money (an export's one text column is the action and the description
    both; TJ-2122)."""
    fs = set(fields) - {"description"}
    return (len(fs) > 1 and bool(fs - {"amount", "book_value"})
            and bool(fs - {"amount", "market_value"}))


def _check_keys(path_name: str, section: str, table, valid) -> None:
    for k in table:
        if k not in valid:
            raise ValueError(
                f"generic importer: {path_name}: unknown [{section}].{k}."
                f"{_did_you_mean(k, valid)} Valid keys: "
                f"{', '.join(valid)}. An unknown key used to be ignored "
                f"silently (and its column never read).")


class MappingAmbiguity(ValueError):
    """A CSV whose header fits two layouts' signatures."""


class LayoutError(ValueError):
    """A candidate layout (or the folder's generic.toml) that cannot be
    read or carries an invalid [signature]: refused for every file it
    could have read, naming it, rather than left silently inert."""


class MappingChoice:
    """How a CSV's mapping was chosen (`effective_mapping`): `path` the
    mapping file (None when nothing describes the file), `how` one of
    'sidecar', 'shared', 'signature', 'layout' or '', `name` the layout
    the file stands for (its own [signature], or the `[layout]` it
    names), `deps` every file whose presence or content decides the
    choice or the columns (the parse cache depends on them)."""
    __slots__ = ("path", "how", "name", "deps")

    def __init__(self, path, how, name, deps):
        self.path, self.how, self.name, self.deps = path, how, name, list(deps)

    @classmethod
    def refused(cls, csv_path: Path) -> "MappingChoice":
        """The choice a caller records when the importer refuses one (a
        dangling sidecar, two fitting layouts): the sidecar, which the
        parse then reports in its own words."""
        csv_path = Path(csv_path)
        sidecar = csv_path.with_name(csv_path.name + ".toml")
        return cls(sidecar, "sidecar", "", [sidecar, csv_path.parent / "generic.toml"])

    def manifest(self) -> str:
        """The choice as the parse manifest records it: the mapping file's
        name (`-` when none exists) and the layout it stands for."""
        return ((self.path.name if self.path and self.path.exists() else "-")
                + (f"@{self.name}" if self.name else ""))


def layout_dirs_of(project: Optional[Path]) -> List[Path]:
    """Where a project's named layouts are looked for, in order: its
    `layouts/` (none when the project is unknown), the user's folder
    (TAXJSON_LAYOUTS when set (empty: no user folder, the tests'
    setting; the shipped ones are still read), else
    ~/.config/taxjson/layouts) and the ones taxjson ships
    (`BUILTIN_LAYOUTS`, weighed only when no other fits a file)."""
    from taxjson.lib.project_layout import user_config_dir
    out = [Path(project) / LAYOUTS_DIR] if project else []
    user = os.environ.get(ENV_LAYOUTS)
    if user is None:
        out.append(user_config_dir() / LAYOUTS_DIR)
    elif user.strip():
        out.append(Path(user.strip()).expanduser())
    return out + [BUILTIN_LAYOUTS]


def is_builtin_layout(path: Path) -> bool:
    """A layout file taxjson ships (`BUILTIN_LAYOUTS`)."""
    return Path(path).resolve().parent == BUILTIN_LAYOUTS


def shipped_layout_hint(csv_path: Path) -> str:
    """For a file read with a layout taxjson ships (chosen by its
    signature or named by a sidecar): where an edited copy goes, since
    the shipped file is not the user's to edit; else ""."""
    try:
        choice = effective_mapping(csv_path, lenient=True)
    except (ValueError, OSError):
        return ""
    lay = next((p for p in [choice.path, *choice.deps]
                if p is not None and p.suffix == ".toml" and is_builtin_layout(p)), None)
    if lay is None:
        return ""
    # Named inside the package, not by the install's path: the line is
    # captured, and its bytes stay the same wherever taxjson is installed
    # (TJ-2578, TJ-2590).
    return (f" The file is read with the layout taxjson ships, {lay.stem} "
            f"(the taxjson package's data/{LAYOUTS_DIR}/{lay.name}): copy it "
            f"to the project's {LAYOUTS_DIR}/{lay.name} (or the user's "
            f"folder) and edit the copy; a layout of that name wins over "
            f"the shipped one.")


def layout_dirs(csv_path: Path, project: Optional[Path] = None) -> List[Path]:
    """The layout folders for a CSV: its project's (`project` when the
    caller knows it, else project_layout.project_of_input: the one
    TAXJSON_PROJECT_ROOT names when the file is in its inputs folder, own
    or shared, else the folder above inputs/), the user's and the ones
    taxjson ships (`layout_dirs_of`)."""
    from taxjson.lib.project_layout import project_of_input
    return layout_dirs_of(project or project_of_input(csv_path))


def layout_files(dirs: List[Path]) -> List[Path]:
    """Every layout file in `dirs`, each folder's by name, a name's first
    folder shadowing the next's (the project's over the user's)."""
    out: List[Path] = []
    seen = set()
    for d in dirs:
        try:
            files = sorted(Path(d).glob("*.toml"))
        except OSError:
            continue
        out += [p for p in files if p.stem not in seen]
        seen |= {p.stem for p in files}
    return out


def _layout_file(name, csv_path: Path, mapping_name: str,
                 project: Optional[Path] = None) -> Path:
    """The file of the layout `name` (letters, digits, `-`, `_`), the
    first of `layout_dirs` that has it; refused naming the folders
    searched when none does."""
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError(
            f"generic importer: {mapping_name}: [layout] name = {name!r} — a "
            f"layout's name is letters, digits, - and _ (its file is "
            f"{LAYOUTS_DIR}/<name>.toml).")
    dirs = layout_dirs(csv_path, project)
    for d in dirs:
        if (d / f"{name}.toml").is_file():
            return d / f"{name}.toml"
    raise ValueError(
        f"generic importer: {mapping_name}: no layout {name!r} — looked for "
        f"{name}.toml in {' and '.join(str(d) for d in dirs)}.")


_HEAD_BYTES = 65536


# The header is the first record, within the first HEADER_WITHIN, that
# holds every name its reader looks for (the mapping's columns for the
# parse, a layout's [signature] for detection): an export may open with
# a title or the account's details, as Coinbase's and Webull's do. The
# records before it are not read, nor counted (Coinbase's rule).
HEADER_WITHIN = 20


def header_index(rows, wanted) -> Optional[int]:
    """The index, in `rows` (records, each a list of cells), of the first
    of the first HEADER_WITHIN whose cells (trimmed, lower-cased) hold
    every `wanted` name; None when none does or nothing is wanted (a
    reader then takes the first record, as before, and names what is
    missing)."""
    want = {str(w).strip().lower() for w in wanted if str(w).strip()}
    if want:
        for i, row in enumerate(rows[:HEADER_WITHIN]):
            if want <= {(c or "").strip().lower() for c in row}:
                return i
    return None


def _first_records(text: str) -> List[List[str]]:
    """The first HEADER_WITHIN records of `text`, where its header is
    looked for, split as the parse's reader splits them (io.StringIO's
    own newline: a lone carriage return in a cell is the reader's error,
    never a record break); those before a record the reader refuses, when
    one does (the parse names that record)."""
    rows: List[List[str]] = []
    try:
        for row in csv.reader(io.StringIO(text)):
            rows.append(row)
            if len(rows) >= HEADER_WITHIN:
                break
    except csv.Error:
        pass
    return rows


def _header_reader(text: str, wanted):
    """(a DictReader over `text` whose fieldnames are its header record,
    `header_index`'s; the physical lines before that record, which its
    `line_num` leaves out: a message adds them to name the file's own
    line; the records above it, each with the line it ends on, which the
    parse checks are no rows of data)."""
    skip = header_index(_first_records(text), wanted) or 0
    f = io.StringIO(text)
    before = csv.reader(f)
    above = []
    for _ in range(skip):
        row = next(before, None)
        above.append((before.line_num, row or []))
    return csv.DictReader(f), before.line_num, above


def preamble_records(csv_path: Path, text: str) -> int:
    """The records of `text` (the export at `csv_path`) above its header,
    as the parse finds it (`header_index` with the mapping's columns):
    records no parse reads, which `tjs redact` blanks whole (a title
    naming the holder, an `Account Number,...` line). Records, not
    lines, so a reader that splits lines otherwise counts the same. 0
    when no mapping describes the file (the folder's generic.toml is the
    parse's fallback for a file routed to it, not a description of a
    broker's own export beside it) or the mapping cannot be read."""
    try:
        if effective_mapping(Path(csv_path)).path is None:
            return 0
        cols = _load_mapping(Path(csv_path)).get("columns") or {}
    except (ValueError, OSError):
        return 0
    return header_index(_first_records(text), cols.values()) or 0


def _refuse_rows_above(above, fieldnames, date_fmt: str, shown: str,
                       header_line: int) -> None:
    """A record above the header that reads as a row of data (three
    cells or more, one a date in `[formats].date`) is refused: the rows
    of an older export pasted above, under a header lacking a mapped
    column, were dropped with no word (a title or an account line has
    neither)."""
    for line, row in above:
        cells = [c.strip() for c in row if c and c.strip()]
        if len(cells) < 3:
            continue
        for c in cells:
            try:
                datetime.strptime(c, date_fmt)
            except ValueError:
                continue
            raise ValueError(
                f"generic importer: {shown} line {line}: a row above the "
                f"header (line {header_line}) reads as data ({len(cells)} "
                f"cells, the date {c!r}) — the rows of an older export "
                f"whose header lacks a mapped column? Rows above the header "
                f"are not read: save each export as its own file (the header "
                f"is the first of the first {HEADER_WITHIN} rows holding "
                f"every mapped column: {', '.join(fieldnames)}).")


def _head_records(csv_path: Path) -> List[List[str]]:
    """The file's `_first_records`, where its header is looked for; []
    when the file cannot be read (the parse says why). The first 64 KB
    are read, cut at their last line break so no character is split (a
    UTF-16 break is two bytes); a header longer than that fits nothing."""
    try:
        with open(csv_path, "rb") as fh:
            data = fh.read(_HEAD_BYTES)
        if len(data) == _HEAD_BYTES:
            if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
                nl = (data.rfind(b"\n\x00") if data[:2] == b"\xff\xfe"
                      else data.rfind(b"\x00\n"))
                data = data[:nl + 2] if nl > 1 else data
            else:
                nl = data.rfind(b"\n")
                data = data[:nl + 1] if nl > 0 else data
        text = decode_broker_text(data, shown_name(csv_path))
    except (OSError, UnicodeDecodeError, BrokerageParseError):
        return []
    return _first_records(text)


def _signature_of(path: Path, how: str) -> Optional[List[str]]:
    """A candidate mapping file's [signature] headers, trimmed and
    lower-cased, or None when it has no [signature]. A layout that cannot
    be read, or a [signature] the load would refuse (`_check_signature`),
    is LayoutError here, at detection: a layout is only ever chosen by
    its signature, so the load's check would never reach it, and the run
    would end with the file unexplained."""
    try:
        doc = read_toml(path)
    except (OSError, UnicodeDecodeError, ValueError, *TOML_ERRORS) as e:
        if how != "layout":
            return None     # the folder's mapping: the parse says why
        raise LayoutError(
            f"generic importer: the layout {path.name} ({path.parent}) "
            f"cannot be read: {e}") from e
    sig = doc.get("signature") if isinstance(doc, dict) else None
    if sig is None:
        return None
    try:
        _check_signature(path.name, sig)
    except ValueError as e:
        raise LayoutError(f"{e} ({path.parent})") from e
    return [h.strip().lower() for h in sig["headers"]]


def _signed_mappings(csv_path: Path,
                     cands: List[Tuple[Path, str]]) -> List[Tuple[Path, str]]:
    """The candidate mapping files (`_candidate_mappings`) whose
    [signature] fits the CSV's header (every listed header present, case
    and surrounding spaces ignored), as (file, how), in the candidates'
    order: the folder's generic.toml first, then the layouts."""
    signed = []
    for p, how in cands:
        sig = _signature_of(p, how) if p.is_file() else None
        if sig:
            signed.append((p, how, sig))
    if not signed:
        return []               # the file is not read for nothing
    rows = _head_records(csv_path)          # read once, for every signature
    return [(p, how) for p, how, sig in signed
            if header_index(rows, sig) is not None]


def _candidate_mappings(csv_path: Path,
                        project: Optional[Path]) -> List[Tuple[Path, str]]:
    """The folder's generic.toml, then every layout file (`layout_files`
    over `layout_dirs`)."""
    return ([(Path(csv_path).parent / "generic.toml", "signature")]
            + [(p, "layout") for p in layout_files(layout_dirs(csv_path, project))])


def _named_layout(choice: MappingChoice, csv_path: Path,
                  project: Optional[Path]) -> MappingChoice:
    """The choice with the `[layout]` its file names, when it names one:
    the layout's file joins the deps (the load reads it in the file's
    stead). A layout chosen by its own signature names none (the load
    refuses a `[layout]` in a layout); a file that cannot be read is
    left to the load's message."""
    if choice.how == "layout":
        return choice
    try:
        doc = read_toml(choice.path)
    except (OSError, UnicodeDecodeError, ValueError, *TOML_ERRORS):
        return choice
    lay = doc.get("layout") if isinstance(doc, dict) else None
    name = lay.get("name") if isinstance(lay, dict) else None
    if not isinstance(name, str) or not name:
        return choice
    try:
        lp = _layout_file(name, csv_path, choice.path.name, project)
    except ValueError:
        return MappingChoice(choice.path, choice.how, name, choice.deps)
    return MappingChoice(choice.path, choice.how, name, choice.deps + [lp])


def _stat(p: Path) -> tuple:
    try:
        st = p.stat()
        return (str(p), st.st_mtime_ns, st.st_size)
    except OSError:
        return (str(p), None, p.is_symlink())


def _choice_state(csv_path: Path, cands: List[Tuple[Path, str]]) -> tuple:
    """What `effective_mapping`'s answer depends on, as a key: the file,
    its sidecar, the folder's generic.toml and every layout file (each by
    mtime and size), the layout folders themselves."""
    sidecar = csv_path.with_name(csv_path.name + ".toml")
    return (_stat(csv_path), _stat(sidecar),
            tuple(_stat(p) for p, _how in cands))


def effective_mapping(csv_path: Path, *, lenient: bool = False,
                      project: Optional[Path] = None) -> MappingChoice:
    """The mapping that applies to `csv_path`, in order: its sidecar
    `<csv>.toml` (a dangling link is refused: falling back to the shared
    mapping parsed the CSV with another file's columns, audit A2-0299);
    the folder's generic.toml for a generic_ file; the folder's
    generic.toml when its [signature] fits the file's header; the layout
    whose [signature] fits (two fitting layouts: MappingAmbiguity, a
    sidecar picks). The chosen file may name a `[layout]` it stands for.
    `lenient`: a refusal other than an ambiguity or a bad layout (the
    dangling sidecar) counts as the sidecar, `MappingChoice.refused`, for
    a caller that records the choice and leaves the message to the parse.
    `project`: the project whose layouts apply, when the caller knows it
    (a frozen year judging its shared copies); else the file's. The
    answer is cached on the files' state: the run asks several times per
    file."""
    csv_path = Path(csv_path)
    cands = _candidate_mappings(csv_path, project)
    try:
        return _cached_choice(str(csv_path), _choice_state(csv_path, cands),
                              tuple(cands), str(project) if project else "")
    except (MappingAmbiguity, LayoutError):
        raise
    except ValueError:
        if lenient:
            return MappingChoice.refused(csv_path)
        raise


@lru_cache(maxsize=1024)
def _cached_choice(csv_str: str, _state: tuple, cands: tuple,
                   project: str) -> MappingChoice:
    return _effective_mapping(Path(csv_str), list(cands),
                              Path(project) if project else None)


def _effective_mapping(csv_path: Path, cands: List[Tuple[Path, str]],
                       project: Optional[Path]) -> MappingChoice:
    sidecar = csv_path.with_name(csv_path.name + ".toml")
    shared = csv_path.parent / "generic.toml"
    deps = [sidecar, shared]
    if sidecar.is_symlink() and not sidecar.exists():
        raise ValueError(
            f"generic importer: the mapping {sidecar.name} for "
            f"{shown_name(csv_path)} is a link to a file that does not exist — "
            f"fix or remove it. (The shared generic.toml is NOT used in "
            f"its place: it describes other files' columns.)")
    if sidecar.exists():
        return _named_layout(MappingChoice(sidecar, "sidecar", "", deps),
                             csv_path, project)
    if csv_path.name.lower().startswith("generic_") and shared.is_file():
        return _named_layout(MappingChoice(shared, "shared", "", deps),
                             csv_path, project)
    fits = _signed_mappings(csv_path, cands)
    # The folder's generic.toml first: the layouts are weighed only when
    # it does not fit; the ones taxjson ships only when no project or
    # user layout does (a user's own layout for the export wins, never an
    # ambiguity).
    if not (fits and fits[0][1] == "signature"):
        own = [f for f in fits if not is_builtin_layout(f[0])]
        fits = own or fits
    layouts = ([] if fits and fits[0][1] == "signature"
               else [p for p, how in fits if how == "layout"])
    if len(layouts) > 1:
        raise MappingAmbiguity(
            f"generic importer: {shown_name(csv_path)}: its header fits "
            f"{len(layouts)} layouts ({', '.join(p.stem for p in layouts)}) — "
            f"refusing to guess; a sidecar {sidecar.name} holding `[layout] "
            f"name = \"<one of them>\"` picks, or narrow a signature.")
    if fits:
        p, how = fits[0]
        name = p.stem if how == "layout" else ""
        return _named_layout(
            MappingChoice(p, how, name, deps + ([p] if how == "layout" else [])),
            csv_path, project)
    return MappingChoice(None, "", "", deps)


def mapping_path(csv_path: Path) -> Path:
    """The mapping file that applies to `csv_path` (`effective_mapping`),
    else the folder's `generic.toml` (which then does not exist: the
    load names what to write)."""
    return effective_mapping(csv_path).path or Path(csv_path).parent / "generic.toml"


def mapping_documents(csv_path: Path, project: Optional[Path] = None
                      ) -> Optional[List[Tuple[str, dict]]]:
    """The mapping a CSV is read with, as documents: (file name, the
    TOML as read) for the chosen file and the layout it names, so two
    copies of a file compare by what their mapping says, a layout edit
    included (the frozen year's drift check). None when the file has no
    mapping. Raises ValueError as the load does for a file it cannot
    read."""
    choice = effective_mapping(csv_path, project=project)
    if choice.path is None:
        return None
    out = [(choice.path.name, _read_mapping_file(choice.path, layout=choice.how == "layout"))]
    lay = out[0][1].get("layout")
    if isinstance(lay, dict):
        lp = _layout_file(lay.get("name"), csv_path, choice.path.name, project)
        out.append((lp.name, _read_mapping_file(lp, layout=True)))
    return out


def mapping_bytes(csv_path: Path, project: Optional[Path] = None) -> Optional[bytes]:
    """The mapping a CSV is read with, as bytes: the chosen file's and,
    after a NUL, the layout's it names (its name alone when the layout
    cannot be found); None when the file has no mapping. Two copies with
    equal bytes are read alike: the drift check's fast path."""
    choice = effective_mapping(csv_path, project=project)
    if choice.path is None:
        return None
    out = choice.path.read_bytes()
    if choice.how != "layout" and choice.name:
        lay = next((p for p in choice.deps[2:] if p.stem == choice.name), None)
        out += b"\0" + (lay.read_bytes() if lay is not None else choice.name.encode())
    return out


def action_key(label) -> str:
    """An action label as rows and [actions] keys are matched: case and
    surrounding spaces ignored (backlog TJ-0382)."""
    return str(label).strip().upper()


def _read_mapping_file(path: Path, *, layout: bool = False) -> Dict[str, Any]:
    """A mapping file read and its sections checked: TOML, UTF-8, the
    sections it may have, each a table (the [[classify]] rules a list
    of them). `layout`: the file is a layout (named, or chosen by its
    signature), which describes the columns itself and never names a
    layout."""
    try:
        # A mapping saved with a BOM failed as "Invalid statement (at line
        # 1, column 1)" (S038-04): toml_io's parser drops the mark.
        mapping = read_toml(path)
    except OSError as e:
        raise ValueError(f"generic importer: cannot read the mapping "
                         f"{shown_name(path)}: {e.strerror or e}")
    except UnicodeDecodeError as e:
        # It was reported against the CSV, at an offset in the .toml
        # (A2-1452).
        raise ValueError(
            f"generic importer: {shown_name(path)}: not UTF-8 text (byte "
            f"0x{e.object[e.start]:02x} at offset {e.start}) — save the "
            f"mapping as UTF-8")
    except TOML_ERRORS as e:
        raise ValueError(f"generic importer: {shown_name(path)}: bad TOML: {e}")
    for sec, val in mapping.items():
        if sec not in _SECTIONS:
            raise ValueError(
                f"generic importer: {shown_name(path)}: unknown section "
                f"[{sec}].{_did_you_mean(sec, _SECTIONS)} Valid sections: "
                f"{', '.join('[' + x + ']' for x in _SECTIONS)}.")
        if sec == "classify" and not isinstance(val, list):
            raise ValueError(
                f"generic importer: {shown_name(path)}: classify is written "
                f"as [[classify]] tables, one per rule, got {val!r}")
        if sec != "classify" and not isinstance(val, dict):
            raise ValueError(
                f"generic importer: {shown_name(path)}: {sec} must be a "
                f"[{sec}] table, got {val!r}")
    if layout and "layout" in mapping:
        raise ValueError(
            f"generic importer: {shown_name(path)}: a layout does not name "
            f"a layout — describe the columns here.")
    return mapping


def _load_mapping(csv_path: Path) -> Dict[str, Any]:
    sidecar = csv_path.with_name(csv_path.name + ".toml")
    choice = effective_mapping(csv_path)
    path = choice.path or csv_path.parent / "generic.toml"
    if not path.exists():
        raise ValueError(
            f"generic importer: no mapping for {shown_name(csv_path)} — write "
            f"{sidecar.name} (or a shared generic.toml in the same folder, "
            f"or a layout whose [signature] fits the file's header). See "
            f"examples/generic_wealthsimple.toml for a template.")
    mapping = _read_mapping_file(path, layout=choice.how == "layout")
    is_layout = choice.how == "layout"
    if "layout" in mapping:
        # The file stands for a named layout: read in its stead, its
        # own [broker] keys winning; nothing else beside the name.
        _check_keys(shown_name(path), "layout", mapping["layout"], _LAYOUT_KEYS)
        if "name" not in mapping["layout"]:
            raise ValueError(
                f"generic importer: {shown_name(path)}: the [layout] table "
                f"needs name = \"<layout>\" ({LAYOUTS_DIR}/<layout>.toml).")
        extra = sorted(set(mapping) - {"layout", "broker"})
        if extra:
            raise ValueError(
                f"generic importer: {shown_name(path)}: a mapping that names a "
                f"[layout] describes nothing else — move [{extra[0]}] into the "
                f"layout's file (its own [broker] keys override the layout's).")
        mine = mapping.get("broker") or {}
        if isinstance(mine, dict) and "verified" in mine:
            # Whether a layout was checked against a real export is the
            # layout's to say (TJ-2573), not a file that stands for it.
            raise ValueError(
                f"generic importer: {shown_name(path)}: [broker].verified is "
                f"the layout's own key — a mapping that names a [layout] "
                f"cannot set it; edit a copy of the layout instead.")
        path = _layout_file(mapping["layout"]["name"], csv_path, shown_name(path))
        mapping = _read_mapping_file(path, layout=True)
        is_layout = True
        if mine:
            mapping["broker"] = {**(mapping.get("broker") or {}), **mine}
    _check_signature(shown_name(path), mapping.get("signature"))
    cols = mapping.get("columns") or {}
    # Every message names the mapping as shown_name does: a sidecar
    # named for the account (99900001.csv.toml) printed the id
    # unmasked (backlog TJ-0235).
    _check_keys(shown_name(path), "columns", cols, _COLUMN_KEYS)
    _check_keys(shown_name(path), "formats", mapping.get("formats") or {},
                _FORMAT_KEYS)
    _check_keys(shown_name(path), "defaults", mapping.get("defaults") or {},
                _DEFAULT_KEYS)
    _check_keys(shown_name(path), "broker", mapping.get("broker") or {},
                _BROKER_KEYS)
    if "name" in (mapping.get("broker") or {}):
        raw_name = mapping["broker"]["name"]
        bname = raw_name.strip().lower() if isinstance(raw_name, str) else ""
        if not _BROKER_NAME_RE.match(bname):
            raise ValueError(
                f"generic importer: {shown_name(path)}: [broker].name must be a "
                f"short name of letters, digits, '-' or '_' (e.g. "
                f"\"wealthsimple\"), got {raw_name!r}")
        mapping["broker"]["name"] = bname
    if "account" in (mapping.get("broker") or {}):
        acct = mapping["broker"]["account"]
        if not isinstance(acct, str) or not acct.strip():
            raise ValueError(
                f"generic importer: {shown_name(path)}: [broker].account must be "
                f"the broker account id as a quoted string, got a "
                f"{type(acct).__name__}")
        mapping["broker"]["account"] = acct.strip()
    mapping["_unverified"] = _unverified_label(path, mapping, is_layout)
    # Every [defaults] / [formats] value is text: a TOML true, 5 or a
    # list was str()-coerced and booked (currency 'TRUE', symbol
    # "['SAMPLG'].TRUE") or crashed strptime (audit A2-1081).
    for sec in ("defaults", "formats"):
        for k, v in (mapping.get(sec) or {}).items():
            if not isinstance(v, str):
                raise ValueError(
                    f"generic importer: {shown_name(path)}: [{sec}].{k} must be "
                    f"a quoted string, got {v!r}")
    for k, v in cols.items():
        if not isinstance(v, str):
            raise ValueError(
                f"generic importer: {shown_name(path)}: [columns].{k} must be "
                f"a quoted header NAME, got {v!r}")
    defaults = mapping.get("defaults") or {}
    # Two logical fields on ONE header: fee = "Net" next to amount =
    # "Net" booked the whole trade value as commission.
    by_header: Dict[str, List[str]] = {}
    for k, v in cols.items():
        by_header.setdefault(v.strip().lower(), []).append(k)
    shared = {h: ks for h, ks in by_header.items() if _shared_wrongly(ks)}
    if shared:
        detail = "; ".join(f"{', '.join(ks)} -> {h!r}"
                           for h, ks in sorted(shared.items()))
        raise ValueError(
            f"generic importer: {shown_name(path)}: several [columns] fields "
            f"map to the SAME CSV header ({detail}) — each logical field "
            f"needs its own column (only a transfer's book_value or "
            f"market_value may name the amount's, and description any). "
            f"A fee/amount mix-up "
            f"books wrong money silently; fix the mapping.")
    if "currency" not in cols and not str(
            defaults.get("currency", "")).strip():
        raise ValueError(
            f"generic importer: {shown_name(path)}: no currency — map "
            f"[columns].currency or set [defaults].currency (e.g. "
            f"\"CAD\"). There is no implicit USD default: a CAD account "
            f"read as USD is converted at the wrong rate.")
    options = mapping.get("options") or {}
    for k, v in options.items():
        if k not in _OPTIONS:
            raise ValueError(
                f"generic importer: {shown_name(path)}: unknown [options].{k}."
                f"{_did_you_mean(k, _OPTIONS)} (valid: "
                f"{', '.join(_OPTIONS)})")
        if not isinstance(v, bool):
            raise ValueError(
                f"generic importer: {shown_name(path)}: [options].{k} must be "
                f"true or false, got {v!r}")
    if "date" not in cols:
        raise ValueError(f"generic importer: {shown_name(path)}: "
                         f"[columns].date is required")
    if "action" not in cols and "action" not in defaults:
        raise ValueError(f"generic importer: {shown_name(path)}: map "
                         f"[columns].action or set [defaults].action")
    mapping["_classify"] = _load_classify(shown_name(path),
                                          mapping.get("classify"), cols)
    mapping["_extract"] = _load_extract(shown_name(path),
                                        mapping.get("extract"), cols,
                                        mapping.get("formats") or {})
    if not {"symbol", "option"} & (set(cols) | set(defaults)
                                    | set(mapping["_extract"])):
        raise ValueError(f"generic importer: {shown_name(path)}: map "
                         f"[columns].symbol, set [defaults].symbol or give "
                         f"an [extract].symbol pattern")
    _check_actions(shown_name(path), mapping.get("actions") or {})
    origins = {t: "a [[classify]] rule" for _rx, _over, t, _act in mapping["_classify"]}
    origins.update({t: "[actions]" for t in (mapping.get("actions") or {}).values()})
    _check_targets(shown_name(path), origins, set(cols) | set(mapping["_extract"]))
    mapping["_path"] = shown_name(path)
    return mapping


def _unverified_label(path: Path, mapping: Dict[str, Any], is_layout: bool) -> str:
    """What the parse names when the mapping says `[broker] verified =
    false` (`_say_unverified`, TJ-2573): the layout by its name, else the
    mapping file; "" when it says nothing or true. Not a boolean: refused."""
    verified = (mapping.get("broker") or {}).get("verified", True)
    if not isinstance(verified, bool):
        raise ValueError(
            f"generic importer: {shown_name(path)}: [broker].verified must be "
            f"true or false, got {verified!r}")
    if verified:
        return ""
    return f"layout {path.stem}" if is_layout else f"mapping {shown_name(path)}"


def _check_signature(path_name: str, sig) -> None:
    """[signature] at load time: `headers`, a list of two or more header
    names (one would fit most exports), each a non-blank string."""
    if sig is None:
        return
    _check_keys(path_name, "signature", sig, _SIGNATURE_KEYS)
    hs = sig.get("headers")
    if (not isinstance(hs, list) or len(hs) < 2
            or not all(isinstance(h, str) and h.strip() for h in hs)):
        raise ValueError(
            f"generic importer: {path_name}: [signature] headers must list "
            f"two or more header names (quoted strings) that identify the "
            f"export, got {hs!r}.")


def _check_actions(path_name: str, actions) -> None:
    """[actions] at load time: every target known, and no two keys that
    are one label (a row's action is matched ignoring case and
    surrounding spaces) with two meanings — the last one used to win
    silently (backlog TJ-0382)."""
    for raw, target in actions.items():
        if target not in _VALID_TARGETS:
            raise ValueError(
                f"generic importer: {path_name}: [actions] {raw!r} maps "
                f"to unknown target {target!r} (valid: "
                f"{', '.join(_VALID_TARGETS)})")
    seen_keys: Dict[str, Any] = {}
    for raw, target in actions.items():
        prev = seen_keys.setdefault(action_key(raw), (raw, target))
        if prev[1] != target:
            raise ValueError(
                f"generic importer: {path_name}: [actions] "
                f"{prev[0]!r} = {prev[1]!r} and {raw!r} = {target!r} name "
                f"one action (rows are matched ignoring case and "
                f"surrounding spaces) with two meanings — keep the line "
                f"that is right and delete the other.")


def _regex(path_name: str, what: str, pat) -> "re.Pattern":
    """A mapping's pattern compiled once, at load time, case-insensitive;
    a value that is not a string or does not compile is refused naming
    `what` (the key it came from)."""
    if not isinstance(pat, str) or not pat.strip():
        raise ValueError(
            f"generic importer: {path_name}: {what} must be a quoted "
            f"regular expression, got {pat!r}.")
    try:
        return re.compile(pat, re.IGNORECASE)
    except re.error as e:
        raise ValueError(
            f"generic importer: {path_name}: {what} = {pat!r} is not a "
            f"valid regular expression ({e}).") from e


def _load_classify(path_name: str, rules, cols) -> list:
    """The `[[classify]]` rules, compiled at load time, as (regex, the cell
    it reads, target, the action regex or None) in the mapping's order:
    the first rule whose `match` is found in the row's action cell
    (`over = "action"`, the default) or its description cell (`over =
    "description"`), and whose `action`, when given, is found in the
    action cell too, names the target of a row whose action is not in
    [actions]. Refused here, not row by row: a
    rule that is not a table, an unknown key, a missing or invalid
    `match`, an unknown target, an `over` that names no cell, and a rule
    over the description with no [columns].description mapped."""
    out = []
    for i, rule in enumerate(rules or (), 1):
        at = f"[[classify]] rule {i}"
        if not isinstance(rule, dict):
            raise ValueError(
                f"generic importer: {path_name}: {at} is not a table — each "
                f"rule is a `[[classify]]` header followed by match = "
                f"\"<regex>\" and target = \"<target>\".")
        for k in rule:
            if k not in _CLASSIFY_KEYS:
                raise ValueError(
                    f"generic importer: {path_name}: unknown {at} key "
                    f"{k!r}.{_did_you_mean(k, _CLASSIFY_KEYS)} Valid keys: "
                    f"{', '.join(_CLASSIFY_KEYS)}.")
        rx = _regex(path_name, f"{at} match", rule.get("match"))
        target, over = rule.get("target"), rule.get("over", "action")
        if "target" not in rule:
            raise ValueError(
                f"generic importer: {path_name}: {at} needs a target = "
                f"\"<target>\" line (one of {', '.join(_VALID_TARGETS)}).")
        if target not in _VALID_TARGETS:
            raise ValueError(
                f"generic importer: {path_name}: {at} names unknown target "
                f"{target!r} (valid: {', '.join(_VALID_TARGETS)}).")
        if over not in ("action", "description"):
            raise ValueError(
                f"generic importer: {path_name}: {at} over = {over!r} — a "
                f"rule reads the row's \"action\" cell (the default) or "
                f"its \"description\" cell.")
        if over == "description" and "description" not in cols:
            raise ValueError(
                f"generic importer: {path_name}: {at} reads the description "
                f"cell but [columns].description is not mapped.")
        act = (_regex(path_name, f"{at} action", rule["action"])
               if "action" in rule else None)
        out.append((rx, over, target, act))
    return out


def _load_extract(path_name: str, table, cols, formats) -> Dict[str, Any]:
    """The `[extract]` patterns, compiled at load time, one per field:
    searched in the row's description cell, the capturing group is the
    field's value when its own column is unmapped or blank (a mapped,
    non-blank cell wins). `ratio` may have two groups, new units then old
    units (`2 FOR 1` is 2, `1 FOR 10` is 0.1). `option` is searched in
    the symbol cell, then the description cell, and names its groups
    (_OPTION_GROUPS): the contract it reads is the row's symbol
    (`_option_symbol`). Refused here: a field not in the list, a pattern
    that does not compile or has the wrong groups, the table with no
    cell to read mapped, and an option_expiry format naming no year,
    month or day."""
    table = table or {}
    _check_keys(path_name, "extract", table, _EXTRACT_KEYS)
    if set(table) - {"option"} and "description" not in cols:
        raise ValueError(
            f"generic importer: {path_name}: [extract] reads the description "
            f"cell but [columns].description is not mapped.")
    if "option" in table and not {"symbol", "description"} & set(cols):
        raise ValueError(
            f"generic importer: {path_name}: [extract].option reads the "
            f"symbol cell, then the description cell, but neither "
            f"[columns].symbol nor [columns].description is mapped.")
    fmt = formats.get("option_expiry", _OPTION_EXPIRY_FMT)
    if "option" in table and not all(re.search(d, fmt) for d in (
            "%[yY]", "%[mbB]", "%d")):
        raise ValueError(
            f"generic importer: {path_name}: [formats].option_expiry = "
            f"{fmt!r} must name the expiry's year (%y or %Y), month (%m, "
            f"%b or %B) and day (%d).")
    out = {}
    for field, pat in table.items():
        rx = _regex(path_name, f"[extract].{field}", pat)
        if field == "option":
            missing = [g for g in _OPTION_GROUPS if g not in rx.groupindex]
            if missing:
                raise ValueError(
                    f"generic importer: {path_name}: [extract].option = "
                    f"{pat!r} has no named group "
                    f"{', '.join(f'(?P<{g}>...)' for g in missing)} — it "
                    f"needs root, expiry, right and strike (e.g. "
                    f"'^(?P<right>CALL|PUT) (?P<root>[A-Z.]+) "
                    f"(?P<expiry>\\d\\d/\\d\\d/\\d\\d) (?P<strike>[\\d.]+)$').")
            out[field] = rx
            continue
        want = (1, 2) if field == "ratio" else (1,)
        if rx.groups not in want:
            raise ValueError(
                f"generic importer: {path_name}: [extract].{field} = {pat!r} "
                f"has {rx.groups} capturing group(s); it needs "
                f"{'one, or two (new units, then old units)' if field == 'ratio' else 'exactly one'}"
                f" — the group is the value.")
        out[field] = rx
    return out


def _check_targets(path_name: str, targets: Dict[str, str], cols) -> None:
    """The columns each mapped target books from, required at load time:
    a target without its column would book every such row as 0 (or
    nothing), and a mapping is checked once, not row by row. `targets`
    maps each target to where it came from ([actions], a [[classify]]
    rule); `cols` holds the mapped columns and the [extract] fields."""
    def _from(t):
        return targets[t]

    def _missing(col):
        return (f"[columns].{col} is not mapped"
                + (f" (nor an [extract].{col} pattern)"
                   if col in _EXTRACT_KEYS else ""))
    income = sorted(t for t in targets if t in _INCOME_TARGETS)
    if income and "amount" not in cols:
        raise ValueError(
            f"generic importer: {path_name}: {_from(income[0])} maps to "
            f"{', '.join(income)} but [columns].amount is not mapped — "
            f"income/tax/fee rows book the amount cell, and without it "
            f"every such row would be booked as 0.")
    if "rename" in targets and "symbol_new" not in cols:
        raise ValueError(
            f"generic importer: {path_name}: {_from('rename')} maps to rename "
            f"but {_missing('symbol_new')} — a rename row names "
            f"the new ticker there (a ratio alone is split).")
    if "split" in targets and "ratio" not in cols:
        raise ValueError(
            f"generic importer: {path_name}: {_from('split')} maps to split "
            f"but {_missing('ratio')} — a split row's ratio "
            f"(new units per old unit) is its own cell; the quantity cell "
            f"is the shares received or held, never the ratio.")
    for t, col, what in (("transfer_in", "quantity", "the units moved"),
                         ("transfer_out", "quantity", "the units moved"),
                         ("opening", "quantity", "the units held"),
                         ("opening", "amount", "the position's total cost"),
                         ("roc", "amount", "the capital returned"),
                         ("adjust", "amount", "the cost adjustment")):
        if t in targets and col not in cols:
            raise ValueError(
                f"generic importer: {path_name}: {_from(t)} maps to {t} but "
                f"{_missing(col)} — a {t} row books {what} "
                f"from that cell.")
    trades = [x for x in ("buy", "sell", *_ASSIGN_SIDE) if x in targets]
    if trades:
        _t = trades[0]
        if "quantity" not in cols:
            raise ValueError(
                f"generic importer: {path_name}: {_from(_t)} maps to {_t} "
                f"but {_missing('quantity')}.")
        if "price" not in cols and "amount" not in cols:
            raise ValueError(
                f"generic importer: {path_name}: {_from(_t)} maps to {_t} "
                f"but neither [columns].price nor [columns].amount "
                f"is mapped — the cost/proceeds would be the fee alone.")



def mapping_broker_name(csv_path: Path) -> Optional[str]:
    """The `[broker].name` of the mapping that applies to `csv_path`
    (lower case), or None when it names no broker. Raises ValueError
    like the importer for a missing or invalid mapping."""
    return ((_load_mapping(csv_path).get("broker") or {}).get("name")
            or None)


def unverified_mapping(csv_path: Path) -> str:
    """The mapping `csv_path` is read with, named (`layout wealthsimple`),
    when it says `[broker] verified = false` (never run on a real export,
    TJ-2573); "" when it does not, when no mapping reads the file or when
    the mapping cannot be loaded (the parse says why)."""
    try:
        return _load_mapping(Path(csv_path)).get("_unverified") or ""
    except (ValueError, OSError):
        return ""


def _check_trade_row(where: str, target: str, qty: float, price: float,
                     amount: float, fee: float, mult: float,
                     allow_large_fees: bool, fee_mapped: bool = True,
                     is_future: bool = False,
                     amount_given: bool = False,
                     fee_sign: Optional[str] = None,
                     price_given: bool = False) -> float:
    """Refuse a BUY/SELL row whose numbers don't hang together — the
    signature of a mis-mapped column. See the module docstring. Returns
    the fee to book, SIGNED (positive = charged, negative = a credited
    rebate): the mapped fee, or — with no fee column mapped — the
    commission inferred from |amount| vs qty x price (S056-23).

    Direction of a mapped fee (audit A2-0626 / A2-1079; abs() booked
    every ECN rebate as a charge): with an amount and a price, the
    amount decides — the reading (charge or rebate) closer to the cash
    the row shows wins; otherwise an explicit `fee_sign` ("cash":
    negative = charged, "charged": positive = charged) does; with
    neither, the cell is a charge whatever its sign (the importer's
    long-standing reading, kept so a file of positive commissions is
    never flipped into rebates)."""
    # A futures contract size is never guessed (S012-01): no qty x
    # price comparison is possible, so check against the amount only.
    gross = 0.0 if is_future else abs(qty) * abs(price) * mult
    raw_fee = fee
    fee = abs(fee)
    if fee and amount and gross > 0:
        charge_net = gross + fee if target == "buy" else gross - fee
        rebate_net = gross - fee if target == "buy" else gross + fee
        if abs(abs(amount) - rebate_net) + 0.005 < abs(abs(amount)
                                                       - abs(charge_net)):
            fee = -fee
    elif fee and fee_sign in ("cash", "charged"):
        charged = raw_fee < 0 if fee_sign == "cash" else raw_fee > 0
        if not charged:
            fee = -fee
    hint = ("check the [columns] mapping — is the fee, gross or amount "
            "column mapped to the wrong field?")
    if not fee_mapped and gross > 0 and (
            amount or (amount_given and target == "sell")):
        # A commission-inclusive Net column with no commission column:
        # the gap between the net and qty x price IS the commission.
        # A negative gap (a buy's amount below the gross) is left for
        # the mismatch refusal below. A sale whose stated net is 0 had
        # its whole gross taken by the commission: inferred before the
        # zero-amount refusal below, which refused it (backlog TJ-0989).
        inferred = (abs(amount) - gross if target == "buy"
                    else gross - abs(amount))
        if inferred > 0.005:
            fee = M.units(inferred, 6)
    if amount_given and not amount and gross > 0:
        # An explicit 0 in the amount cell is not "no amount" (S057-05):
        # deriving qty x price over it booked a cost or proceeds the
        # row says was never paid, and skipped the cross-check below —
        # for every row of a file whose `amount` is mapped to a column
        # that is always 0. Only a sale whose commission ate the whole
        # gross really nets 0.
        expected = gross + fee if target == "buy" else gross - fee
        if expected > money_tolerance(abs_tol=_ABS_TOL, rel_tol=_REL_TOL,
                                      scale=max(gross, 1.0)):
            raise ValueError(
                f"generic importer: {where}: the amount cell is 0 but "
                f"qty x price{f' x {mult:g}' if mult > 1 else ''} "
                f"{'+' if target == 'buy' else '-'} fee = {M.shown(expected, 2):.2f} "
                f"— a row with no cash (free or journal shares) is not a "
                f"{target}: map its action to skip and enter the real "
                f"cost or proceeds in a .tt file; or is `amount` mapped "
                f"to a column that is always 0? {hint}")
    if gross <= 0 and amount:
        # No price (unmapped, blank or 0) or a future: the qty x price
        # cross-check cannot run, but a fee against the booked total
        # still can — a swapped fee/amount mapping books the commission
        # as the cost (S011-03).
        # (A negative-price future: a charge larger than the amount
        # makes the buy cost money although its notional is received —
        # backlog TJ-0668; the fee-share check below still asks for the
        # allow_large_fees opt-in.)
        # (At a stated zero price an amount equal to the fee is no swap
        # and no large fee: the fee is the row's whole cash, what a blank
        # amount derives. A buy-to-close $0 expiry was refused, a sale
        # asked for the opt-in: backlog TJ-1077, TJ-1121. A share bought
        # at $0 stays refused, as with a blank amount: backlog TJ-1237.
        # A future's fill at 0 too: its notional is 0, its amount its
        # fee, booked so by _zero_price_future: backlog TJ-4558.)
        fee_only = _fee_only_cash(price_given, price, amount, fee)
        if (target == "buy" and fee > 0 and fee >= abs(amount) - 0.005
                and not (fee_only and (mult > 1 or is_future))
                and not (is_future and price < 0)):
            raise ValueError(
                f"generic importer: {where}: fee {M.shown(fee, 2):.2f} is not less "
                f"than the buy's whole amount {M.shown(abs(amount), 2):.2f} — {hint}")
        if fee and abs(fee) > _MAX_FEE_SHARE * abs(amount) \
                and not allow_large_fees and not fee_only:
            raise ValueError(
                f"generic importer: {where}: fee {M.shown(abs(fee), 2):.2f} is "
                f"{abs(fee) / abs(amount):.2%} of the amount {M.shown(abs(amount), 2):.2f} "
                f"(limit {_MAX_FEE_SHARE:.0%}) "
                f"(no price to cross-check qty x price) — {hint} If the "
                f"fee really is that large, set `[options] "
                f"allow_large_fees = true` in the mapping.")
    if fee and gross > 0 and abs(fee) > _MAX_FEE_SHARE * gross \
            and not allow_large_fees:
        raise ValueError(
            f"generic importer: {where}: fee {M.shown(abs(fee), 2):.2f} is "
            f"{abs(fee) / gross:.2%} of the gross {M.shown(gross, 2):.2f} "
            f"(limit {_MAX_FEE_SHARE:.0%}) "
            f"(qty {abs(qty):g} x price {abs(price):g}"
            f"{f' x {mult:g}' if mult > 1 else ''}) — {hint} If the fee really "
            f"is that large (tiny odd-lot trades), set "
            f"`[options] allow_large_fees = true` in the mapping.")
    if amount and gross > 0:
        expected = gross + fee if target == "buy" else gross - fee
        # A sell whose commission exceeds its gross nets NEGATIVE (a
        # penny option close): its amount is fee - gross in magnitude,
        # whichever sign the file writes (S057-02).
        cmp = abs(expected)
        if not money_close(amount, cmp, abs_tol=_ABS_TOL, rel_tol=_REL_TOL,
                           scale=max(cmp, 1.0)):
            raise ValueError(
                f"generic importer: {where}: amount {M.shown(abs(amount), 2):.2f} "
                f"differs from qty x price{f' x {mult:g}' if mult > 1 else ''} "
                f"{'+' if target == 'buy' else '-'} fee = {M.shown(expected, 2):.2f} "
                f"(qty {abs(qty):g}, price {abs(price):g}, fee "
                f"{fee:g}) by more than 1% — {hint}")
        if fee and abs(abs(amount) - gross) < 0.005:
            emit_line(f"warning: generic importer: {where}: amount "
                  f"{M.shown(abs(amount), 2):.2f} equals qty x price exactly while the "
                  f"fee is {M.shown(fee, 2):.2f} — `amount` must be the fee-INCLUSIVE "
                  f"net; is the GROSS column mapped as amount?")
    return fee


def _commission_allowance(qty: float) -> float:
    """The most a row's amount may carry as a commission of its own when
    no fee column states one (back_compute_fee's allowance)."""
    return 10.0 + 2.0 * abs(qty)


def _fee_only_cash(price_given: bool, price: float, amount: float,
                   fee: float, is_future: bool = False) -> bool:
    """A row at a STATED zero price whose only cash is its commission
    (a worthless-share cleanup, an expiry the broker charged for):
    |amount| is the charged fee (backlog TJ-0731 / TJ-0732)."""
    return (price_given and not price and not is_future and fee > 0
            and money_close(amount, fee, abs_tol=_ABS_TOL, rel_tol=_REL_TOL,
                            scale=fee))


def _a_row(target: str) -> str:
    """'a buy row', 'a sell row'; an assignment's leg by its target."""
    return (f"a {target} row" if target in ("buy", "sell")
            else f"a row mapped to {target}")


def _expiry_commission(target: str, qty: float, amount: float, date: str,
                       expiry: Optional[str]) -> float:
    """The commission of an option closed at a stated zero price on (or
    posted up to 7 days after) its expiry, in a file with no fee column:
    the row's amount, when it is a charge (a buy's, or a sale's cash
    out) of a commission's size. Else 0: a sale's positive amount may be
    cash received, a larger one more than a commission. The expiry was
    read as a trade settling T+1 with the charge as proceeds (backlog
    TJ-1035)."""
    if (not expiry or not amount or expiry > date
            or GenericBrokerage._days(expiry, date) > _EXPIRY_POST_DAYS
            or (target == "sell" and amount > 0)
            or abs(amount) > _commission_allowance(qty)):
        return 0.0
    return abs(amount)


def _trade_net(fname: str, target: str, qty: float, price: float,
               amount: float, fee: float, mult: float = 1.0,
               is_future: bool = False, amount_given: bool = False,
               price_given: bool = False) -> float:
    """Fee-inclusive trade total: the amount column when present, else
    derived. A SELL whose commission exceeds its gross (a penny option
    close, a worthless-position cleanup) nets NEGATIVE: the engine
    deducts those negative proceeds (engine _ca_trade_money) and the schema
    accepts them (S017-00). It used to be clamped to 0 — the excess
    commission never reached the loss — or, with an amount column,
    refused as a mis-mapping (S057-02). So does a sale at a stated zero
    price whose amount is its fee: the stated amount booked the charge
    as proceeds received, the blank one as paid (backlog TJ-0731)."""
    gross = 0.0 if is_future else abs(qty) * abs(price) * mult
    if amount or amount_given:
        if target == "sell" and fee > gross and (
                gross > 0 or _fee_only_cash(price_given, price, amount,
                                            fee, is_future)):
            return -abs(amount)
        if is_future and price < 0:
            # A futures price can be negative (WTI, April 2020): the
            # notional's sign is kept, as the IB parser keeps it — a
            # buy receives money, a sale pays it (lib/futures._money
            # books the signed amount). The magnitude flipped the P/L
            # (KNOWN_ISSUES, fixed 2026-10).
            return -abs(amount)
        return abs(amount)
    # `fee` is signed: a rebate (negative) lowers a buy's cost and
    # raises a sale's proceeds (A2-0626).
    return gross + (fee if target == "buy" else -fee)


def _zero_price_future(where: str, target: str, amount: float, fee: float,
                       fee_known: bool) -> Tuple[float, bool]:
    """(net, deferred) of a futures fill at a STATED price of 0: its
    notional is 0, so its amount is its commission or rebate alone (the
    price-sign reading booked a sale's charge as proceeds received:
    backlog TJ-4558). A known fee (the fee cell, or an expiry's
    commission read from a charge: _expiry_commission) is that cash (a
    buy's cost, a sale's cash out), and an amount that is not the fee is
    refused; with none, the file's cash sign decides after the loop
    (`deferred`: _resolve_zero_price_futures)."""
    if not fee_known:
        return abs(amount), True
    if not money_close(abs(amount), abs(fee), abs_tol=_ABS_TOL,
                       rel_tol=_REL_TOL, scale=max(abs(fee), 1.0)):
        raise ValueError(
            f"generic importer: {where}: a futures {target} at a stated price "
            f"of 0 has an amount of {abs(amount):.2f} and a fee of "
            f"{abs(fee):.2f}: at price 0 its only cash is its commission or "
            f"rebate, so neither a cost nor a credit of {abs(amount):.2f} "
            f"fits. Check the amount, the fee and the price against the "
            f"statement.")
    return (fee if target == "buy" else -fee) or 0.0, False


# A futures fill whose direction the finish decides: (the transaction,
# where, target, the amount cell, the signed fee).
_FutFill = Tuple[Dict[str, Any], str, str, float, float]


def _resolve_zero_price_futures(rows: Sequence[_FutFill],
                                trade_amounts: Sequence[Tuple[Any, ...]]
                                ) -> None:
    """Book the futures fills at a stated price of 0 with no fee stated
    (_zero_price_future): a negative amount is a charge paid (a buy's
    paid cost; a sale nets it negative whichever convention the file
    has); a positive one a rebate received in a file that signs its cash
    (a buy row paid is negative), and refused in one that does not. The
    amount is the fee, the notional 0 (backlog TJ-4558)."""
    signed = any(t == "buy" and (a or 0) < 0
                 for _, t, a, _l in trade_amounts) or any(
        t == "buy" and a < 0 for _tx, _w, t, a, _f in rows)
    for tx, w, t, a, _f in rows:
        if a > 0 and not signed:
            raise ValueError(
                f"generic importer: {w}: a futures {t} at a stated price of "
                f"0: its amount ({a:g}) is its commission or rebate alone, "
                f"and the file's amounts carry no sign to tell paid from "
                f"received. Export the amount signed (negative = paid; the "
                f"file's buys then show it), map the fee column, or enter "
                f"the fill in a .tt file.")
        fee = -a or 0.0
        tx["fee"] = fee
        tx["net_amount"] = fee if t == "buy" else -fee
        tx["gross_amount"] = 0.0


def _say_zero_prices(shown: str, rows: List[str]) -> None:
    """The rows at a stated price of 0 carrying cash beyond a fee, said
    once per file: each is booked at its amount, with no quantity x price
    check (the owner's ruling of 2026-11-19, backlog TJ-4559 (1)); in
    `tjs run`'s end-of-run list too (END_OF_RUN_MARK)."""
    if rows:
        n = len(rows)
        emit_line(f"warning: ATTENTION: {END_OF_RUN_MARK}generic importer: "
                  f"{shown}: "
                  f"{n} trade {'row states' if n == 1 else 'rows state'} a "
                  f"price of 0 with cash (the first: {rows[0]}); each is "
                  f"booked at its amount, "
                  f"with no quantity x price check. If the export has no "
                  f"prices, blank the price cells; else fill them in from "
                  f"the statement.")


def _future_notional(target: str, net: float, fee: float) -> float:
    """|qty x price x size| of a futures fill from its signed net (a
    buy's cost, a sale's proceeds) and signed fee (positive = charged):
    a buy costs notional + fee, a sale brings notional - fee."""
    return M.units(abs(net - fee if target == "buy" else net + fee), 6)


def _future_net(target: str, price: float, amount: float,
                fee: float, where: str) -> Tuple[float, bool]:
    """(net, ambiguous) of a futures fill whose amount cell is read as a
    magnitude. Its notional carries the price's sign (a negative-price
    buy receives money), so the net is the reading of |amount| — cost
    or credit — whose notional (net - fee for a buy, net + fee for a
    sale) has that sign. When a commission or rebate larger than the
    amount makes both readings possible, `ambiguous` is True and the
    caller decides from the file's cash sign; the net returned is the
    price-sign reading, the only one before backlog TJ-0668 (a rebate
    crossing a near-zero notional was booked as a cost, and a charge
    crossing a negative one refused). Neither reading fitting a stated
    price is refused (`where` names the row; the owner's ruling of
    2026-11-19, backlog TJ-4559 (3): it was booked at the price-sign
    reading, a notional of the other sign)."""
    a = abs(amount)
    sign = -1.0 if price < 0 else 1.0
    fits = [n for n in (a, -a)
            if (n - fee if target == "buy" else n + fee) * sign > 0]
    if not fits and a and price:
        raise ValueError(
            f"generic importer: {where}: neither a cost nor a credit of "
            f"{a:.2f} fits a {'sale' if target == 'sell' else 'purchase'} "
            f"at price {price:.2f} with a fee of {fee:.2f}. Check the "
            f"amount, the fee and the price's sign against the statement.")
    default = -a if price < 0 else a
    if len(fits) == 1:
        return fits[0], False
    return default, len(fits) == 2 and bool(a) and bool(price)


class _RowFacts(NamedTuple):
    """What every row classified to build reads of its cells: the date
    (its clock when the format has one), the currency, the account, the
    symbol as the export spells it and as listed, and the four numbers as
    given (None for a blank cell) and as money (0 for a blank)."""
    date: str
    clock: str
    currency: str
    row_account: str
    symbol_raw: str
    symbol: str
    qty_v: Optional[float]
    price_v: Optional[float]
    amount_v: Optional[float]
    fee_v: Optional[float]
    qty: float
    price: float
    amount: float
    fee: float


class _TradeMoney(NamedTuple):
    """A trade row's money, checked (`_trade_money`): whether it is a
    future, the option size and its basis, an option's expiry, the fee,
    a future's signed net (None for the rest) and whether its commission
    crosses its amount (resolved by the file's cash sign in the finish)."""
    is_future: bool
    mult: float
    size_basis: str
    exp: Optional[str]
    fee: float
    fut_net: Optional[float]
    crossing: bool                  # a commission larger than the amount: the finish resolves it


class _GenRow:
    """A row of the export as `read` yields it: its cells (the reader's
    dict), its line, whether it is the header repeated mid-file; and
    what `classify` learned for `build` (where it is named from, its
    raw action, its target, its description)."""
    __slots__ = ("cells", "line", "header", "where", "raw_action", "target", "desc")

    def __init__(self, cells: Dict[str, Any], line: int, header: bool = False):
        self.cells, self.line, self.header = cells, line, header
        self.where = self.raw_action = self.target = self.desc = None


@dataclass
class _GenParse:
    """What `read` hands the loop and the finish of one export: the
    mapping and what it says (the columns, the actions, the defaults,
    the options, the formats and their signs, the broker account, whether
    a fee is mapped), the cells reader, the header and the file's shape;
    and what the rows accumulate for the finish. Reset by `read`,
    released by `release` whatever happened."""
    mapping: Dict[str, Any]
    path: Path
    actions: Dict[str, str]
    defaults: Dict[str, Any]
    allow_large_fees: bool
    settle_on_trade_date: bool
    date_fmt: str
    has_clock: bool
    settle_fmt: str
    tax_sign: str
    fee_sign: str
    trade_fee_sign: Optional[str]
    broker_account: Optional[str]
    fee_mapped: bool
    amount_shared: bool             # one Amount column for the event rows' money
    cells: "_Cells"
    shown: str                      # the file as the messages name it
    fieldnames: List[str]
    unterminated: bool              # the last record has no line break
    last_line: int
    line_offset: int                # the lines before the header (HEADER_WITHIN)
    option_fmt: str = _OPTION_EXPIRY_FMT    # [formats] option_expiry
    # Rows with an unmapped action that move shares or cash.
    unbooked: List[str] = dc_field(default_factory=list)
    # Rows mapped to cash that carry units: their own UNBOOKED line (TJ-2586).
    cash_units: List[str] = dc_field(default_factory=list)
    # Each consumed row's date, file order (newest-first detection).
    row_dates: List[str] = dc_field(default_factory=list)
    # Each repeated header's (line, row-date index): the segments' bounds
    # (`_oldest_first` finds the transactions' by the lines the template noted).
    segments: List[Tuple[int, int]] = dc_field(default_factory=list)
    # (where, target, raw signed quantity) per trade row, for the
    # file-level sign-convention check after the loop.
    trade_signs: List[tuple] = dc_field(default_factory=list)
    # (where, target, amount) per trade row: the file's amount-sign
    # convention check after the loop (A2-0106).
    trade_amounts: List[tuple] = dc_field(default_factory=list)
    # Futures rows whose commission crosses the notional: (tx index,
    # where, target, amount cell, signed fee) — backlog TJ-0668.
    fut_crossing: List[tuple] = dc_field(default_factory=list)
    # Futures rows at a stated price of 0 with no fee stated, whose
    # amount is their commission or rebate: the same tuples, resolved by
    # the file's cash sign (backlog TJ-4558).
    fut_zero: List[_FutFill] = dc_field(default_factory=list)
    # Rows at a stated price of 0 that carry cash beyond a fee: said
    # once per file (the owner's ruling of 2026-11-19, TJ-4559 (1)).
    zero_priced: List[str] = dc_field(default_factory=list)
    # Option rows closed at $0 and stock trades, for the exercise/
    # assignment check after the loop (A2-0629).
    zero_closes: List[Dict[str, Any]] = dc_field(default_factory=list)
    expiry_clamped: List[str] = dc_field(default_factory=list)
    late_settles: List[str] = dc_field(default_factory=list)
    # One segment per export pasted into the file (backlog TJ-0099):
    # their shared rows are refused after the loop; the first starts on
    # the line after the header (`read` sets it past the lines above).
    pasted: List[tuple] = dc_field(default_factory=lambda: [(2, [])])


def _is_header_row(row) -> bool:
    """A record that repeats the header (two exports concatenated)."""
    return all((v or "").strip().lower() == (k or "").strip().lower()
               for k, v in row.items()
               if k is not None and isinstance(v, (str, type(None))))


@dataclass
class _GenAccountContext:
    """What every parse of one taxjson account's generic exports shares
    (`GenericBrokerage.prepare_files`): each export's broker accounts with
    their account_type cells (`per_file`, by resolved path), merged over
    the exports (`plan_types`, `plan_files`), for the plan check across
    them, said once (`plans_checked`; TJ-2584); the mappings never run
    on a real export, each said once (`unverified_said`; TJ-2573)."""
    files: List[str]
    per_file: Dict[str, Dict[str, set]] = dc_field(default_factory=dict)
    plan_types: Dict[str, set] = dc_field(default_factory=dict)
    plan_files: Dict[str, set] = dc_field(default_factory=dict)
    plans_checked: bool = False
    unverified_said: set = dc_field(default_factory=set)


def _account_cells(path: Path) -> Dict[str, set]:
    """{broker account: {its account_type cells}} of one generic export:
    a row's `account` cell, else the mapping's [broker] account. A row
    naming no account under a mapped account column, with no [broker]
    account, is left out, as Questrade's blank Account # is (TJ-2585); a
    file with neither is one account of no number. {} when the mapping
    maps no account, no type and no [broker] account, or when the file
    or its mapping cannot be read (the parse says why)."""
    try:
        mapping = _load_mapping(path)
    except (ValueError, OSError):
        return {}
    cols = mapping.get("columns") or {}
    fallback = str((mapping.get("broker") or {}).get("account") or "").strip()
    if not (cols.get("account") or cols.get("account_type") or fallback):
        return {}
    out: Dict[str, set] = {}
    try:
        reader, _, _ = _header_reader(
            decode_broker_text(path.read_bytes(), shown_name(path)), cols.values())
        header = {(h or "").strip().lower(): h for h in reader.fieldnames or []}
        cells = _Cells({k: v for k, v in cols.items()
                        if k in ("account", "account_type")
                        and str(v).strip().lower() in header}, header)
        for row in reader:
            if _is_header_row(row):
                continue
            acct = cells.own(row, "account").strip() or fallback
            if not acct and cols.get("account"):
                continue
            out.setdefault(acct, set()).add(cells.own(row, "account_type").strip())
    except (BrokerageParseError, csv.Error, OSError, ValueError):
        return {}
    return out


def _generic_account_context(paths) -> _GenAccountContext:
    """The account context of `paths` (`_GenAccountContext`)."""
    ctx = _GenAccountContext(files=list(dict.fromkeys(
        str(Path(p).resolve()) for p in paths)))
    for f in ctx.files:
        ctx.per_file[f] = accts = _account_cells(Path(f))
        for a, types in accts.items():
            ctx.plan_types.setdefault(a, set()).update(types)
            ctx.plan_files.setdefault(a, set()).add(f)
    return ctx


class GenericBrokerage(BaseBrokerage):
    DEFAULT_ACCOUNT = "Generic"

    @classmethod
    def prepare_files(cls, paths, *, tax_year: Optional[int] = None,
                      combined: bool = False, taxable: Optional[bool] = None,
                      symbol_codes=None, project=None) -> _GenAccountContext:
        """The account's exports' broker accounts and types, read once
        before any parse, so a TFSA's export beside a margin account's is
        checked as one account's (`_GenAccountContext`, TJ-2584)."""
        return _generic_account_context(paths)

    def _option_symbol(self, parts, own: str, currency: str, where: str,
                       money=(None, None, None, None)) -> str:
        """The OCC symbol of the contract `[extract] option` read on the
        row (`parts`: its named groups, where it read them, whether the
        match is the whole cell; '' when it read none), through the
        broker parsers' `option_parts` and `format_occ_symbol`: the root
        upper-cased (a space, a '-' or a '/' a dot), the expiry in
        `[formats].option_expiry` (2000 to 2099, as the symbol holds it).
        A symbol cell that is itself an OCC symbol must be the same
        contract (its listing kept). Read from the description beside a
        ticker in the symbol cell (an explicit listing there kept), the
        row is the contract when its `money` (quantity, price, amount,
        fee cells) reads as an option's, or '' (the ticker's shares: an
        assignment's stock leg names its option too) when it reads as
        shares; with no price and amount to tell, the contract only when
        the match is the whole description, else refused."""
        if not parts:
            return ""
        src = f"([extract].option, {self._gen.mapping['_path']})"
        try:
            p = option_parts(parts["right"], parts["root"], parts["expiry"],
                             parts["strike"])
        except BrokerageParseError as e:
            raise ValueError(f"generic importer: {where}: {e} {src}.") from None
        if p["right"] is None:
            raise ValueError(
                f"generic importer: {where}: option right {parts['right']!r} "
                f"is not C, P, CALL or PUT {src}.")
        fmt = self._gen.option_fmt
        try:
            expiry = datetime.strptime(parts["expiry"], fmt)
        except ValueError:
            raise ValueError(
                f"generic importer: {where}: option expiry "
                f"{parts['expiry']!r} does not match [formats].option_expiry "
                f"= {fmt!r} {src}.") from None
        if not 2000 <= expiry.year <= 2099:
            raise ValueError(
                f"generic importer: {where}: option expiry "
                f"{parts['expiry']!r} is in {expiry.year}: an OCC symbol "
                f"holds the years 2000 to 2099 {src}.")
        root = re.sub(r"[-/]", ".", p["base"]).upper()
        if not root:
            raise ValueError(
                f"generic importer: {where}: the option's root is blank {src}.")
        try:
            occ = self.format_occ_symbol(p["right"], root,
                                         expiry.strftime("%m/%d/%y"),
                                         p["strike"],
                                         where=f"generic importer: {where}")
        except BrokerageParseError as e:
            raise ValueError(f"{e} {src}.") from None
        if not is_option_symbol(occ):
            raise ValueError(
                f"generic importer: {where}: the option's root "
                f"{parts['root']!r} cannot begin an OCC symbol (letters, "
                f"digits and dots, at most 10) {src}.")
        compact = re.sub(r"\s+", "", own).upper()
        if is_option_symbol(compact):
            # (A futures option's cell keeps its prefix: the same contract.)
            body = re.match(r"^(.*?\d{6}[CP]\d+)", FUTURES_PREFIX_RE.sub(
                "", self._listing_symbol(compact, currency), count=1))
            if body is None or body.group(1) != occ:
                raise ValueError(
                    f"generic importer: {where}: the symbol cell {own!r} and "
                    f"the description's contract {occ} {src} name two "
                    f"contracts — fix the pattern or the cell.")
            return compact
        if parts.get("_from") != "description" or not compact:
            return occ
        return self._option_beside_ticker(parts, occ, root, compact,
                                          currency, where, money, src)

    def _option_beside_ticker(self, parts, occ, root, ticker, currency,
                              where, money, src) -> str:
        """`_option_symbol`'s contract read from the description of a row
        whose symbol cell holds `ticker` (its root's ticker: the same
        letters, an adjusted or a weekly root's prefix; another refused):
        the contract (the ticker's explicit listing kept, a venue spelled
        as `_listing_symbol` spells it), or '' when the money reads as
        shares, the price and the amount both non-zero: quantity x price
        within the fee of the amount, against x 100, a mini's 10 or the
        ticker.map MULT size. Without them (a blank, a 0), the contract
        only when the match is the whole description, else refused."""
        from taxjson.lib.option_symbols import parse_option_underlying
        from taxjson.lib.markets import option_contract_size
        base = re.sub(r"[-/]", ".", ticker)
        sfx = next((s for s in _KNOWN_SUFFIXES if base.endswith("." + s)), "")
        if sfx:
            base = base[:-len(sfx) - 1]
            lst = self._listing_symbol(ticker, currency)
            occ += lst[lst.rindex("."):]    # ZZQ.US in CAD: the .US listing
        r, b = root.replace(".", ""), base.replace(".", "")
        if not (r.startswith(b) or b.startswith(r)) or not is_option_symbol(occ):
            raise ValueError(
                f"generic importer: {where}: the symbol cell {ticker!r} and "
                f"the description's contract {occ} {src} name two "
                f"securities — fix the pattern or the cell.")
        qty, price, amount, fee = money
        if price and amount:
            gross = abs(qty or 0.0) * abs(price)
            a, f = abs(amount), abs(fee or 0.0)

            def off(g):
                return min(abs(a - g), abs(a - g - f), abs(a + f - g))
            sizes = (self.OPTION_MULTIPLIER, self.MINI_OPTION_MULTIPLIER,
                     option_contract_size(occ, parse_option_underlying(occ)
                                          or root) or self.OPTION_MULTIPLIER)
            return ("" if off(gross) < min(off(gross * float(s)) for s in sizes)
                    else occ)
        if parts.get("_whole"):
            return occ
        raise ValueError(
            f"generic importer: {where}: the description names the contract "
            f"{occ} {src}, the symbol cell {ticker!r}, and the row has no "
            f"price and amount (both non-zero) to tell an option's trade "
            f"from the shares (an assignment's or an exercise's stock leg "
            f"names its option too). Anchor the pattern (^...$) so it "
            f"matches the option rows' whole description, or map "
            f"[columns].price and [columns].amount.")

    def _listing_symbol(self, symbol_raw: str, currency: str) -> str:
        """The row's symbol with its exchange suffix. An explicit known
        suffix (.TO/.US/.AX/.L) is the LISTING and is kept (a Canadian
        venue .V/.VN/.CN/.NE is spelled .TO, below):
        the row currency is only what the trade settled in, and deriving
        the suffix from it turned SAMPLF.U.TO bought in USD into SAMPLF.U.US —
        a different identity, so a superficial loss across accounts was
        missed (audit R1-122). A bare symbol still takes its suffix from
        the currency (SAMPLZ in CAD -> SAMPLZ.TO)."""
        sym = symbol_raw.strip()
        # A futures contract is spelled F: everywhere downstream (IB,
        # check-dates, the engines): '/ESZ5' and '\\ESZ5' are the same
        # contract as 'F:ESZ5' (audit A2-0107 / A2-0294), as every reader
        # of a symbol a person writes spells it (backlog TJ-5150).
        sym = canonical_futures_symbol(sym)
        # One security, one spelling (audit S010-04): the ACB pool and
        # the superficial-loss match key on the exact symbol string.
        #  * An OCC symbol padded to 21 characters ('SAMPLE   250321C...')
        #    is the compact contract, not 'SAMPLE...250321C...'.
        #  * A class-share separator '-' or '/' is the dot every broker
        #    parser uses: SAMPLC-B and SAMPLC/B are SAMPLC.B.
        #  * A broker option DESCRIPTION ('SAMPLE 21MAR25 50 C', 'CALL SAMPLE
        #    03/21/25 50') is not a symbol: booked as a share, it never
        #    expired. Refused — give the OCC symbol.
        _occ = re.match(r'^([A-Za-z0-9.]+)\s+(\d{6}[CPcp]\d{8})$', sym)
        if _occ:
            sym = _occ.group(1) + _occ.group(2).upper()
        elif (re.match(r'^\S+\s+\d{1,2}[A-Za-z]{3}\d{2}\s+[\d.,]+\s+[CPcp]$',
                       sym)
              or re.match(r'^(?:CALL|PUT)\s', sym, re.IGNORECASE)):
            raise BrokerageParseError(
                f"generic importer: symbol {symbol_raw!r} is an option "
                f"DESCRIPTION, not a symbol — give the OCC symbol (e.g. "
                f"SAMPLE250321C00050000), or read the contract from it with "
                f"an [extract] option pattern, so the contract is an option.")
        _cls = re.match(r'^([A-Za-z0-9]+)[-/]([A-Za-z]{1,2})(\.[A-Za-z]{1,2})?$',
                        sym)
        if _cls:
            sym = f"{_cls.group(1)}.{_cls.group(2)}{_cls.group(3) or ''}"
        # Upper-cased: `sample` and `SAMPLE` are one security; left as typed
        # they were two ACB pools and a sale opened a phantom short
        # (R1-128).
        sym = sym.replace(" ", ".").upper()
        up = sym
        # A Canadian venue (.TO/.V/.VN/.CN/.NE) is one Canadian listing,
        # spelled ROOT.TO by every broker parser (base.canonical_ca_
        # listing, audit S010-05): an explicit .V here must not split
        # the pool from the same shares bought at IB. The venue, not the
        # row currency, decides -- SAMPLF.U.TO bought in USD stays .TO.
        # A dotted preferred series (FTN.PR.L, FTN.PR.V) names no venue:
        # its letter is the series (backlog TJ-0970).
        if is_ca_pref_series(sym):
            return self.apply_currency_suffix(sym, currency)
        for suf in sorted(_ca_sfx()):
            if up.endswith("." + suf) and len(up) > len(suf) + 1:
                return canonical_ca_listing(sym, "CAD")
        for suf in _KNOWN_SUFFIXES:
            if up.endswith("." + suf) and len(up) > len(suf) + 1:
                return sym[:-len(suf)] + suf
        return self.apply_currency_suffix(sym, currency)

    # The real broker named by the last parsed file's mapping
    # ([broker].name), None when unnamed (audit S027-05).
    broker_name: Optional[str] = None
    # The project's futures_settle ("trade" | "next_day"), set by
    # taxjson-brokerage like on the IB parser (CA-DATE-09/10,
    # US-DATE-09/12; audit A2-0103 — it used to reach IB only).
    futures_settle: str = "trade"

    def statement_accounts(self) -> set:
        """The broker account ids the last parsed file's rows carry
        ([columns].account cells and/or [broker].account) — empty when
        the mapping declares none (audit A2-1085)."""
        return set(getattr(self, "_accounts", None) or ())

    def read(self, path: Path):
        """The mapping resolved and read (its columns, actions, defaults,
        options and formats checked), the file decoded and its header
        checked against the mapping; what the later phases need kept as
        `_gen`. The rows are a generator (`_rows`), so each row's checks
        come in file order with its classification and build, as the
        one loop had them."""
        mapping = _load_mapping(path)
        self._say_unverified(mapping)
        self.broker_name = (mapping.get("broker") or {}).get("name") or None
        cols: Dict[str, str] = {k: v for k, v in
                                (mapping.get("columns") or {}).items()}
        actions: Dict[str, str] = {
            action_key(k): v for k, v in
            (mapping.get("actions") or {}).items()}
        defaults = mapping.get("defaults") or {}
        options = mapping.get("options") or {}
        allow_large_fees = bool(options.get("allow_large_fees", False))
        settle_on_trade_date = bool(options.get("settle_on_trade_date",
                                                False))
        formats = mapping.get("formats") or {}
        date_fmt = formats.get("date", "%Y-%m-%d")
        # A date format with a clock: each trade keeps the execution
        # time the row states (backlog TJ-0734: every row was stamped
        # 09:30, and a newest-first export replayed a day's trades
        # backwards).
        has_clock = bool(re.search(r"%[HIMSXc]", date_fmt))
        settle_fmt = formats.get("settle", date_fmt)
        tax_sign = formats.get("tax_sign", "cash")
        fee_sign = formats.get("fee_sign", "cash")
        # A trade row's commission sign is read only under an EXPLICIT
        # fee_sign (A2-0626): the default is documented for FEE rows,
        # and files of positive commissions must not become rebates.
        trade_fee_sign = formats.get("fee_sign")
        broker_account = (mapping.get("broker") or {}).get("account")
        self._accounts: set = set()
        if fee_sign not in ("cash", "charged"):
            raise ValueError(
                f"generic importer: {mapping['_path']}: [formats]."
                f"fee_sign must be 'cash' (negative = charged, the "
                f"default) or 'charged' (positive = charged), got "
                f"{fee_sign!r}")
        fee_mapped = "fee" in cols
        # Parser-reported problems `taxjson-brokerage --lint` fails on.
        self.lint_findings: List[str] = []
        if tax_sign not in ("cash", "withheld"):
            raise ValueError(
                f"generic importer: {mapping['_path']}: [formats]."
                f"tax_sign must be 'cash' (negative = withheld, the "
                f"default) or 'withheld' (positive = withheld), got "
                f"{tax_sign!r}")

        # The shared decoder: UTF-16 (BOM) like every parser and broker
        # detection read it; a legacy encoding is refused naming the file
        # (audit A2-1083).
        try:
            text = decode_broker_text(path.read_bytes(), shown_name(path))
        except BrokerageParseError as e:
            raise ValueError(f"generic importer: {e}")
        # The file's last record has no line break after it: a cut-off
        # export ends this way (audit A2-0030).
        _unterminated = bool(text) and not text.endswith(("\n", "\r"))
        _last_line = (len(text.rstrip("\r\n").splitlines())
                      if text else 0)
        reader, line_offset, above = _header_reader(text, cols.values())
        fieldnames = reader.fieldnames or []
        header: Dict[str, str] = {}
        _dupes = set()
        for h in fieldnames:
            key = (h or "").strip().lower()
            if key in header:
                _dupes.add(key)
            header[key] = h
        # A MAPPED column that exists more than once is ambiguous
        # (real exports carry twin Amount columns — native vs
        # account currency); DictReader silently keeps the last.
        _ambig = [f"{k} -> {v!r}" for k, v in cols.items()
                  if v.strip().lower() in _dupes]
        if _ambig:
            raise ValueError(
                f"generic importer: {shown_name(path)}: mapped column(s) "
                f"appear MORE THAN ONCE in the header — rename the "
                f"duplicates or map the exact one you mean: "
                f"{', '.join(_ambig)}. Header: {fieldnames}.")
        missing = [f"{k} -> {v!r}" for k, v in cols.items()
                   if v.strip().lower() not in header]
        if missing:
            raise ValueError(
                f"generic importer: {shown_name(path)}: mapped column(s) "
                f"not in the CSV header: {', '.join(missing)}. "
                f"Header: {fieldnames} (no row of the first "
                f"{HEADER_WITHIN} holds every mapped column). Fix "
                f"{mapping['_path']}.")
        _refuse_rows_above(above, fieldnames, date_fmt, shown_name(path),
                           line_offset + 1)

        cells = _Cells(cols, header)

        self._transfer_amounts_unread = []
        self._gen = _GenParse(
            mapping=mapping, path=path, actions=actions,
            defaults=defaults, allow_large_fees=allow_large_fees,
            settle_on_trade_date=settle_on_trade_date, date_fmt=date_fmt,
            has_clock=has_clock, settle_fmt=settle_fmt, tax_sign=tax_sign,
            fee_sign=fee_sign, trade_fee_sign=trade_fee_sign,
            broker_account=broker_account, fee_mapped=fee_mapped,
            amount_shared=_amount_shared(cols), cells=cells,
            shown=shown_name(path), fieldnames=fieldnames,
            unterminated=_unterminated,
            last_line=_last_line, line_offset=line_offset,
            pasted=[(line_offset + 2, [])],
            option_fmt=formats.get("option_expiry", _OPTION_EXPIRY_FMT))
        return self._rows(reader)

    def _say_unverified(self, mapping: Dict[str, Any]) -> None:
        """A mapping marked `[broker] verified = false`, never run on a real
        export (the shipped Wealthsimple layout: the owner's decision of
        2026-11-21, TJ-2573): said once per account's parse (each file's
        when there is no account context), before the file's own checks
        can refuse it, and in `tjs run`'s end-of-run list too
        (END_OF_RUN_MARK). What it says to check is the books against the
        statements; how to report a mismatch, without the export's data."""
        what = mapping.get("_unverified")
        ctx = self.account_context
        said = ctx.unverified_said if isinstance(ctx, _GenAccountContext) else set()
        if not what or what in said:
            return
        said.add(what)
        emit_line(f"warning: ATTENTION: {END_OF_RUN_MARK}generic importer: the "
                  f"{what} is unverified against a real export: check this "
                  f"account's totals against the statements; report a "
                  f"mismatch with the header row and made-up rows, never "
                  f"real ones (docs/brokers.md).")

    def _rows(self, reader):
        """The export's rows, each with its line: a blank row dropped
        (counted nowhere, as before), the header repeated mid-file
        marked for `classify` (a segment boundary), a malformed record
        (fewer or more cells than the header, a line break in a cell,
        a cut-off last record) refused naming its line."""
        st = self._gen
        shown, fieldnames = st.shown, st.fieldnames
        _unterminated, _last_line = st.unterminated, st.last_line
        off = st.line_offset
        for row in reader:
            line = reader.line_num + off      # the file's own line
            if not any((v or "").strip() for v in row.values()
                       if isinstance(v, str) or v is None):
                continue
            if _is_header_row(row):
                # The header repeated mid-file: two exports
                # concatenated. A segment boundary for the row order
                # (re-audit A2-1084), not an unmapped action: classify
                # counts it and marks the boundary.
                yield _GenRow(row, line, header=True)
                continue
            # A record wider than the header, or a cell holding a
            # line break: an unescaped quote in a text cell swallowed
            # the following row(s) and the columns now carry another
            # row's numbers — every cross-check can still pass
            # (S057-08). Refuse, naming the line.
            _extra = row.get(None)
            _nl = [k for k, v in row.items()
                   if k is not None and isinstance(v, str)
                   and ("\n" in v or "\r" in v)]
            # FEWER cells than the header: DictReader fills the
            # missing ones with None, and [defaults] used to fill a
            # currency the cut took away (a USD trade booked as a CAD
            # .TO security) — a cut-off export (audit A2-0030).
            _short = [k for k, v in row.items()
                      if k is not None and v is None]
            if _short:
                raise ValueError(
                    f"generic importer: {shown} record ending on "
                    f"line {line}: "
                    f"{len(fieldnames) - len(_short)} cell(s), "
                    f"fewer cells than the header's {len(fieldnames)} "
                    f"(missing: {', '.join(map(repr, _short[:3]))}) — "
                    f"a cut-off or hand-trimmed row"
                    + (", or a title or account line of a second export "
                       "pasted below the first (save each export as its "
                       "own file)" if len(fieldnames) - len(_short) <= 2
                       else "")
                    + ". Re-export the file (or fill the row in full).")
            if (_unterminated and line >= _last_line
                    and fieldnames
                    and not (row.get(fieldnames[-1]) or "").strip()):
                raise ValueError(
                    f"generic importer: {shown} line "
                    f"{line}: the file ends on a separator "
                    f"with no line break — its last record looks cut "
                    f"off (the {fieldnames[-1]!r} cell is missing). "
                    f"Re-export the file.")
            if _extra or _nl:
                raise ValueError(
                    f"generic importer: {shown} record ending on "
                    f"line {line}: "
                    + (f"{len(_extra)} cell(s) more than the header"
                       if _extra else
                       f"column {_nl[0]!r} holds a line break")
                    + " — an unescaped quote in a text cell usually "
                    "swallows the next row(s) this way. Fix the quoting "
                    "(double an inner quote: \"\") and re-run.")
            yield _GenRow(row, line)

    def classify(self, r) -> Outcome:
        """The repeated header a non-event and a segment boundary; else the
        row's cells kept in its pasted segment (the overlap check after
        the loop), and its action through the mapping's [actions] and
        [[classify]] rules (`_classify_row`): unmapped, a skip noted as
        unbooked when it moves shares or cash; mapped to skip, a skip;
        else build, the label kept on the row."""
        st = self._gen
        if r.header:
            st.segments.append((r.line, len(st.row_dates)))
            st.pasted.append((r.line, []))
            return Outcome.nonevent("repeated header row (concatenated exports)")
        r.where = f"{st.shown} line {r.line}"
        st.pasted[-1][1].append((r.line, tuple(
            (str(v) if v is not None else '').strip()
            for k, v in r.cells.items() if k is not None)))
        raw_action, target, desc = self._classify_row(
            r.cells, st.cells.cell, st.cells.pulled, r.where, st.actions,
            st.defaults, st.mapping)
        if target is None:
            self._note_unbooked(r.cells, st.cells.cell, st.cells.num,
                                raw_action, r.where, st.unbooked)
            return Outcome.skip(f"action {raw_action or '?'!s}")
        if target == "skip":
            return Outcome.skip(f"action {raw_action} (mapped skip)")
        if target == "cash":
            # A cash move (a deposit, a withdrawal): no tax event, but a
            # row carrying units or a ticker is no cash move (TJ-2574).
            units = self._units_of(r.cells, st.cells.cell, st.cells.num)
            if units:
                w = f"{r.where}: action {raw_action!r} carries {units}"
                st.cash_units.append(w)
                self.lint_findings.append(
                    f"{w}: mapped to cash, not a cash move — not booked")
                return Outcome.reported(f"action {raw_action} (cash, with units)")
            return Outcome.nonevent(f"cash move ({raw_action})")
        r.raw_action, r.target, r.desc = raw_action, target, desc
        return Outcome.build()

    def build(self, r) -> "List[Dict[str, Any]] | Outcome":
        """A row classified to build: what every such row reads of its
        cells (`_RowFacts`: the date, with its clock when the format has
        one, the currency, the account, the symbol, the numbers), then a
        trade, an event row or an income row (one transaction at most;
        an Outcome for the non-event an event builder finds)."""
        st = self._gen
        target = r.target
        row, where = r.cells, r.where
        cell, num, mapping = st.cells.cell, st.cells.num, st.mapping
        defaults, date_fmt, has_clock = st.defaults, st.date_fmt, st.has_clock
        broker_account, row_dates = st.broker_account, st.row_dates
        date_raw = str(cell(row, "date")).strip()
        dt = self.parse_date(date_raw, date_fmt)
        if dt is None:
            # Same policy as Coinbase/Kraken: a silently
            # stamped or dropped date distorts years/holding
            # periods invisibly. The line and the mapping are
            # named (backlog TJ-0057).
            raise ValueError(
                f"generic importer: {where}: unparseable date "
                f"{date_raw!r}: it does not match [formats].date = "
                f"{date_fmt!r} "
                f"({mapping['_path']}) — fix the "
                f"format in the mapping or the cell.")
        date = dt.strftime("%Y-%m-%d")
        clock = dt.strftime("%H:%M:%S") if has_clock else ""
        # The order check sees the clock too: a newest-first
        # export's rows of one day go down in time.
        row_dates.append(f"{date} {clock}" if clock else date)
        currency = (str(cell(row, "currency")).strip()
                    or str(defaults.get("currency", ""))
                    ).strip().upper()
        if not currency:
            raise ValueError(
                f"generic importer: {where}: empty currency cell "
                f"and no [defaults].currency in "
                f"{mapping['_path']} — refusing to assume USD.")
        if not _CURRENCY_RE.match(currency):
            raise ValueError(
                f"generic importer: {where}: currency {currency!r} "
                f"is not a currency code (CAD, USD ...) — a cut-off "
                f"row or a mis-mapped [columns].currency.")
        row_account = (str(cell(row, "account")).strip()
                       or broker_account or "")
        if row_account:
            self._accounts.add(row_account)
        qty_v = num(row, "quantity", where)
        price_v = num(row, "price", where)
        amount_v = num(row, "amount", where)
        fee_v = num(row, "fee", where)
        own = str(cell(row, "symbol")).strip()
        symbol_raw = (self._option_symbol(st.cells.pulled.get("option"), own,
                                          currency, where,
                                          (qty_v, price_v, amount_v, fee_v))
                      or own or str(defaults.get("symbol", "")))
        if not symbol_raw and target not in ("interest", "fee") and not (
                {"symbol"} & (set(st.cells.cols) | set(defaults)
                              | set(mapping["_extract"]))):
            raise ValueError(
                f"generic importer: {where}: {r.raw_action} (-> {target}) "
                f"names no security: the mapping's one symbol source, its "
                f"[extract] option pattern ({mapping['_path']}), does not "
                f"match the row. Map [columns].symbol or give an "
                f"[extract].symbol pattern (or map the row to skip).")
        try:
            symbol = (self._listing_symbol(symbol_raw, currency)
                      if symbol_raw else "")
        except FuturesSymbolError as e:     # `//QZM26.US` (TJ-5150)
            raise BrokerageParseError(f"generic importer: {where}: {e}") from None
        qty = qty_v or 0.0
        price = price_v or 0.0
        amount = amount_v or 0.0
        fee = fee_v or 0.0
        f = _RowFacts(date=date, clock=clock, currency=currency,
                      row_account=row_account, symbol_raw=symbol_raw,
                      symbol=symbol, qty_v=qty_v, price_v=price_v,
                      amount_v=amount_v, fee_v=fee_v,
                      qty=qty, price=price, amount=amount, fee=fee)
        if target in ("buy", "sell"):
            return self._build_trade(r, f)
        if target in _ASSIGN_SIDE:
            return self._build_assign(r, f)
        if target in _EVENT_TARGETS:
            return self._build_event(r, f)
        return self._build_income(r, f)

    def _trade_money(self, r, f, side=None) -> "_TradeMoney":
        """A trade row's money checked: whether it is a future, an option's
        size and its basis, its expiry, the fee (an expiry's commission
        inferred from a $0 close, a trade's checked against its cells),
        a future's signed net and whether its commission crosses its
        amount; the sign evidence kept for the finish."""
        st = self._gen
        where, raw_action, target = r.where, r.raw_action, side or r.target
        date, symbol_raw, symbol, price_v = f.date, f.symbol_raw, f.symbol, f.price_v
        amount_v, fee_v, qty, price = f.amount_v, f.fee_v, f.qty, f.price
        amount, fee = f.amount, f.fee
        trade_fee_sign, fee_mapped = st.trade_fee_sign, st.fee_mapped
        allow_large_fees = st.allow_large_fees
        trade_signs, trade_amounts = st.trade_signs, st.trade_amounts
        is_future = symbol.startswith(FUTURES_PREFIXES)
        mult, size_basis = 1.0, ""
        if (is_option_symbol(symbol_raw)
                or is_option_symbol(symbol)):
            mult, size_basis = self._option_size(
                symbol, target, qty, price, amount_v, fee,
                fee_mapped, is_future)
        _exp = (parse_option_expiry(symbol)
                if mult > 1 and not is_future else None)
        # (A blank fee cell states nothing, like no fee
        # column: backlog TJ-1240.) An assignment's option leg is
        # at 0 on any day: its commission is read as an expiry's.
        expiry_fee = (
            _expiry_commission(target, qty, amount, date,
                               date if side and _exp else _exp)
            if fee_v is None and price_v is not None
            and not price else 0.0)
        if expiry_fee:
            fee = expiry_fee
        self._check_trade_shape(
            where, target, raw_action, qty, price_v, amount_v,
            abs(fee), mult, is_future, label=r.target)
        trade_signs.append((where, target, qty, r.target))
        fee = _check_trade_row(
            where, target, qty, price, amount, fee, mult,
            allow_large_fees, fee_mapped, is_future,
            amount_given=amount_v is not None,
            # (An inferred fee is a charge, not a signed cell.)
            fee_sign=None if expiry_fee else trade_fee_sign,
            price_given=price_v is not None)
        fut_zero = is_future and price_v is not None and not price
        fut_net, fut_either = (
            _zero_price_future(where, target, amount, fee,
                               fee_v is not None or bool(expiry_fee))
            if fut_zero
            else _future_net(target, price, amount, fee, where) if is_future
            else (None, False))
        if (price_v is not None and not price and not is_future
                and abs(amount) > 0.005 and not _fee_only_cash(
                    True, price, amount, fee)):
            st.zero_priced.append(f"line {r.line}")
        if not fut_either and not (is_future and (price < 0 or fut_zero)) and not (
                mult > 1 and _fee_only_cash(
                    price_v is not None, price, amount, fee,
                    is_future)):
            # A negative futures price turns the amount's
            # sign, and an option's fee-only $0 close is its
            # charge either way: no evidence of the file's
            # cash sign (backlog TJ-1238).
            trade_amounts.append((where, target, amount_v, r.target))
        return _TradeMoney(is_future, mult, size_basis, _exp, fee, fut_net,
                           fut_either)

    @builds("trade")
    def _build_trade(self, r, f, side=None) -> List[Dict[str, Any]]:
        """A buy or sell (or an assignment's leg, `side` its direction: no
        expiry): its money (`_trade_money`), an option's $0 close
        on or just after its expiry dated the expiry day, the settle (an
        expiry settles on its day; a late settle noted), a $0 option
        close kept for the assignment check, and the transaction."""
        st = self._gen
        m = self._trade_money(r, f, side)
        row, where, target, desc = r.cells, r.where, side or r.target, r.desc
        date, clock, currency, row_account = f.date, f.clock, f.currency, f.row_account
        symbol, price_v, amount_v, qty = f.symbol, f.price_v, f.amount_v, f.qty
        price, amount = f.price, f.amount
        is_future, mult, size_basis, _exp, fee, fut_net, crossing = m
        cell, path, settle_fmt = st.cells.cell, st.path, st.settle_fmt
        fut_crossing = st.fut_crossing
        settle_on_trade_date, zero_closes = st.settle_on_trade_date, st.zero_closes
        expiry_clamped = st.expiry_clamped
        late_settles = st.late_settles
        # An option closed at $0 on (or posted just after)
        # its expiry IS the expiry: dated and settled on the
        # expiry day (CA-DATE-08 / US-DATE-08), not T+1 —
        # a Dec-31 expiry's loss moved into the next year
        # (audit A2-0628).
        # A commission does not make it a trade: its cash
        # is the fee alone (backlog TJ-0732: it settled T+1,
        # a Dec-31 expiry's loss in the next year).
        _zero = (not price and (not amount or _fee_only_cash(
            True, price, amount, fee)))
        if _exp and _zero and not side and _exp < date and \
                self._days(_exp, date) <= _EXPIRY_POST_DAYS:
            expiry_clamped.append(
                f"{where}: {symbol} $0 close dated {date} -> "
                f"its expiry {_exp}")
            date = _exp
            clock = ""      # another day's clock
        if _exp and _zero and date == _exp and not side:
            date_settle = _exp
        else:
            date_settle = self._settle_for(
                row, cell, where, date, settle_fmt, symbol,
                currency, mult > 1, settle_on_trade_date,
                self.futures_settle)
            if self._days(date, date_settle) > _SETTLE_LATE_DAYS:
                late_settles.append(
                    f"{where} ({symbol} trade {date}, settle "
                    f"{date_settle})")
        if mult > 1 and not is_future and _zero and not side:
            zero_closes.append({"where": where,
                                "symbol": symbol, "date": date,
                                "qty": qty, "target": target})
        tx = {
            "action": "BUYSELL",
            "date": date, "time": clock or "09:30:00",
            "date_settle": date_settle,
            "symbol": symbol,
            "quantity": self.signed_quantity(
                qty, action_is_sell=(target == "sell")),
            "currency": currency,
            # A futures price keeps its sign, as IB's does:
            # the schema lets a plain-futures BUY at a
            # negative price receive cash only when it sees
            # that price (stored unsigned, the run refused
            # the row).
            "price": price if is_future else abs(price),
            # Amount column when present (fee-inclusive,
            # magnitude); else derive from qty*price±fee.
            "net_amount": (fut_net if fut_net is not None
                           else _trade_net(
                path.name, target, qty, price, amount, fee,
                mult, is_future,
                amount_given=amount_v is not None,
                price_given=price_v is not None)),
            "gross_amount": (
                # Futures: the implied qty x price x size,
                # from the signed net and fee (a rebate is
                # negative): its magnitude.
                _future_notional(target, fut_net, fee)
                if fut_net is not None
                else self.theoretical_gross(
                    qty, abs(price), is_option=(mult > 1),
                    multiplier=mult)),
            "fee": fee,
            "account": self.DEFAULT_ACCOUNT,
            "description": desc,
            **({"broker_account": row_account}
               if row_account else {}),
            # A mini the row's own amount shows, or the
            # MULT size its cash is at (backlog TJ-0863,
            # TJ-1034): the size the row check compares its
            # money with. A MULT size its cash is not at
            # stays the engine's to read, as for every parser.
            **({"multiplier": mult,
                "contract_size_basis": size_basis}
               if size_basis in ("derived", "declared")
               and mult != float(self.OPTION_MULTIPLIER)
               else {}),
        }
        if crossing:
            # Resolved after the loop by the file's cash sign (backlog
            # TJ-0668, TJ-4558): the transaction itself, not its index.
            if is_future and price_v is not None and not price:
                st.fut_zero.append((tx, where, target, amount, fee))
            else:
                fut_crossing.append((tx, where, target, amount, fee))
        return [tx]

    @builds("assign")
    def _build_assign(self, r, f) -> List[Dict[str, Any]]:
        """One leg of an exercise or an assignment (`assign_buy`: the
        contracts or shares in; `assign_sell`: out), the schema's ASSIGN
        row, which the engines pair (core `_pair_assign_legs`), whichever
        comes first, folding the premium into the shares' cost or
        proceeds (CA-OPT-06, CA-OPT-08; US-OPT-02, US-OPT-05). The option
        leg carries no money but its fee (a blank price is its 0): the
        premium is in the trade that opened it, the strike in the share
        leg, which is a buy's or a sell's money and never $0 (a warrant's
        or a right's exercise is a .tt line with exercise_of=). Neither is
        an expiry; `_check_assign_legs` pairs them in the file. A futures
        contract (or its option) is refused: a .tt line books it. The
        shared checks weigh the leg's side and name its target."""
        where, target, symbol = r.where, r.target, f.symbol
        side = _ASSIGN_SIDE[target]
        what = f"generic importer: {where}: {r.raw_action} (-> {target})"
        if symbol.startswith(FUTURES_PREFIXES):
            raise ValueError(
                f"{what} names the futures contract {symbol}: a futures "
                f"exercise or assignment is not read from a mapping; book "
                f"both legs as .tt ASSIGN lines.")
        option = is_option_symbol(symbol)
        if option and f.price_v is None:
            f = f._replace(price_v=0.0, price=0.0)
        if not option and not f.price and (
                f.price_v is not None or money_close(
                    abs(f.amount), abs(f.fee), abs_tol=_ABS_TOL,
                    rel_tol=_REL_TOL)):
            raise ValueError(
                f"{what} is the share leg of an exercise or assignment "
                f"({symbol}) at $0 (its price 0, or its amount its fee "
                f"alone): a share leg is at the strike. A "
                f"warrant's or a right's exercise is a .tt ASSIGN line with "
                f"exercise_of= (its cost rolls into the shares).")
        tx = self._build_trade(r, f, side=side)
        for x in tx:
            net = x["net_amount"]
            if option and (x["price"] or not any(
                    money_close(net, c, abs_tol=_ABS_TOL, rel_tol=_REL_TOL)
                    for c in (0.0, abs(x["fee"])))):
                raise ValueError(
                    f"{what} is the option leg of an exercise or assignment "
                    f"({symbol}) but carries money (price {x['price']:g}, "
                    f"net {x['net_amount']:g}, fee {x['fee']:g}): the "
                    f"premium is in the trade that opened the option and "
                    f"the strike in the share leg, so the option leg is at "
                    f"0 (its fee aside). Map the row that pays it to the "
                    f"share leg, or fix the cell.")
            x["action"] = "ASSIGN"
        return tx

    @builds("split", "rename", "transfer", "opening", "roc", "adjust")
    def _build_event(self, r, f) -> "List[Dict[str, Any]] | Outcome":
        """A split, rename, transfer, opening, return of capital or
        adjustment row (`_event_row`); the Outcome when the builder found
        a non-event (a 1-for-1 with no new ticker), which the template
        counts as it would classify's."""
        st = self._gen
        row, where, raw_action = r.cells, r.where, r.raw_action
        target, desc = r.target, r.desc
        date, clock, currency, row_account = f.date, f.clock, f.currency, f.row_account
        symbol, qty_v, price_v, amount_v = f.symbol, f.qty_v, f.price_v, f.amount_v
        fee_v = f.fee_v
        cell, num, date_fmt = st.cells.cell, st.cells.num, st.date_fmt
        _ev = self._event_row(
            where, target, raw_action, desc,
            _RowCells(row, where, num, cell, date_fmt, st.amount_shared),
            qty_v, price_v, amount_v, fee_v, symbol, currency, date,
            clock, row_account)
        return _ev if isinstance(_ev, Outcome) else [_ev]

    @builds("dividend", "dividend_in_lieu", "tax", "interest", "fee")
    def _build_income(self, r, f) -> List[Dict[str, Any]]:
        """An income-style row: the amount IS the money (a blank refused,
        never 0); a dividend or a payment in lieu, a tax, an interest or
        a fee row under the mapping's sign conventions. The row keeps the
        clock its date format gives, as a trade does (it was stamped
        09:30:00 whatever the cell said; TJ-5018)."""
        st = self._gen
        where, raw_action, target, desc = r.where, r.raw_action, r.target, r.desc
        date, currency = f.date, f.currency
        time = f.clock or "09:30:00"
        row_account, symbol = f.row_account, f.symbol
        amount_v, qty, amount = f.amount_v, f.qty, f.amount
        tax_sign, fee_sign = st.tax_sign, st.fee_sign
        # Income-style rows: the amount IS the money. A blank
        # cell used to book 0 silently.
        if amount_v is None:
            raise ValueError(
                f"generic importer: {where}: {raw_action} row "
                f"(-> {target}) has a blank amount cell — "
                f"refusing to book it as 0. Fill it in, or map "
                f"the action to skip if the row carries no "
                f"money.")
        if target in ("dividend", "dividend_in_lieu"):
            # A payment in lieu (a short-loan substitute payment)
            # is ordinary income, never a dividend (CA-INC-03 /
            # US-INC-01): its own action and type, like IB's
            # (audit A2-1076).
            _pil = target == "dividend_in_lieu"
            return [{
                "action": "DIVIDEND_IN_LIEU" if _pil else "DIVIDEND",
                "date": date, "time": time,
                # Income is dated its payment day: no
                # settlement cycle.
                "date_settle": date,
                "symbol": symbol, "quantity": qty,
                "currency": currency, "price": 0.0,
                # SIGNED: reversals stay negative (schema).
                "net_amount": amount,
                "gross_amount": amount,
                "type": "dividend_in_lieu" if _pil else "dividend",
                "account": self.DEFAULT_ACCOUNT,
                "description": desc,
                **({"broker_account": row_account}
                   if row_account else {}),
            }]
        elif target in ("tax", "interest", "fee"):
            return [{
                "action": target.upper(),
                "date": date, "time": time,
                "date_settle": date,
                "symbol": symbol, "quantity": 0.0,
                "currency": currency,
                # TAX convention: positive = withheld. Most
                # CSVs book withholding as negative cash (flip,
                # like IB/RBC); statements that book it POSITIVE
                # set [formats].tax_sign = "withheld".
                # FEE convention (IB/Questrade/RBC, fx-cash):
                # positive = charged. A cash-signed CSV (the
                # default) books a charge as negative cash —
                # flipped, like tax (R1-124).
                "net_amount": (
                    (amount if tax_sign == "withheld"
                     else -amount) if target == "tax"
                    else (amount if fee_sign == "charged"
                          else -amount) if target == "fee"
                    else amount),
                "type": target,
                "account": self.DEFAULT_ACCOUNT,
                "description": desc,
                **({"broker_account": row_account}
                   if row_account else {}),
            }]
        return []

    def finish(self, transactions: List[Dict[str, Any]], path: Path
               ) -> List[Dict[str, Any]]:
        """After the loop: the pasted exports' shared rows refused, a
        futures fill whose commission crosses its amount resolved by the
        file's cash sign, the file's sign conventions checked (a sell
        with a positive quantity, a buy with a positive amount), the
        unread transfer amounts, the expiry-dated closes, the late
        settles, the assignment shapes, the settle-on-trade-date note and
        the unbooked rows noted, then each newest-first segment put
        oldest first (`_oldest_first`). The split-fill marks are the
        template's, on the rows in that order."""
        st = self._gen
        pasted, fut_crossing = st.pasted, st.fut_crossing
        trade_amounts, trade_signs = st.trade_amounts, st.trade_signs
        expiry_clamped, late_settles = st.expiry_clamped, st.late_settles
        zero_closes, unbooked = st.zero_closes, st.unbooked
        settle_on_trade_date, shown = st.settle_on_trade_date, st.shown
        refuse_pasted_overlap(shown, pasted, "the header")
        if fut_crossing:
            # A futures fill whose commission or rebate is larger than
            # its amount: paid or received both fit the price's sign.
            # A file that signs its cash (a buy row paid is negative)
            # says which; an unsigned one cannot (backlog TJ-0668).
            cash_signed = any(t == "buy" and (a or 0) < 0
                              for _, t, a, _l in trade_amounts)
            for tx, w, t, a, f in fut_crossing:
                if not cash_signed:
                    raise ValueError(
                        f"generic importer: {w}: a futures {t} whose "
                        f"commission ({f:g}) is larger than its amount "
                        f"({abs(a):g}): the cash could go either way "
                        f"(paid or received {abs(a):g}), and the file's "
                        f"amounts carry no sign to tell. Export the "
                        f"amount signed (negative = paid; the file's buys "
                        f"then show it), or enter the fill in a .tt "
                        f"file.")
                net = -a if t == "buy" else a
                tx["net_amount"] = net
                tx["gross_amount"] = _future_notional(t, net, f)
        _resolve_zero_price_futures(st.fut_zero, trade_amounts)
        # A file that signs its quantities (a sell row carried a
        # negative quantity) must not also carry a sell row with a
        # POSITIVE quantity: that row is a buy under the file's own
        # convention, booked as a sell through a mis-mapped action.
        if any(q < 0 for _, _, q, _l in trade_signs):
            bad = [(w, lb) for w, t, q, lb in trade_signs
                   if t == "sell" and q > 0]
            if bad:
                raise ValueError(
                    f"generic importer: {bad[0][0]}: "
                    f"{_a_row(bad[0][1])} with a "
                    f"POSITIVE quantity in a file whose other sell rows "
                    f"carry negative quantities — this row is a buy by "
                    f"the file's own sign convention. Check the "
                    f"[actions] mapping ({len(bad)} such row(s)).")
        # Amounts: a file whose buy rows carry NEGATIVE (cash-out)
        # amounts signs its cash, so a buy-mapped row with a POSITIVE
        # amount is cash in — a sale under one action mapped to buy,
        # booked as a buy (the R1-121 fix covered only the quantity
        # sign; audit A2-0106).
        if any(t == "buy" and (a or 0) < 0 for _, t, a, _l in trade_amounts):
            bad = [(w, lb) for w, t, a, lb in trade_amounts
                   if t == "buy" and (a or 0) > 0]
            if bad:
                raise ValueError(
                    f"generic importer: {bad[0][0]}: "
                    f"{_a_row(bad[0][1])} with a "
                    f"POSITIVE amount (cash in) in a file whose other buy "
                    f"rows carry negative amounts (cash out) — this row is "
                    f"a sale by the file's own sign convention. Map buys "
                    f"and sells to separate action values ({len(bad)} "
                    f"such row(s)).")
        self._note_unread_transfer_amounts(path)
        if expiry_clamped:
            emit_line(f"note: generic importer: {shown}: "
                  f"{len(expiry_clamped)} option close(s) at $0 posted "
                  f"after the contract's expiry are dated the expiry day "
                  f"(CA-DATE-08 / US-DATE-08): "
                  f"{'; '.join(expiry_clamped[:5])}")
        if late_settles:
            emit_line(f"warning: ATTENTION: generic importer: {shown}: "
                  f"{len(late_settles)} trade(s) settle more than "
                  f"{_SETTLE_LATE_DAYS} days after the trade date: "
                  f"{'; '.join(late_settles[:5])}. The tax year follows "
                  f"the settle date — check the settle cells.")
        _say_zero_prices(shown, st.zero_priced)
        claimed = self._check_assign_legs(shown, transactions)
        self._warn_assignment_shapes(shown, zero_closes,
                                     transactions, claimed)
        if settle_on_trade_date and any(
                t.get("action") in ("BUYSELL", "ASSIGN")
                for t in transactions):
            # The README used to route a crypto-only export through here
            # with this option: the coins became SAMPMA.US / SAMPLT.TO shares
            # (Schedule 3 line 4, no pooling with the crypto accounts,
            # and §1091 in a US project) — re-audit A2-0152 / A2-0425.
            emit_line(f"note: generic importer: {shown}: "
                  f"settle_on_trade_date = true settles each trade on its "
                  f"trade date; the rows are still booked as SECURITIES "
                  f"(a listing suffix is added). The generic importer is "
                  f"not a crypto route: coins go in a `crypto = true` "
                  f"account (its exchange's export, or .tt lines with "
                  f"the bare coin symbol).")
        # The broker's accounts and their types against this taxjson
        # account, as Questrade's Account Type is (TJ-2576, TJ-2577): over
        # all the account's exports once (TJ-2584), then this one's when
        # it holds several.
        ctx, key = self.account_context, str(Path(path).resolve())
        if not isinstance(ctx, _GenAccountContext) or key not in ctx.per_file:
            ctx = _generic_account_context([path])
        column = (st.mapping.get("columns") or {}).get("account_type") or "account type"
        plan_kw = dict(broker=(st.mapping.get("broker") or {}).get("name") or "broker",
                       column=column, plural=f"{column} cells", say_unknown=True)
        if not ctx.plans_checked:
            ctx.plans_checked = True
            check_account_plans_across(self, ctx.files, ctx.plan_types,
                                       ctx.plan_files, **plan_kw)
        if len(ctx.per_file[key]) > 1:
            check_account_plans(self, shown, ctx.per_file[key],
                                "the export holds", **plan_kw)
        if st.cash_units:
            listed = "; ".join(st.cash_units[:5])
            more = (f" (+{len(st.cash_units) - 5} more)"
                    if len(st.cash_units) > 5 else "")
            emit_line(f"warning: UNBOOKED: generic importer: "
                  f"{len(st.cash_units)} row(s) mapped to cash carry units "
                  f"and are NOT in the books: {listed}{more}. A cash move "
                  f"carries no shares: enter a transfer in kind as a .tt "
                  f"TRANSFER line, or map its rows to transfer_in or "
                  f"transfer_out.{shipped_layout_hint(path)}")
        if unbooked:
            shown = "; ".join(unbooked[:5])
            more = (f" (+{len(unbooked) - 5} more)"
                    if len(unbooked) > 5 else "")
            emit_line(f"warning: UNBOOKED: generic importer: {len(unbooked)} "
                  f"row(s) with an unmapped action move shares or cash "
                  f"and are NOT in the books: {shown}{more}. Map the "
                  f"action in [actions] or by a [[classify]] rule (or to "
                  f"\"skip\" if it really is not an event)."
                  f"{shipped_layout_hint(path)}")
        return self._oldest_first(transactions)

    def _oldest_first(self, transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Per header-delimited segment (re-audit A2-1084), a newest-first
        export is read bottom-up: a row with no clock is stamped 09:30:00
        (a trade whose date format has one keeps its time), so a day's
        rows tie and keep the order they are emitted in (CA-DATE-14 /
        US-DATE-13), and one row emits at most one transaction, so
        reversing a segment reverses its rows; a segment's transactions
        are those past its header's line (the template noted each row's
        line). Before the template's fill marks (the end of `finish`):
        the second of two identical rows is then the same row in an
        export listing them the other way round (TJ-5038; it ran after
        the marks). Two newest-first exports concatenated went both ways
        over the whole file and were read top-down."""
        st = self._gen
        lines = [self.row_line(tx) for tx in transactions]
        r_bounds = [0] + [r for _, r in st.segments] + [len(st.row_dates)]
        t_bounds = ([0] + [bisect_right(lines, line) for line, _ in st.segments]
                    + [len(transactions)])
        out: List[Dict[str, Any]] = []
        for r0, r1, t0, t1 in zip(r_bounds, r_bounds[1:], t_bounds, t_bounds[1:]):
            part = transactions[t0:t1]
            if self.newest_first(st.row_dates[r0:r1]):
                part.reverse()
            out.extend(part)
        transactions[:] = out
        return transactions

    def release(self) -> None:
        self._gen = None                # the export's cells and mapping released

    def _note_unread_transfer_amounts(self, path) -> None:
        """The transfer rows whose amount cell was left unread (the parse's
        count): one note naming the columns that would read it."""
        _u = self._transfer_amounts_unread
        if _u:
            emit_line(f"note: generic importer: {shown_name(path)}: "
                      f"{len(_u)} transfer row(s) carry an amount that was "
                      f"not read (a transfer books no money; the cell is a "
                      f"market value, a book value or 0). If it is the "
                      f"broker's stated book value, map [columns].book_value "
                      f"to the column; if the value at transfer, "
                      f"[columns].market_value — either may name the amount's "
                      f"own header: {'; '.join(_u[:5])}")

    def _event_row(self, where, target, raw_action, desc, cells, qty_v,
                   price_v, amount_v, fee_v, symbol, currency, date, clock,
                   row_account):
        """A row of one of the event targets as the schema row the broker
        parsers emit, or None for a non-event (step 5b). Each builder answers
        the same four questions: which cell is which (its own cells read
        through `cells`), what money the row may carry, what its no-op is,
        and which shared shape books it already. Every event names its
        symbol."""
        if not symbol:
            raise ValueError(
                f"generic importer: {where}: {raw_action} row (-> {target}) "
                f"has a blank symbol cell and no [defaults].symbol — an "
                f"event names the security it is on.")
        if target in ("split", "rename"):
            return self._split_row(where, target, raw_action,
                                   cells.text("symbol_new"), cells.num("ratio"),
                                   symbol, currency, date, clock, row_account,
                                   amount_v, fee_v, desc)
        if (fee_v or 0.0):
            raise ValueError(
                f"generic importer: {where}: {raw_action} row (-> {target}) "
                f"carries a fee ({fee_v!r}) — a {target} row books none; a "
                f"fee is a fee row.")
        common = {"date": date, "date_settle": date, "symbol": symbol,
                  "currency": currency, "account": self.DEFAULT_ACCOUNT,
                  "description": desc,
                  **({"broker_account": row_account} if row_account else {})}
        if target in ("transfer_in", "transfer_out"):
            return self._transfer_row(where, target, raw_action, qty_v,
                                      amount_v, cells, common, clock)
        if target == "opening":
            return self._opening_row(where, raw_action, qty_v, price_v,
                                     amount_v, cells, common)
        return self._adjust_row(where, target, raw_action, qty_v, price_v,
                                amount_v, common)

    def _transfer_row(self, where, target, raw_action, qty_v, amount_v, cells,
                      common, clock=""):
        """An in-kind transfer as the broker parsers emit it, at the clock
        its date format gives (`clock`; 09:30:00 without one): the units
        arrive or leave among the day's trades where the export puts them
        (stamped 09:30:00 whatever the cell said, units a noon purchase
        bought left before it; TJ-5018). The direction
        is the target's (`transfer_in`, `transfer_out`: exports seldom sign
        the units, so the action says which way), the quantity cell the
        units (its sign may agree with the target, never contradict it).
        No money is booked here: the pipeline nets cross-account pairs and
        books the rest as transfers in or out. The amount and price cells
        are not read on a transfer (an export puts a market value, a book
        value or 0 in the one, the market price in the other); an amount
        left unread is counted for the note after the parse. The evidence
        is opt-in: `[columns].book_value` states the broker's book value
        (on both directions, as the Questrade parser keeps it; the arrival
        reads it as its cost, `lib/transfer_in`; a stated 0 is a stated 0),
        `[columns].market_value` the value at transfer, what the in-kind
        deemed disposition reads. Either may name the amount column's own
        header. A zero quantity moves nothing: refused."""
        if not qty_v:
            raise ValueError(
                f"generic importer: {where}: {raw_action} row (-> {target}) "
                f"needs a non-zero quantity (the units moved), got "
                f"{qty_v!r}.")
        if target == "transfer_in" and qty_v < 0:
            raise ValueError(
                f"generic importer: {where}: {raw_action} row (-> transfer_in) "
                f"has a negative quantity ({qty_v:g}): the units left the "
                f"account — map this action to transfer_out, or the cell's "
                f"sign is wrong.")
        if amount_v and not cells.amount_shared:
            self._transfer_amounts_unread.append(where)
        qty = abs(qty_v) if target == "transfer_in" else -abs(qty_v)
        bv, mv = cells.num("book_value"), cells.num("market_value")
        return {"action": "TRANSFER", "time": clock or "09:30:00",
                "quantity": qty, "price": 0.0, "net_amount": 0.0, "fee": 0.0,
                **({"book_value": abs(bv)} if bv is not None else {}),
                **({"market_value": abs(mv)} if mv else {}),
                **common}

    def _opening_row(self, where, raw_action, qty_v, price_v, amount_v, cells,
                     common):
        """An opening position as a .tt OPENING line books it, through the
        shared builder (`lib/opening.opening_row`; tax-logic CA-OPEN-01 /
        US-OPEN-01): the quantity cell the units (a long position alone),
        the amount cell the position's total cost (its magnitude; a cost
        is never 0; a mapped price must agree with it), the
        `[columns].lot_date` cell the acquisition date when the export has
        one (in the mapping's date format, its date part or ISO; the
        holding period starts at the snapshot day without it). An option's
        contract size is the ticker.map MULT line's, as the trade rows
        read it, carried as `multiplier` when it is not the standard 100.
        The builder refuses a futures contract, a short and a lot date
        after the snapshot day."""
        symbol = common["symbol"]
        if not amount_v:
            raise ValueError(
                f"generic importer: {where}: {raw_action} row (-> opening) "
                f"needs the position's total cost in the amount cell, got "
                f"{amount_v!r} — units held before the data with no cost "
                f"are a .tt `OPENING ... cost=unknown` line.")
        total = abs(amount_v)
        size = None
        if is_option_symbol(symbol):
            from taxjson.lib.option_symbols import parse_option_underlying
            from taxjson.lib.markets import option_contract_size
            declared = float(option_contract_size(
                symbol, parse_option_underlying(symbol) or "") or 0.0)
            if declared and declared != float(self.OPTION_MULTIPLIER):
                size = declared
        per = size or (100.0 if is_option_symbol(symbol) else 1.0)
        if price_v and qty_v and not money_close(
                total, abs(qty_v) * abs(price_v) * per, abs_tol=_ABS_TOL,
                rel_tol=0.01, scale=total):
            raise ValueError(
                f"generic importer: {where}: {raw_action} row (-> opening) "
                f"amount {total:g} is not {abs(qty_v):g} x {abs(price_v):g}"
                f"{' x ' + format(per, 'g') if per != 1 else ''} — the "
                f"amount cell must be the position's TOTAL cost (a per-unit "
                f"book price belongs in the price cell alone).")
        from taxjson.lib.opening import opening_row
        lot, raw_lot = "", cells.text("lot_date")
        if raw_lot:
            dt = self.parse_date(raw_lot, *_day_formats(cells.date_fmt))
            if dt is None:
                raise ValueError(
                    f"generic importer: {where}: {raw_action} row (-> opening) "
                    f"acquisition date {raw_lot!r} is not in [formats].date "
                    f"({cells.date_fmt}), its date part, or YYYY-MM-DD — fix "
                    f"the format or the cell.")
            lot = dt.strftime("%Y-%m-%d")
        try:
            tx = opening_row(symbol=symbol, quantity=qty_v or 0.0,
                             total_cost=total, day=common["date"],
                             currency=common["currency"],
                             account=common["account"], lot_date=lot,
                             size=size, description=common["description"])
        except ValueError as e:
            raise ValueError(
                f"generic importer: {where}: {raw_action} row (-> opening): "
                f"{e}.") from e
        if "broker_account" in common:
            tx["broker_account"] = common["broker_account"]
        return tx

    def _adjust_row(self, where, target, raw_action, qty_v, price_v, amount_v,
                    common):
        """A cost adjustment, at 09:30:00 as every parser stamps one,
        whatever clock the row's date cell carries (CA-DATE-14 /
        US-DATE-13; the ruling on TJ-5018): the clock on such a row is when
        the broker's books posted it, not when the amount became payable.
        At 09:30:00 it follows its day's rows stamped 09:30:00 or earlier
        (every trade of a date-only export) and precedes those clocked
        later; whether it should stand ahead of every trade of its day is
        an open ruling (backlog TJ-5142). A .tt ADJUST line keeps the time
        written on it. `roc`, a return of capital, is the base class's
        shared shape (`tx_roc_adjust`): the amount cell is the cash
        received, signed as the export signs cash, so a receipt lowers the
        pool's cost and a broker's reversal row nets it out again; `adjust`
        is the amount as given, a signed cost change. A zero amount adjusts
        nothing, and a row with units or a price is a trade mapped wrong:
        both refused."""
        if not amount_v:
            raise ValueError(
                f"generic importer: {where}: {raw_action} row (-> {target}) "
                f"needs a non-zero amount, got {amount_v!r}.")
        if qty_v or price_v:
            raise ValueError(
                f"generic importer: {where}: {raw_action} row (-> {target}) "
                f"carries a quantity or price ({qty_v!r} / {price_v!r}) — "
                f"a cost adjustment moves no units; a trade row is buy or "
                f"sell.")
        if target == "roc":
            tx = self.tx_roc_adjust(symbol=common["symbol"],
                                    currency=common["currency"],
                                    date=common["date"],
                                    desc=common["description"],
                                    amount=amount_v,
                                    account=common["account"])
            if "broker_account" in common:
                tx["broker_account"] = common["broker_account"]
            return tx
        return {"action": "ADJUST", "time": "09:30:00",
                "quantity": 0.0, "price": 0.0, "net_amount": amount_v,
                "gross_amount": 0.0, **common}

    def _split_row(self, where, target, raw_action, new_raw, ratio_v, symbol,
                   currency, date, clock, row_account, amount_v, fee_v, desc):
        """A `split` or `rename` row as one SPLIT row (the broker parsers'
        shape: the pool scaled by the ratio and renamed, cost and
        acquisition dates carried), or None for a non-event. `new_raw` is
        the row's new-ticker cell (read for both targets: a split that
        changes the ticker renames too; a rename needs one), `ratio_v` its
        ratio cell, new units per old unit (2 for a 2-for-1 split, 0.1 for a
        1-for-10 consolidation): a split needs one, a rename with none (a
        blank cell; a 0 is refused) is 1.
        The quantity cell is never the ratio: exports put the shares
        received or held there (TJ-2077). Money on the row (a cash-in-lieu
        sale, a fee) is refused: the split books none, and a .tt line sells
        the fraction (TJ-2078). A ratio of 1 with no new ticker is nothing
        to book (TJ-2083, TJ-2087)."""
        if (amount_v or 0.0) or (fee_v or 0.0):
            raise ValueError(
                f"generic importer: {where}: {raw_action} row (-> {target}) "
                f"carries an amount or fee ({amount_v!r} / {fee_v!r}) — a "
                f"split or rename row books no money. A cash-in-lieu sale "
                f"of the fraction is a .tt line; a fee is a fee row.")
        if target == "rename" and not new_raw:
            raise ValueError(
                f"generic importer: {where}: {raw_action} row (-> rename) "
                f"has a blank new-ticker cell ([columns].symbol_new) — name "
                f"the new ticker there, or map the row to split for a ratio "
                f"alone.")
        # A blank cell alone is a rename's 1-for-1: a 0 written there is a
        # ratio the export got wrong, refused as a negative one is (it
        # read as blank and renamed at 1; TJ-5019).
        ratio = ratio_v if ratio_v is not None else (
            1.0 if target == "rename" else None)
        if ratio is None or ratio <= 0:
            raise ValueError(
                f"generic importer: {where}: {raw_action} row (-> {target}) "
                f"needs a positive ratio in the ratio cell (new units per "
                f"old unit: 2 for a 2-for-1 split, 0.1 for a 1-for-10 "
                f"consolidation), got {ratio_v!r}."
                + (" A rename at 1-for-1 leaves the cell blank; if the "
                   "export writes 0 there on every row that is no split, "
                   "unmap [columns].ratio and read the ratio from the "
                   "description with an [extract].ratio pattern."
                   if target == "rename" else ""))
        if ratio >= 1000:
            raise ValueError(
                f"generic importer: {where}: {raw_action} row (-> {target}) "
                f"ratio {ratio:g} reads as a share count, not a ratio "
                f"(new units per old unit; a 50-for-1 split is 50) — map "
                f"[columns].ratio to the cell that holds the ratio, or "
                f"enter the split in a .tt line.")
        new_symbol = self._listing_symbol(new_raw, currency) if new_raw else ""
        if new_symbol == symbol:
            new_symbol = ""
        if ratio == 1.0 and not new_symbol:
            return Outcome.nonevent(
                f"{raw_action} {symbol}: a 1-for-1 with no new ticker "
                f"(nothing to book)")
        return {
            "action": "SPLIT",
            "date": date, "time": clock or "09:30:00",
            "date_settle": date,
            "symbol": symbol, "symbol_new": new_symbol,
            "quantity": ratio,
            "currency": currency, "price": 0.0, "net_amount": 0.0,
            "account": self.DEFAULT_ACCOUNT,
            "description": desc,
            **({"broker_account": row_account} if row_account else {}),
        }

    def _classify_row(self, row, cell, pulled, where, actions, defaults,
                      mapping):
        """A row's action label, target and description (step 5c). The
        action cell (or [defaults].action) is looked up in [actions]; a
        label not there takes the first [[classify]] rule whose regex is
        found in the cell it reads; None when nothing maps it. Then the
        [extract] patterns of the fields the target books (`_FIELDS_OF`;
        every pattern for a row nothing maps, so the UNBOOKED probe sees
        what it moves) run over the description cell and fill `pulled`,
        the overlay `cell` reads for a field whose own column is unmapped
        or blank (a two-group ratio is new units over old; a value is
        checked as a number where a booked row reads it). The description
        is the description cell when mapped and non-blank, else the
        action label (as every parser keeps the broker's text)."""
        pulled.clear()
        desc = str(cell(row, "description")).strip()
        label = str(cell(row, "action")).strip() or str(
            defaults.get("action", ""))
        raw_action = action_key(label)
        target = actions.get(raw_action)
        if target is None:
            text = {"action": label, "description": desc}
            target = next((t for rx, over, t, act in mapping["_classify"]
                           if text[over] and rx.search(text[over])
                           and (act is None or act.search(label))), None)
        fields = (_EXTRACT_KEYS if target is None
                  else _FIELDS_OF.get(target, ("symbol",)))
        for field, rx in mapping["_extract"].items():
            if field == "option":
                # The contract's parts, read where the export writes it
                # (the symbol cell, else the description), with where and
                # whether the match is the whole cell; made a symbol, and
                # checked, only when a booked row reads it.
                for src, t in (("symbol", str(cell(row, "symbol"))), ("description", desc)):
                    m = rx.search(t) if field in fields and t else None
                    if m is not None:
                        pulled[field] = {g: (m.group(g) or "").strip()
                                         for g in _OPTION_GROUPS}
                        pulled[field].update(
                            _from=src, _whole=m.group(0).strip() == t.strip())
                        break
                continue
            m = rx.search(desc) if desc and field in fields else None
            if m is None:
                continue
            # A group that did not take part (optional, an alternation)
            # is absent; a value is checked as a number only when a
            # booked row reads it (`num`), so a skipped row's text never
            # refuses the file.
            found = [g.strip() for g in m.groups() if g is not None]
            if len(found) == 2:     # a ratio's new units over its old
                try:
                    new, old = (parse_strict_number(g, field=field)
                                for g in found)
                    found = [repr(new / old)] if old else [m.group(0)]
                except BrokerageParseError:
                    found = [m.group(0)]
            if found:
                pulled[field] = found[0]
        return raw_action, target, desc or raw_action

    @staticmethod
    def _units_of(row, cell, num) -> str:
        """What a `cash` row carries that a cash move does not: a
        quantity, a ticker, a new ticker or a ratio (as the message says
        it), else ""."""
        try:
            qty = num(row, "quantity", "")
        except ValueError:
            qty = 1.0               # unparseable: treat as live
        if qty:
            return "a quantity"
        for f, what in (("symbol", "a ticker"), ("symbol_new", "a new ticker")):
            if str(cell(row, f) or "").strip():
                return what
        try:
            ratio = num(row, "ratio", "")
        except ValueError:
            ratio = 1.0
        return "a ratio" if ratio else ""

    def _note_unbooked(self, row, cell, num, raw_action, where, unbooked):
        """An unmapped action's row that moves shares, cash or a ticker is a
        real event the mapping does not book (a DRIP reinvest left the
        position short; a rename nobody mapped): surfaced as UNBOOKED, not
        just counted (R1-129). A mapped fee is cash too (TJ-0972); a ratio
        cell is a split nobody mapped (TJ-2086)."""
        moves = False
        for f in ("quantity", "amount", "fee", "ratio"):
            try:
                v = num(row, f, "")
            except ValueError:
                v = 1.0             # unparseable: treat as live
            if v:
                moves = True
        if str(cell(row, "symbol_new") or "").strip():
            moves = True
        if moves:
            w = (f"{where}: action {raw_action or '?'!r} (not in [actions], "
                 f"no [[classify]] rule) carries a quantity/amount/fee, a "
                 f"ratio or a new ticker")
            unbooked.append(w)
            self.lint_findings.append(f"{w} — not booked")

    def _option_size(self, symbol: str, target: str, qty: float,
                     price: float, amount_v: Optional[float], fee: float,
                     fee_mapped: bool, is_future: bool) -> Tuple[float, str]:
        """(premium multiplier, basis) of an option row; the export
        never states a contract's size. The row's own money decides,
        when it has a price and an amount: the standard 100, else a
        ticker.map `MULT` size that fits ("declared"), else a mini's 10
        (option_row_multiplier: "assumed" / "derived", as the Questrade,
        RBC and Webull parsers do). With no amount to show it, 100,
        unless MULT declares a mini's 10. `MULT` otherwise sizes the
        deliverable: an adjusted series' 50 shares still cost its premium
        x 100, and reading it as the premium multiplier refused the
        row's real cash, or cut a price-only row's to N/100 (backlog
        TJ-1034). A declared mini's own size is tried first, and a mini
        with no MULT line (the cash used to be x 100 whatever the row
        said) is found by its amount (backlog TJ-0863). A futures option
        keeps the old 100 (its cash is the amount column; no qty x price
        check runs)."""
        std = float(self.OPTION_MULTIPLIER)
        if is_future:
            return std, "assumed"
        from taxjson.lib.option_symbols import parse_option_underlying
        from taxjson.lib.markets import option_contract_size
        und = parse_option_underlying(symbol) or ""
        declared = float(option_contract_size(symbol, und) or 0.0)
        is_mini = declared == float(self.MINI_OPTION_MULTIPLIER)
        if not price or not amount_v:
            return (declared, "declared") if is_mini else (std, "assumed")
        cash, f = abs(amount_v), abs(fee or 0.0)

        def fits(m: float) -> bool:
            gross = abs(qty) * abs(price) * m
            tol = money_tolerance(abs_tol=_ABS_TOL, rel_tol=_REL_TOL,
                                  scale=max(gross, 1.0))
            if fee_mapped:
                # A charge or a rebate, and a sale whose commission
                # exceeds its gross (it nets negative).
                return any(abs(cash - c) <= tol
                           for c in (gross + f, gross - f, abs(gross - f)))
            # No fee column: the amount carries a commission of its own
            # (back_compute_fee's allowance).
            gap = cash - gross if target == "buy" else gross - cash
            return -tol <= gap <= _commission_allowance(qty)

        def fee_within_limit(m: float) -> bool:
            # The commission a fit with no fee column infers, against the
            # row check's limit.
            gross = abs(qty) * abs(price) * m
            gap = cash - gross if target == "buy" else gross - cash
            return gap <= _MAX_FEE_SHARE * gross

        if not declared:
            return self.option_row_multiplier(symbol, fits)
        # (When nothing fits, the money check names the first size.)
        order = (declared, std) if is_mini else (std, declared)
        # A low price fits 100 by reading the gap as a commission the row
        # check then refuses; the declared size shows the amount without
        # one (backlog TJ-1239).
        if (not is_mini and not fee_mapped and not fee_within_limit(std)
                and fits(declared) and fee_within_limit(declared)):
            order = (declared, std)
        for m in order:
            if fits(m):
                break
        else:
            m = declared if is_mini else std
        return m, ("declared" if m == declared else "assumed")

    @staticmethod
    def _check_trade_shape(where: str, target: str, raw_action: str,
                           qty: float, price_v: Optional[float],
                           amount_v: Optional[float], fee: float,
                           mult: float, is_future: bool = False,
                           label: str = "") -> None:
        """Refuse a buy/sell row that cannot be booked without a guess:
        no quantity, no price AND no amount (the fee alone became the
        whole cost or proceeds, audit R1-120), a zero-cost stock buy,
        or a quantity/amount sign that contradicts the mapped action
        (a sell under an action mapped to buy was booked as a buy,
        audit R1-121). A message names the row's own target (`label`:
        an assignment's leg is its side's money, `target`)."""
        label = label or target
        if not qty:
            raise ValueError(
                f"generic importer: {where}: {raw_action} row (-> "
                f"{label}) has a blank or zero quantity.")
        if is_future and not (amount_v or 0.0):
            raise ValueError(
                f"generic importer: {where}: {raw_action} row is a "
                f"futures contract with no amount — the contract size "
                f"(multiplier) is never guessed, so qty x price cannot "
                f"give the cost/proceeds. Map the fee-inclusive "
                f"[columns].amount (or enter the trade in a .tt file).")
        if price_v is None and amount_v is None:
            raise ValueError(
                f"generic importer: {where}: {raw_action} row (-> "
                f"{label}) has neither a price nor an amount — the "
                f"{'cost' if target == 'buy' else 'proceeds'} would be "
                f"{'the fee alone' if fee else '0'}. Fill one in, or map "
                f"the action to skip (a transfer-in needs its real cost "
                f"as a .tt BUYSELL).")
        if (target == "buy" and mult == 1.0 and not (price_v or 0.0)
                and not (amount_v or 0.0)):
            raise ValueError(
                f"generic importer: {where}: {raw_action} row is a stock "
                f"buy at ZERO cost (price and amount both 0). A zero-cost "
                f"buy is almost always a transfer or journal row — map "
                f"it to skip and supply the real cost basis (.tt).")
        if target == "buy" and qty < 0:
            raise ValueError(
                f"generic importer: {where}: {raw_action} is mapped to "
                f"{label} but the quantity is NEGATIVE ({qty:g}) — a sell by "
                f"its own sign. Map sells to their own action value; "
                f"booking it as a buy would drop the disposition.")
        _gross = abs(qty) * abs(price_v or 0.0) * mult
        # (A stated zero price counts: its fee is the whole cash —
        # backlog TJ-0731.)
        _neg_net = (fee > _gross
                    and (_gross > 0 or (price_v is not None and not price_v
                                        and not is_future))
                    and money_close(amount_v or 0.0, fee - _gross,
                                    abs_tol=_ABS_TOL, rel_tol=_REL_TOL,
                                    scale=fee - _gross))
        if (target == "sell" and qty > 0 and (amount_v or 0.0) < 0
                and not _neg_net
                and not (is_future and (price_v or 0.0) < 0)):
            # (Not a sell whose commission exceeds its gross: that one
            # really is cash out — S057-02. Nor a futures sale at a
            # negative price, which pays cash too — audit #70.)
            raise ValueError(
                f"generic importer: {where}: {raw_action} is mapped to "
                f"{label} but the quantity is positive and the amount "
                f"negative ({amount_v:g}, cash paid out) — a buy by its "
                f"own signs. Map buys to their own action value.")

    @staticmethod
    def _days(a: str, b: str) -> int:
        """Calendar days from ISO date `a` to `b` (0 when either is not
        an ISO date)."""
        try:
            return (datetime.strptime(b, "%Y-%m-%d")
                    - datetime.strptime(a, "%Y-%m-%d")).days
        except ValueError:
            return 0

    def _check_assign_legs(self, name: str, transactions) -> set:
        """The file's option ASSIGN legs paired with their share legs as
        the engines pair them (core `_pair_assign_legs`: a marked leg, or a
        trade at the strike or of the delivered size, in the assignment's
        direction, on the option's account and underlying, 3 days before
        to 7 after, best first). A paired option leg takes its share leg's
        settle date, as an exercise or an assignment takes its stock
        leg's (CA-DATE-04 / US-DATE-04). An unpaired one beside only
        marked share legs the other way is refused: its side contradicts
        them. One with no share leg here is left to the engines (another
        export may hold it; an unclaimed premium is named at the end of
        the run). Returns the claimed share legs (their ids), which the
        assignment-shape check leaves alone."""
        import contextlib
        import io
        from taxjson.engine.assign import (
            _ASSIGN_LEG_LEAD_DAYS, _MARKED_LEG_MAX_LAG_DAYS, _assign_direction,
            _make_assign_underlying_resolver, _pair_assign_legs, _signed_day_gap,
        )
        from taxjson.model.transaction import TaxTransaction
        from taxjson.lib.contract_size import OPTION_CONTRACT_SHARES
        if not any(t.get("action") == "ASSIGN"
                   and is_option_symbol(t["symbol"]) for t in transactions):
            return set()
        fields = TaxTransaction.__dataclass_fields__
        txs, row_of = [], {}
        for n, r in enumerate(transactions):
            if r.get("action") not in ("BUYSELL", "ASSIGN"):
                continue
            t = TaxTransaction(**{k: v for k, v in r.items() if k in fields})
            t.id, t.account = f"gen-{n}", str(r.get("broker_account") or "")
            if is_option_symbol(t.symbol) and not r.get("multiplier"):
                # The engines' own size, without their once-said note of
                # an assumed 100 (theirs to say, not the parse's).
                t.multiplier, t.contract_size_basis = (
                    OPTION_CONTRACT_SHARES, "declared")
            row_of[t.id] = r
            txs.append(t)
        txs.sort(key=lambda t: (t.date, t.time))   # stable: the file's order
        with contextlib.redirect_stderr(io.StringIO()):
            resolve = _make_assign_underlying_resolver(
                txs, lambda t: t.date, quiet=True)
            pairs = _pair_assign_legs(txs, lambda _t: True, resolve)
        claimed = {i for legs in pairs.values() for i in legs}
        for o in txs:
            if o.action != "ASSIGN" or not is_option_symbol(o.symbol):
                continue
            if o.id in pairs:
                row_of[o.id]["date_settle"] = row_of[pairs[o.id][0]]["date_settle"]
                continue
            d, und = _assign_direction(o), resolve(o)
            if not d or not und or not any(
                    s.action == "ASSIGN" and s.id not in claimed
                    and s.symbol == und and s.account == o.account
                    and (float(s.quantity) > 0) != (d > 0)
                    and -_ASSIGN_LEG_LEAD_DAYS
                    <= (_signed_day_gap(o.date, s.date) or 0)
                    <= _MARKED_LEG_MAX_LAG_DAYS for s in txs):
                continue
            line = self.row_line(row_of[o.id])
            buy = float(o.quantity) > 0
            raise ValueError(
                f"generic importer: {name}"
                f"{f' line {line}' if line else ''}: the option leg "
                f"{o.symbol} of {o.date} "
                f"({'assign_buy' if buy else 'assign_sell'}) "
                f"{'buys' if d > 0 else 'sells'} the shares, but the "
                f"share leg beside it {'sells' if d > 0 else 'buys'} "
                f"them. A written put assigned is assign_buy on both "
                f"legs, a written call the call's assign_buy and the "
                f"shares' assign_sell, a held call exercised the call's "
                f"assign_sell and the shares' assign_buy, a held put "
                f"exercised assign_sell on both.")
        return {id(row_of[i]) for i in claimed}

    def _warn_assignment_shapes(self, name: str, zero_closes, transactions,
                                claimed=frozenset()) -> None:
        """An assigned or exercised option whose rows the mapping sends to
        buy and sell, not to `assign_buy` / `assign_sell`, arrives as a $0
        option close plus a stock trade at the strike, booked as an
        expiry plus an unrelated trade:
        the premium is realized as its own gain instead of folding into
        the shares' cost or proceeds (CA-OPT-06, s.49(3)/(3.1); audit
        A2-0629). The pairing is not inferred (no broker-specific
        exercise fee tells it from a trade at a round strike); each
        candidate is an ATTENTION line. A share leg an option ASSIGN leg
        claimed (`claimed`, from `_check_assign_legs`) is that option's."""
        import re as _re
        hits = []
        for z in zero_closes:
            m = _re.match(r'^([A-Z0-9.]+?)(\d{6})([CP])(\d{8})(?:\.(\w+))?$',
                          z["symbol"])
            if not m:
                continue
            under = m.group(1) + (f".{m.group(5)}" if m.group(5) else "")
            strike = int(m.group(4)) / 1000.0
            # Shares per contract: ticker.map MULT (the option or its
            # root), else the standard 100 (B10).
            from taxjson.lib.markets import contract_size as _csize
            shares = abs(z["qty"]) * float(
                _csize(z["symbol"], m.group(1).split(".", 1)[0]) or 100)
            for t in transactions:
                if (t.get("action") not in ("BUYSELL", "ASSIGN")
                        or t["symbol"] != under or id(t) in claimed):
                    continue
                if abs(abs(float(t["quantity"])) - shares) > 1e-6:
                    continue
                if abs(float(t.get("price") or 0) - strike) > 0.005:
                    continue
                gap = GenericBrokerage._days(z["date"], t["date"])
                if -1 <= gap <= 7:
                    hits.append(f"{z['symbol']} closed at $0 on "
                                f"{z['date']} ({z['where']}) + "
                                f"{abs(float(t['quantity'])):g} {under} at "
                                f"the strike {strike:g} on {t['date']}")
                    break
        if hits:
            emit_line(f"warning: ATTENTION: generic importer: {name}: "
                  f"{len(hits)} possible exercise/assignment(s) booked as "
                  f"an expiry plus a separate trade: {'; '.join(hits[:5])}"
                  f": the premium is NOT folded into the shares' cost or "
                  f"proceeds"
                  f"{self.law(' (s.49(3)/(3.1))', ' (Rev. Rul. 78-182)')}"
                  f". If the statement shows one, map those rows' actions "
                  f"(or a [[classify]] rule) to assign_buy / assign_sell, "
                  f"the legs in and out.")

    @staticmethod
    def _settle_for(row, cell, where: str, date: str, settle_fmt: str,
                    symbol: str, currency: str, is_option: bool,
                    settle_on_trade_date: bool,
                    futures_settle: str = "trade") -> str:
        """Settlement date of a trade row: the mapped `settle` column
        when the cell is filled, else the standard cycle from the
        shared holiday-aware helper (T+1 since the 2024 cutover, T+2
        before, T+3 before 2017-09-05, T+5 before 1995-06-07 in USD and
        CAD; options T+1), on the listing's
        market (lib/dates.settlement_calendar: a Canadian suffix settles
        on the Canadian calendar, in US dollars on the days both Canada
        and the US settle, .US on the US one, .L/.AX on the UK/ASX cycle,
        otherwise the row currency). Booking the
        trade date put a Dec-31 sale in the wrong Canadian tax year
        (audits R1-123/R1-185). `[options] settle_on_trade_date = true`
        settles on the trade date; futures (`F:`, `/`, `\\`)
        on the trade date, or the next settlement day under
        futures_settle = "next_day" (CA-DATE-09/10, US-DATE-09/12)."""
        raw = str(cell(row, "settle")).strip()
        if raw:
            try:
                sd = datetime.strptime(raw, settle_fmt)
            except ValueError:
                raise ValueError(
                    f"generic importer: {where}: unparseable settle date "
                    f"{raw!r} with [formats].settle={settle_fmt!r}")
            settle = sd.strftime("%Y-%m-%d")
            if settle < date:
                raise ValueError(
                    f"generic importer: {where}: settle date {settle} is "
                    f"before the trade date {date} — are the date and "
                    f"settle columns swapped?")
            if GenericBrokerage._days(date, settle) > _SETTLE_REFUSE_DAYS:
                raise ValueError(
                    f"generic importer: {where}: settle date {settle} is "
                    f"more than {_SETTLE_REFUSE_DAYS} days after the trade "
                    f"date {date} — no listed security settles that late, "
                    f"and the tax year follows the settle date. Fix the "
                    f"cell (or blank it for the standard cycle).")
            return settle
        if symbol.upper().startswith(FUTURES_PREFIXES):
            if futures_settle == "next_day":
                from taxjson.lib.dates import market_of
                from taxjson.lib.market_calendar import add_settlement_days
                return add_settlement_days(
                    date, 1, market_of(symbol, currency)).isoformat()
            return date
        if settle_on_trade_date:
            return date
        from taxjson.lib.dates import settlement_calendar
        return settlement_date(date, settlement_calendar(symbol, currency),
                               is_option)
