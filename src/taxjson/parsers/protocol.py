"""The parse, as every parser does it (step 5h of the engine refactor):
`read(path)` the rows of one export, with whatever the parser prepares
from them (its account context, a pre-pass over paired rows); per row
`classify(row)`, an `Outcome`: skip it (counted under a label), count a
non-event (a header, a footer, a balance line), take it as consumed by
an earlier row (a leg booked with its partner), or build; `build(row)`
the schema rows, or the Outcome (a skip, a non-event) the parser finds
only while building (the generic importer's 1-for-1 split), counted by
the template like classify's: the template is the one counter (a build
that counts the row itself is a parser bug the template refuses);
`finish(transactions, path)` the parser's pairings and notes after the
loop (rows counted one way that are another after all move their count
with `BaseBrokerage.recount`: a row consumed into the parser's state that
the finish finds is a non-event or a skip; moving more rows than were
counted so is a parser bug the template refuses). A skipped row has one
of three classes, the Outcome's kind: `skip`, listed in the summary as
unclassified; `nonevent`, a recognized non-event, listed calmly;
`reported`, counted and not listed, for a row the parser's own note
already reports (an UNBOOKED warning);
`note_cash(row)` (from classify, or from read: a row it drops, or a row
read as the export holds it, IB's before its temporary symbols are
folded) keeps a row the export's cash events are built from, in the
order noted, and the parser's finish builds them from those rows
(pairing what pairs) into `cash_events`, which taxjson-brokerage
--cash-out writes beside the books (PLAN step 16 commits 7 to 11: RBC,
Coinbase, Questrade, IB, Kraken, whose readers in lib/cash_readers went;
the registry's `builds_cash`; a parser that builds none leaves it None,
Webull and the generic importer); a cash cell the finish cannot read (one
the parse itself never reads) leaves the file no events and its
`cash_problem`, the file's problem in the sidecar, never a refused parse
(as a reader's error was): the finish hands
its build to `BaseBrokerage._cash_events_from(build)`, which does that;
`release()` what the parser kept, called whatever happened;
`new_transactions()` the list the parse builds into, asked after `read`
(a new one by default; a parser whose row undoes rows built before it,
Interactive Brokers' `Ca` cancellations, hands its own).
`BaseBrokerage.parse_rows` is the parse up to the parser's finish (what
a parser reading another file for its hints calls: Webull's sibling
exports, which release it themselves); `BaseBrokerage.parse_file` is
the template over them: it runs `parse_rows`, which notes each row's
line (the row contract: a row `read` returns
carries its export line as its `line` attribute, a row object of the
parser's, never the reader's bare dict, whose keys are the export's
columns; a parser keeping it elsewhere overrides `row_line_of`), counts
and consumes, and after `finish` runs the closing steps every parser
shares (the settlement clamp on the expiries `build` kept in
`_expiries`, the split fills on the rows without an `id` of their own (a
row with its id, an exchange's transaction id, needs no fill mark), then
`after_marks`, the parser's step
after them, for what must see the marks or must not be seen by them (an
output order, a costing), then the skip summary). The template counts
the rows it iterates as seen (`_rows_seen`, after what `read` adds for
rows it saw and will not return: footers and duplicates it counted as
non-events), so the lint reconciliation (seen = consumed + skipped)
holds for every converted parser by construction; a `read` that finds
the file is not its export sets the count to None and returns no rows,
and the tool warns about the file as it always did. `read` may return a generator: a parser whose per-row checks
must come in file order with its classification and build (the generic
importer) reads lazily. Detection (`sniff`) is lib/brokerages/detect's,
by the registry. Every parser is on the template (RBC, the generic
importer, Questrade, Webull, Coinbase, Kraken, Interactive Brokers since
step 5h (8) of PLAN.md); none overrides `parse_file`."""
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence, Set, Tuple, TypeVar

from taxjson.lib.row_types import OPENING_TYPE, STOCK_DIVIDEND
from taxjson.lib.trade_cancel import TRADE_CANCEL_TYPE


KINDS = ("skip", "nonevent", "reported", "consumed", "build")

# The kinds of row a parse books (PLAN step 16), each one a name the rows
# already carry: an action the schema knows (lib/brokerages/schema
# KNOWN_ACTIONS: a BUYSELL is a trade), a BUYSELL the parse kept as an
# expiry (`expiry_rows`), a SPLIT to another symbol (a rename: schema's
# SPLIT convention), a BUYSELL of one of the row types a parser sets
# (lib/row_types STOCK_DIVIDEND and OPENING_TYPE, lib/trade_cancel
# TRADE_CANCEL_TYPE), an ADJUST of type 'roc' (`tx_roc_adjust`) and any
# other ADJUST. `row_kind` reads one off a row; nothing stores it, so no
# row's bytes change. A fixture folder claims the kinds its rows have
# (fixture.toml `kinds`, the conformance kit holds the claim).
ROW_KINDS = ("trade", "expiry", "assign", "split", "rename", "stock_dividend",
             "opening", "trade_cancel", "dividend", "dividend_in_lieu", "tax",
             "interest", "fee", "transfer", "roc", "adjust")
