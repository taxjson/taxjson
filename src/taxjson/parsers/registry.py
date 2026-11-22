"""Every parser taxjson ships, in one place (step 5e of the engine
refactor): its id (what `tjs run` records as the source brokerage), the
name shown, the other spellings `taxjson-brokerage --brokerage` accepts,
its module and class, its tier (`TIERS`), whether it extracts corporate
actions (taxjson-corp-actions), whether it reads an exchange's coins
(`crypto`: the stablecoin model applies), the file-name prefixes and words
of the name fallback, whether it reads the run's rates and whether its
parse builds the export's cash events. The detection tables, the brokerage tool's
registrations and the corporate-action brokers are generated from it: a
parser missing here is not a parser. The content detectors stay in
lib/brokerages/detect, the module that owns them, held to this list by
tests/test_parser_registry.py. The order is the order the name fallback
tries prefixes in."""
from importlib import import_module
from typing import Dict, NamedTuple, Tuple

TIERS = {1: "layout", 2: "parser", 3: "extractor"}


class ParserSpec(NamedTuple):
    id: str
    display: str
    aliases: Tuple[str, ...]
    module: str
    cls: str
    tier: int                   # TIERS
    corp_actions: bool          # a corporate-action extractor exists
    crypto: bool                # an exchange's coins (stablecoins modelled)
    name_prefixes: Tuple[str, ...]
    name_words: Tuple[str, ...]
    # It reads the run's rates (work/to_base.csv): the parse stage gives
    # them (`--rates`) and declares them as the parse's input for it only.
    # Coinbase and Kraken check a stablecoin fill for a de-peg through them
    # (its notes in the parse's .diag), and Kraken values a fiat leg of a
    # multi-leg receipt with them, which sets the split's shares (TJ-2497,
    # TJ-2508). tests/test_freshness_edges.py holds the parser modules that
    # name the rates' readers to this mark.
    reads_rates: bool = False
    # Its parse builds the export's cash events, what the FX-on-cash ledger
    # reads (the parser's `cash_events` or its `cash_problem`, written by
    # taxjson-brokerage --cash-out: lib/cash_events `cash_sidecar`; PLAN
    # step 16). A book of any other broker (Webull, the generic importer)
    # that moves foreign cash is refused until a `CASHBOOK <book> complete`
    # line says its .tt cash lines are all of them (`cash_event_brokers`).
    # tests/test_one_copy.py holds it to the parsers that build them.
    builds_cash: bool = False


_B = "taxjson.lib.brokerages."
SPECS: Tuple[ParserSpec, ...] = (
    ParserSpec("ib", "Interactive Brokers", ("interactive_brokers",),
               _B + "ib_extractor", "IbBrokerage", 3, True, False, (), (),
               builds_cash=True),
    ParserSpec("questrade", "Questrade", ("qt",),
               _B + "questrade", "QuestradeBrokerage", 2, True, False, (), (),
               builds_cash=True),
    ParserSpec("webull", "Webull", ("wb",),
               _B + "webull", "WebullBrokerage", 2, False, False, (), ()),
    ParserSpec("rbc_direct", "RBC Direct Investing", ("rbc",),
               _B + "rbc_direct", "RbcBrokerage", 2, True, False, (), (),
               builds_cash=True),
    ParserSpec("coinbase", "Coinbase", ("cb",),
               _B + "coinbase", "CoinbaseBrokerage", 2, False, True,
               ("cb_",), ("coinbase",), reads_rates=True, builds_cash=True),
    ParserSpec("kraken", "Kraken", ("kr",),
               _B + "kraken", "KrakenBrokerage", 2, False, True,
               ("kr_",), ("kraken",), reads_rates=True, builds_cash=True),
    # The column-mapped importer for unsupported brokers; a generic_ file
    # with no mapping still goes there so its "no mapping" message says
    # which file to write.
    ParserSpec("generic", "generic", (),
               _B + "generic", "GenericBrokerage", 1, False, False, ("generic_",), ()),
)


def spec(id_or_alias: str) -> ParserSpec:
    """The parser an id or one of its spellings names; ValueError
    (`Unknown brokerage: <id>`, the brokerage tool's usage error) for
    none."""
    key = str(id_or_alias).strip().lower()
    for s in SPECS:
        if key == s.id or key in s.aliases:
            return s
    raise ValueError(f"Unknown brokerage: {id_or_alias}")


def parser_class(spec_or_id):
    """The parser class, imported on request: of a ParserSpec, or of an
    id or spelling (one lookup). A parser that cannot be loaded (its
    module missing or failing to import, its class misspelled) is a
    ValueError naming it, the usage error the tools print on one line."""
    s = spec_or_id if isinstance(spec_or_id, ParserSpec) else spec(spec_or_id)
    try:
        return getattr(import_module(s.module), s.cls)
    except (ImportError, AttributeError) as e:
        raise ValueError(f"parser {s.id}: cannot load {s.module}.{s.cls}: "
                         f"{e}") from e


def corp_action_brokers() -> frozenset:
    """The parser ids with a corporate-action extractor
    (taxjson-corp-actions); the others skip that stage."""
    return frozenset(s.id for s in SPECS if s.corp_actions)


def rate_reading_brokers() -> frozenset:
    """The parser ids that read the run's rates: the parse stage gives them
    the rates file and declares it as their input (TJ-2497, TJ-2508)."""
    return frozenset(s.id for s in SPECS if s.reads_rates)


def cash_event_brokers() -> frozenset:
    """The parser ids whose parse builds the export's cash events
    (`builds_cash`): the FX-on-cash ledger reads their books' cash from the
    run's sidecars, and refuses a book of any other broker that moves
    foreign cash until its .tt cash lines are declared complete."""
    return frozenset(s.id for s in SPECS if s.builds_cash)


def recorded_id(label: str) -> str:
    """The id a recorded source brokerage folds to: a spelling's parser
    id, else the label as recorded (`generic:<name>`, a .tt's, anything
    the registry does not know) — the fees report's fold."""
    try:
        return spec(label).id
    except ValueError:
        return label


def display_names() -> Dict[str, str]:
    return {s.id: s.display for s in SPECS}


def name_prefixes() -> Tuple[Tuple[str, str], ...]:
    return tuple((p, s.id) for s in SPECS for p in s.name_prefixes)


def name_words() -> Tuple[Tuple[str, str], ...]:
    return tuple((w, s.id) for s in SPECS for w in s.name_words)