# The kinds of the cash events a broker's export holds (lib/cash_lines
# `conv`, `move`, `balance`, `flow`; Interactive Brokers' statement period
# and its notes), claimed as `cash_kinds`.
CASH_KINDS = ("FXCONV", "CASHMOVE", "CASHBAL", "FLOW", "PERIOD", "NOTE")
_ACTION_KINDS = {"ASSIGN": "assign", "DIVIDEND": "dividend",
                 "DIVIDEND_IN_LIEU": "dividend_in_lieu", "TAX": "tax",
                 "INTEREST": "interest", "FEE": "fee", "TRANSFER": "transfer"}
_TRADE_TYPES = {"": "trade", STOCK_DIVIDEND: "stock_dividend", OPENING_TYPE: "opening",
                TRADE_CANCEL_TYPE: "trade_cancel"}


def row_kind(tx: Mapping[str, Any], expiries: Iterable[Mapping[str, Any]] = ()
             ) -> Optional[str]:
    """The kind (ROW_KINDS) of one parsed row, None for a row of none (an
    action no parser books, OPENING_BALANCE or DISALLOW, or a BUYSELL of a
    type the pipeline sets). `expiries`: the parse's `expiry_rows`, the
    rows themselves (a BUYSELL among them is an expiry)."""
    action, typ = tx.get("action"), tx.get("type") or ""
    if action == "BUYSELL":
        if any(e is tx for e in expiries):
            return "expiry"
        return _TRADE_TYPES.get(typ)
    if action == "SPLIT":
        new = tx.get("symbol_new") or ""
        return "rename" if new and new != tx.get("symbol") else "split"
    if action == "ADJUST":
        return "roc" if typ == "roc" else "adjust"
    return _ACTION_KINDS.get(str(action))


# The attribute `builds` sets on a parser's method: the kinds it books.
_BUILDS = "row_kinds_built"
_F = TypeVar("_F", bound=Callable[..., Any])


def builds(*kinds: str) -> Callable[[_F], _F]:
    """Declares the kinds of row (ROW_KINDS) a parser's method books: each
    builder, and `finish`, `after_marks` or an account-phase stage where
    they book rows of their own (Webull's assignments, Interactive Brokers'
    contract-id renames). `parser_kinds` is their union over a class. The
    conformance kit holds every row a fixture parses to it, and the suite
    every declared kind to a fixture's claim (tests/test_parser_check.py),
    so a parser that books a kind no fixture holds says so here."""
    bad = [k for k in kinds if k not in ROW_KINDS]
    if bad or not kinds:
        raise ValueError(f"builds({', '.join(map(repr, kinds))}): "
                         + (f"{', '.join(map(repr, bad))} is no kind: " if bad else "no kind: ")
                         + f"the kinds are {', '.join(ROW_KINDS)}")

    def mark(fn: _F) -> _F:
        setattr(fn, _BUILDS, tuple(dict.fromkeys(kinds)))
        return fn
    return mark


def parser_kinds(cls: type) -> Tuple[str, ...]:
    """The kinds a parser class declares it books (`builds` on its methods
    and its bases'), in ROW_KINDS order."""
    found: Set[str] = set()
    for klass in cls.__mro__:
        for member in vars(klass).values():
            fn = getattr(member, "__func__", member)    # a classmethod's or staticmethod's
            found.update(getattr(fn, _BUILDS, ()))
    return tuple(k for k in ROW_KINDS if k in found)
# How the base's private skip counters encode a skipped row's class (the kind
# of its Outcome): a recognized non-event (the summary lists it calmly), a row
# the parser's own note already reports (counted, not listed), else
# unrecognized (listed with a call for a new branch). BaseBrokerage writes and
# reads the encoding alone (`skip_classes`): every parser answers Outcomes,
# Interactive Brokers since step 5h (8) (TJ-2410, TJ-2486).
KNOWN_NONEVENT_PREFIX = 'non-event '
REPORTED_PREFIX = 'reported '

# What taxjson-brokerage passes the account phase by keyword, the one list
# (TJ-2424): the tool's calls pass these (tests/test_parser_check.py reads its
# source), and the conformance kit checks that each stage takes them.
PREPARE_KEYWORDS = ("tax_year", "combined", "taxable", "symbol_codes", "project")
COVERAGE_KEYWORDS = ("country", "year_end_posting")

# The template's surface, frozen before the Interactive Brokers conversion
# (TJ-2321): every public member of BaseBrokerage, by role.
# tests/test_parse_protocol.py holds the class to this list, so a new hook or
# helper is an edit here, reviewed as a change to the protocol.
SURFACE = {
    # The parse a parser writes: the hooks it overrides.
    "hooks": ("read", "classify", "build", "finish", "after_marks", "release",
              "row_line_of", "new_transactions"),
    # The account phase taxjson-brokerage runs once per account, and what
    # the caller hands each parse (the account context, the sibling finder).
    "account": ("prepare_files", "coverage_messages", "reconcile_files", "configure",
                "account_context", "statement_accounts", "sibling_finder"),
    # The parse the tools run and what they read after it: `cash_events`,
    # the file's cash events where the parse builds them (PLAN step 16
    # commits 7 to 11; None for a parser that builds none: the registry's
    # builds_cash), and
    # `cash_problem`, why they could not be built ("" when they were;
    # commit 8); `symbol_spellings`, the symbol spellings the parse read
    # (lib/symbol_case, backlog TJ-4606).
    "result": ("parse_file", "parse_rows", "row_counts", "skip_classes",
               "emit_skip_summary", "lint_findings", "instant_of_line", "expiry_rows",
               "broker_name", "zero_tx_reason", "cash_events", "cash_problem",
               "symbol_spellings"),
    # The counting a parser does beside its Outcomes: the rows read drops,
    # the finish's moves, the export line of a row, the rows its cash
    # events are built from (`note_cash`).
    "counting": ("count_skip", "count_nonevent", "note_row_consumed", "recount",
                 "note_row_line", "row_line", "rows_noting_lines", "note_cash"),
    # The settings `configure` sets (every parsers.settings.ParseSettings
    # name), the law the country gives and the basis fields the rows
    # depend on (`BASIS_INPUTS`, which the conformance check holds).
    "settings": ("country", "law", "security_overrides", "account_taxable",
                 "combined_broker_accounts", "transfer_costs_checked_downstream",
                 "futures_settle", "exercise_fee",
                 "stablecoins_as_cash", "plans_taxed", "BASIS_INPUTS"),
    # The shared readers: dates, numbers, options, settlement, money.
    "helpers": ("apply_currency_suffix", "back_compute_fee", "check_settle_order",
                "clamp_settlement_across", "clamp_settlement_to_expiry", "clean_number",
                "content_stem", "disambiguate_split_fills", "equity_settlement_date",
                "format_occ_symbol", "newest_first", "non_option_expiry_booking_date",
                "option_expiry_booking_date", "option_row_multiplier", "parse_date",
                "parse_option_from_description", "parse_strict_number", "require_columns",
                "settlement_date_t1", "signed_quantity", "strict_option_from_description",
                "theoretical_gross", "trade_date_from_settlement", "tx_roc_adjust",
                "warn_zero_cost_buy"),
    # The constants a parser reads. The skip counters' label prefixes are
    # this module's, the base's encoding, not the base's members.
    "constants": ("CURRENCY_EXT_FALLBACK", "CURRENCY_EXT_MAP", "DEFAULT_ACCOUNT",
                  "MINI_OPTION_MULTIPLIER", "OPTION_MULTIPLIER"),
}


@dataclass(frozen=True)
class RowCounts:
    """What one parse counted (`BaseBrokerage.row_counts`), the one reading
    the tools and the conformance kit make of the template's counters: the
    rows it iterated (`seen`; None when the file was not the parser's
    export), the rows consumed and the rows counted as skipped, every
    class. `unaccounted`: seen less the other two, a dropped row when
    positive, a row counted twice when negative."""
    seen: Optional[int]
    consumed: int
    skipped: int

    @property
    def unaccounted(self) -> Optional[int]:
        return None if self.seen is None else self.seen - self.consumed - self.skipped


class Row:
    """What a parser's row object has at least: its cells, its export line
    (the row contract) and, once `classify` chose it, the builder `build`
    runs for it (`build(fn)` keeps it and answers the build Outcome). A
    parser derives its row class, adding what its classify learns."""
    __slots__ = ("cells", "line", "builder")

    def __init__(self, cells, line: int):
        self.cells, self.line, self.builder = cells, line, None

    def build(self, builder) -> "Outcome":
        """Classify's answer for a row to build: the builder kept on the row."""
        self.builder = builder
        return Outcome.build()


@dataclass(frozen=True, slots=True)
class Outcome:
    """What `classify` says of a row. Built through the four constructors
    below; a kind outside KINDS is a parser bug, refused when the Outcome
    is built (a TypeError, which no refusal catch swallows as the file's
    fault)."""
    kind: str                   # one of KINDS
    label: str = ""             # the skip or non-event category
    txs: Sequence[dict] = ()    # a consumed row's rows, booked elsewhere

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise TypeError(f"an Outcome's kind is one of {KINDS}, not "
                            f"{self.kind!r} (a parser bug: parsers.protocol)")

    @classmethod
    def skip(cls, label: str) -> "Outcome":
        return cls("skip", label)

    @classmethod
    def reported(cls, label: str) -> "Outcome":
        """A skip the parser's own note already reports (an UNBOOKED
        warning, a note of its own): counted, not in the summary."""
        return cls("reported", label)

    @classmethod
    def nonevent(cls, label: str) -> "Outcome":
        return cls("nonevent", label)

    @classmethod
    def consumed(cls, txs: Sequence[dict] = ()) -> "Outcome":
        return cls("consumed", "", tuple(txs))

    @classmethod
    def build(cls) -> "Outcome":
        return cls("build")
