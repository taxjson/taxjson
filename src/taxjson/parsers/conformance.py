"""The conformance checks every parser passes on a synthetic fixture
folder (step 5g of the engine refactor): `sample.csv` beside
`expected.json`. The schema (no errors and no warnings: fixtures are
curated), the golden (the output equals expected.json exactly),
determinism (two fresh instances agree), row accounting (every row
consumed or counted as skipped, the expected count when the caller
knows it), detection (`tjs run`'s router sends the fixture to the
parser) and the countries (under each country's settings the fixture
parses, passes the schema and leaves no row unaccounted; at a fixed
basis the rows are the same under each country and under none, and
the same under every basis but in the fields the parser declares) and
the kinds (every row and cash event is of a kind, `parsers.protocol`
ROW_KINDS and CASH_KINDS, and each kind the folder's fixture.toml claims
has one). tests/parser_conformance.py asserts them per fixture folder; `tjs
parser-check` runs them for any registered parser, a third party's
fixture folder included. Each check returns findings, [] when it
passes."""
import csv
import inspect
import io
import itertools
import json
import re
from contextlib import redirect_stderr
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from taxjson.rules.country import COUNTRIES, NEUTRAL_BASIS, parse_basis
from taxjson.parsers.protocol import (CASH_KINDS, COVERAGE_KEYWORDS, PREPARE_KEYWORDS,
                                      ROW_KINDS, parser_kinds, row_kind)
from taxjson.parsers.settings import ParseSettings
from taxjson.parsers.toolbox.errors import BrokerageParseError

# What a parser may raise on a fixture it refuses or cannot read: a
# finding, never a traceback (the parsers' own guards list these).
_REFUSALS = (ValueError, OSError, csv.Error, KeyError, IndexError,
             BrokerageParseError)
# A finding is a headline the reader acts on, then its detail on the
# lines after (the command prints the headline as an `! ` line).
_NL = "\n"


def _fresh(parser_cls):
    """A fresh parser that reads a fixture's siblings as the run does (the
    run's detection: lib/brokerages/detect same_broker_siblings; Kraken's
    trades fixture reads its ledger)."""
    from taxjson.lib.brokerages.detect import same_broker_siblings
    parser = parser_cls()
    parser.sibling_finder = same_broker_siblings
    return parser


def parse_fixture(parser_cls, sample: Path) -> Tuple[object, list]:
    """(parser, rows) of a fresh parser on the fixture, its notes on
    stderr swallowed. Bare (the class's defaults) unless the folder's
    fixture.toml sets a parse setting (`fixture_settings`): then it is
    told that alone, at no country."""
    parser = _fresh(parser_cls)
    given = fixture_settings(Path(sample).parent)
    if given:
        parser.configure(ParseSettings(**given))
    with redirect_stderr(io.StringIO()):
        txs = parser.parse_file(sample)
    return parser, txs


_META_LINE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(-?\d+(?:\.\d+)?|\"[^\"]*\")\s*(?:#.*)?$")


# What a fixture folder claims its rows hold (PLAN step 16): fixture.toml's
# `kinds` (ROW_KINDS) and `cash_kinds` (CASH_KINDS, the cash events its
# parse builds), each a quoted list of names.
CLAIMS = {"kinds": ROW_KINDS, "cash_kinds": CASH_KINDS}
# What a fixture folder's fixture.toml may tell the parse: the ParseSettings
# fields an account sets (`[accounts.<name>]`), each a number. Webull's
# exercise_fee: with it the parser infers an assignment.
FIXTURE_SETTINGS = ("exercise_fee",)


def read_fixture_meta(fixture_dir: Path) -> Dict[str, object]:
    """The fixture folder's `fixture.toml`, when it has one: `expected_skips`,
    the rows the parser must count as skipped (absent: no row accounting
    is expected); `kinds` and `cash_kinds`, the kinds its rows and its cash
    events have (`CLAIMS`: a tuple of names; absent, none is asked for);
    the parse settings it is told (`FIXTURE_SETTINGS`).
    The one place they live, read by the suite and the command alike. A
    flat file of `key = number` or `key = "text"` lines (comments
    allowed), read here without a TOML library: the parsers sit below the
    project layer. A claim of a name that is no kind is a ValueError, as a
    line that is no `key = value` is, and a count or a setting that is no
    number."""
    p = Path(fixture_dir) / "fixture.toml"
    if not p.is_file():
        return {}
    out: Dict[str, object] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _META_LINE.match(line)
        if not m:
            raise ValueError(f"{p}: not a `key = number` or `key = \"text\"` "
                             f"line: {line.strip()!r}")
        key, val = m.group(1), m.group(2)
        out[key] = (val[1:-1] if val.startswith('"')
                    else float(val) if "." in val else int(val))
        if key == "expected_skips" and not isinstance(out[key], int):
            raise ValueError(f"{p}: expected_skips is a count of rows: {line.strip()!r}")
        if key in FIXTURE_SETTINGS and isinstance(out[key], str):
            raise ValueError(f"{p}: {key} is a number: {line.strip()!r}")
    for key, known in CLAIMS.items():
        if key in out:
            out[key] = _claim(p, key, out[key], known)
    return out


def _claim(p: Path, key: str, value: object, known: Sequence[str]) -> Tuple[str, ...]:
    """A claim's names, in its order, once each (`read_fixture_meta`)."""
    if not isinstance(value, str):
        raise ValueError(f"{p}: {key} is a quoted list of names, such as "
                         f"{key} = \"{known[0]}, {known[1]}\"")
    names = tuple(dict.fromkeys(n for n in re.split(r"[,\s]+", value) if n))
    bad = [n for n in names if n not in known]
    if bad:
        raise ValueError(f"{p}: {key} names {', '.join(map(repr, bad))}, "
                         f"{'which is' if len(bad) == 1 else 'which are'} no kind: "
                         f"the kinds are {', '.join(known)} (parsers.protocol)")
    return names


def fixture_settings(fixture_dir: Path) -> Dict[str, Any]:
    """The parse settings the folder's fixture.toml gives
    (`FIXTURE_SETTINGS`), which every parse of its sample is told; {} for
    none, or when the file cannot be read (`check` reports it)."""
    try:
        meta = read_fixture_meta(fixture_dir)
    except ValueError:
        return {}
    return {key: meta[key] for key in FIXTURE_SETTINGS if key in meta}


def unclaimed_note(fixture_dir: Path) -> Optional[str]:
    """What `tjs parser-check` says of a folder whose fixture.toml claims
    no kinds (`CLAIMS`, a third party's folder): it passes, and no kind is
    asked for. None when it claims both (or cannot be read: `check` says
    so)."""
    try:
        meta = read_fixture_meta(fixture_dir)
    except ValueError:
        return None
    missing = [key for key in CLAIMS if key not in meta]
    if not missing:
        return None
    what = " or ".join({"kinds": "row", "cash_kinds": "cash"}[k] for k in missing)
    keys = " and ".join(f'{k} = "..."' for k in missing)
    return (f"{fixture_dir} claims no {what} kinds: none is asked for ({keys} in its "
            f"fixture.toml)")


def fixture_parser_id(fixture_dir: Path) -> str:
    """The parser a fixture folder is for: its fixture.toml's `parser`
    (a second export's fixture, `kraken_ledgers`), else the folder's
    name, which a fixture.toml that cannot be read leaves too (`check`
    of the folder reports the file)."""
    try:
        named = read_fixture_meta(fixture_dir).get("parser")
    except ValueError:
        named = None
    return str(named or Path(fixture_dir).name)


def fixture_dirs(fixtures: Path, parser_id: str) -> List[Path]:
    """One parser's fixture folders under `fixtures` (the checkout's
    tests/fixtures): every folder `fixture_parser_id` gives to it, the
    one named after it first, then in name order; none: the folder
    named after it (missing, which the caller reports)."""
    own = Path(fixtures) / parser_id
    mine = sorted(d for d in Path(fixtures).iterdir()
                  if d.is_dir() and fixture_parser_id(d) == parser_id)
    return sorted(mine, key=lambda d: d != own) or [own]


def schema_findings(txs) -> List[str]:
    from taxjson.lib.brokerages.schema import validate_transactions
    errors, warnings = validate_transactions(txs)
    return ([f"schema error{_NL}{e}" for e in errors]
            + [f"schema warning{_NL}{w}" for w in warnings])


def golden_findings(txs, golden: Path, *, update: bool = False,
                    hint: str = "UPDATE_GOLDEN=1") -> List[str]:
    """The output against the golden; `update` writes the golden first,
    so a reviewed change is recorded rather than refused; `hint` names
    the caller's switch for that (the suite's environment variable, the
    command's --update-golden)."""
    return _golden_findings(txs, golden, update, hint, ("golden", "parser's output",
                                                        "the output", "row(s)"))


# The folder's golden of the cash events of the sample (`cash_events_of`: the
# parse's), beside expected.json (PLAN step 16).
CASH_GOLDEN = "expected_cash.json"


def cash_golden_findings(cash: Sequence[Dict[str, Any]], golden: Path, *,
                         update: bool = False, hint: str = "UPDATE_GOLDEN=1") -> List[str]:
    """The cash events (`cash_events_of`) against the folder's cash golden
    (CASH_GOLDEN), as `golden_findings` holds the rows: `update` writes it
    first. A folder without one passes while its parse builds no events
    (a broker without them, a folder of one's own)."""
    if not update and not cash and not golden.exists():
        return []
    return _golden_findings(cash, golden, update, hint, ("cash golden", "parse's cash events",
                                                         "the cash events", "event(s)"))


def _golden_findings(values: Sequence[Any], golden: Path, update: bool, hint: str,
                     said: Tuple[str, str, str, str]) -> List[str]:
    """`values` against the JSON file `golden`; `said`: what the file is,
    what writes it, what diverged and what is counted (the findings'
    words)."""
    what, source, subject, unit = said
    if update:
        golden.write_text(json.dumps(values, indent=1, sort_keys=True) + "\n",
                          encoding="utf-8")
    if not golden.exists():
        return [f"missing {what} {golden.name}{_NL}write it from the {source} "
                f"({hint}) and review it"]
    try:
        expected = json.loads(golden.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [f"the {what} {golden.name} cannot be read{_NL}{e}"]
    if values == expected:
        return []
    return [f"{subject} diverged from the {what} {golden.name}{_NL}"
            f"{len(values)} {unit} now, {len(expected)} expected: if the change "
            f"is intentional, regenerate the {what} ({hint}) and review the "
            f"diff"]


def determinism_findings(parser_cls, sample: Path, first=None) -> List[str]:
    """Two fresh instances agree; `first` is a parse the caller already
    has (one fewer parse)."""
    try:
        if first is None:
            _, first = parse_fixture(parser_cls, sample)
        _, second = parse_fixture(parser_cls, sample)
    except _REFUSALS as e:
        return [f"a second parse of the fixture raised{_NL}{e}"]
    return ([] if first == second
            else ["parse_file is not deterministic across fresh instances"])


def accounting_findings(parser, expected_skips: Optional[int] = None
                        ) -> List[str]:
    """Row accounting, from the parser's `row_counts`: the rows it counted
    as skipped (against the expected count when known) and none neither
    consumed nor counted, a silent-drop code path, or counted twice. A
    parser with no `row_counts` (not on BaseBrokerage's template) is a
    finding (TJ-2415)."""
    counts = _row_counts(parser)
    if counts is None:
        # One text whatever the expected count, so the countries' parses,
        # which expect none, do not repeat it (TJ-2419).
        return [f"the parser does no row-level accounting{_NL}it has no row_counts: "
                f"a parser derives from BaseBrokerage"]
    out = []
    if expected_skips is not None and counts.skipped != expected_skips:
        out.append(f"{counts.skipped} row(s) counted as skipped, {expected_skips} "
                   f"expected")
    if counts.seen is not None:
        unaccounted = counts.unaccounted
        if unaccounted > 0:
            out.append(f"{unaccounted} fixture row(s) neither consumed nor "
                       f"counted as skipped{_NL}a silent-drop code path")
        elif unaccounted < 0:
            out.append(f"{-unaccounted} fixture row(s) counted twice{_NL}a code "
                       f"path counts a row the template counts too (consumed and "
                       f"skipped, or skipped twice)")
    return out


def kind_findings(txs: Sequence[Dict[str, Any]], expiries: Iterable[Dict[str, Any]] = (),
                  claimed: Optional[Sequence[str]] = None,
                  cash: Sequence[Dict[str, Any]] = (),
                  cash_claimed: Optional[Sequence[str]] = None,
                  declared: Optional[Sequence[str]] = None) -> List[str]:
    """The kinds: every row is of one (`row_kind`, `expiries` the parse's
    expiry rows), one the parser declares (`declared`, `parser_kinds`;
    None: not asked), and every cash event (`cash`) of one of CASH_KINDS;
    each kind the folder claims (`claimed`, `cash_claimed`: fixture.toml's,
    None when it claims none) has a row, or an event."""
    out = []
    expiries = list(expiries)
    kinds = [row_kind(t, expiries) for t in txs]
    none = [t for t, k in zip(txs, kinds) if k is None]
    if none:
        t = none[0]
        out.append(f"{len(none)} row(s) of no kind{_NL}the first: action {t.get('action')!r}"
                   + (f", type {t.get('type')!r}" if t.get("type") else "")
                   + "; a parser books the kinds parsers.protocol ROW_KINDS names")
    undeclared = [k for k in ROW_KINDS if k in kinds and declared is not None
                  and k not in declared]
    if undeclared:
        out.append(f"the parser books {', '.join(undeclared)}, which it does not declare"
                   f"{_NL}the method that books a kind declares it (parsers.protocol "
                   f"builds); it declares {', '.join(declared or ()) or 'none'}")
    stray = [e for e in cash if e.get("kind") not in CASH_KINDS]
    if stray:
        out.append(f"{len(stray)} cash event(s) of no kind{_NL}the first: kind "
                   f"{stray[0].get('kind')!r}; the kinds are {', '.join(CASH_KINDS)}")
    for what, claim, have, word in (("row", claimed, kinds, "no row has"),
                                    ("cash", cash_claimed, [e.get("kind") for e in cash],
                                     "the parse's cash events have none of")):
        missing = [k for k in (claim or ()) if k not in have]
        if missing:
            out.append(f"the folder claims {', '.join(missing)}, which {word}{_NL}a claimed "
                       f"{what} kind needs one in sample.csv: add it, or drop the claim "
                       f"from fixture.toml")
    return out


def declared_findings(parser_cls: type, folders: Iterable[Path]) -> List[str]:
    """Each kind the parser declares (`parser_kinds`) is claimed by one of
    its fixture folders (fixture.toml `kinds`), so every kind it books has
    a row the kit checks: the checkout's folders (a folder of one's own
    need not hold them all)."""
    claimed: Set[str] = set()
    for d in folders:
        try:
            claimed.update(_claims(read_fixture_meta(d), "kinds") or ())
        except ValueError:
            pass                                # `check` of the folder reports it
    missing = [k for k in parser_kinds(parser_cls) if k not in claimed]
    if not missing:
        return []
    return [f"the parser declares {', '.join(missing)}, which no fixture folder claims"
            f"{_NL}a declared kind needs a row in a fixture: add one and claim it "
            f"(fixture.toml kinds), or drop the declaration (parsers.protocol builds)"]


def cash_events_of(parser_id: str, parser: object
                   ) -> Tuple[List[Dict[str, Any]], List[str]]:
    """(the cash events, findings) of the fixture: those `parser`'s parse
    of it built (its `cash_events`; its --cash-out sidecar holds the same),
    none for a parser that builds none (Webull, the generic importer, a
    contributor's class without them). A parse that could not build them
    (its `cash_problem`) is a finding, as is a parse of a broker the
    registry marks `builds_cash` that built none."""
    problem = getattr(parser, "cash_problem", "")
    if problem:
        return [], [f"the parse could not build the fixture's cash events{_NL}{problem}"]
    built = getattr(parser, "cash_events", None)
    if built is not None:
        return list(built), []
    from taxjson.parsers.registry import cash_event_brokers
    if parser_id in cash_event_brokers():
        return [], [f"the parse built no cash events of the fixture{_NL}{parser_id}'s "
                    f"parse builds them (the registry's builds_cash): its finish sets "
                    f"cash_events"]
    return [], []


def _row_counts(parser):
    """The parser's counts, None for a parser off the template (the kit
    checks a contributor's class too)."""
    read = getattr(parser, "row_counts", None)
    return read() if callable(read) else None


def _outcome_of(parser, txs) -> tuple:
    """What the identical rows compare of one parse: its rows, its counts
    and its skip counts by class (`skip_classes`: two parses that skip as
    many rows for different reasons differ; None off the template)."""
    read = getattr(parser, "skip_classes", None)
    return (txs, _row_counts(parser), read() if callable(read) else None)


# What taxjson-brokerage hands each stage of the account phase: all of
# parsers.protocol's keywords, so an override that takes less is a TypeError
# when an account is parsed (TJ-2412, TJ-2424).
ACCOUNT_CALLS = (
    ("prepare_files", ((),), dict.fromkeys(PREPARE_KEYWORDS)),
    ("coverage_messages", (None, 2000), dict.fromkeys(COVERAGE_KEYWORDS)),
    ("reconcile_files", ([],), {}),
)


def account_phase_findings(parser_cls) -> List[str]:
    """The account phase: each stage takes what taxjson-brokerage passes
    it (the base's signatures, `ACCOUNT_CALLS`)."""
    out = []
    for name, args, kwargs in ACCOUNT_CALLS:
        stage = getattr(parser_cls, name, None)
        if stage is None:
            out.append(f"the parser has no {name}{_NL}a parser derives from BaseBrokerage")
            continue
        try:
            inspect.signature(stage).bind(*args, **kwargs)
        except TypeError as e:
            out.append(f"{name} does not take what taxjson-brokerage passes it{_NL}{e}"
                       + (f"; the account's keywords: {', '.join(kwargs)}" if kwargs
                          else ""))
    return out


def detection_findings(parser_id: str, sample: Path, *, route) -> List[str]:
    """Detection: `tjs run` routes the fixture to the parser. `route` is
    the router (`lib/brokerages/detect.detect`, handed by the caller: the
    kit sits beside the parsers and imports none of them), with all it
    reads: the detectors on the content, a sidecar or a layout's mapping
    (the generic importer's fixture is routed by its sidecar), a
    positions report's shape. A sample two exports' detectors claim is
    refused (AmbiguousBroker), never routed."""
    try:
        routed = route(sample)
    except _REFUSALS as e:
        return [f"tjs run would refuse the fixture{_NL}{e}"]
    if routed.broker == parser_id:
        return []
    return [f"tjs run would not read the fixture with {parser_id}{_NL}routed to "
            f"{routed.broker or 'no parser'}"
            + (f" ({routed.reason})" if routed.reason else "")
            + (f"{_NL}{routed.error}" if getattr(routed, "error", "") else "")]


def bases() -> List[Dict[str, object]]:
    """Every basis a parse can be told (rules/country `parse_basis`): each
    basis field at every value no country or a country gives it, in every
    combination (the stablecoins as property or as cash; no plan, or a US
    project's, taxed as ordinary), in a fixed order."""
    values: Dict[str, list] = {name: [] for name in NEUTRAL_BASIS}
    for country in (None,) + tuple(COUNTRIES):
        for name, value in parse_basis(country).items():
            if value not in values[name]:
                values[name].append(value)
    return [dict(zip(values, combo)) for combo in itertools.product(*values.values())]


def _basis_key(basis) -> tuple:
    return tuple((name, basis[name]) for name in NEUTRAL_BASIS)


def _said(value) -> str:
    if isinstance(value, frozenset):
        return "{" + ", ".join(sorted(value)) + "}" if value else "none"
    return repr(value)


def _basis_said(basis) -> str:
    return ", ".join(f"{name} = {_said(basis[name])}" for name in NEUTRAL_BASIS)


def _parse_under(parser_cls, sample: Path, settings):
    """(parser, rows, None) of a fresh parser told `settings` and the
    folder's own (`fixture_settings`), its notes swallowed; (parser, None,
    the error) when it refuses the fixture."""
    parser = _fresh(parser_cls)
    try:
        parser.configure(replace(settings, **fixture_settings(Path(sample).parent)))
        with redirect_stderr(io.StringIO()):
            return parser, parser.parse_file(sample), None
    except _REFUSALS as e:
        return parser, None, e


def _outcomes(parser_cls, sample: Path, parsed):
    """The outcome of the fixture's parse at (country, basis): None when
    the parser refuses it, else `_outcome_of`'s; each parsed once, kept in
    `parsed`."""
    def outcome(country, basis):
        key = (country, _basis_key(basis))
        if key not in parsed:
            parser, txs, error = _parse_under(
                parser_cls, sample, ParseSettings(country=country, **basis))
            parsed[key] = None if error is not None else _outcome_of(parser, txs)
        return parsed[key]
    return outcome


def _basis_differences(outcome, every, name) -> list:
    """The pairs of bases (`every`) that differ in the field `name` alone
    and give different outcomes at no country."""
    return [(a, b) for a, b in itertools.combinations(every, 2)
            if [n for n in NEUTRAL_BASIS if a[n] != b[n]] == [name]
            and outcome(None, a) != outcome(None, b)]


def basis_effects(parser_cls, sample: Path) -> List[str]:
    """The basis fields whose value alone changes the fixture's parse (at
    no country): what the parser's `BASIS_INPUTS` must name for this
    fixture, and what each field it names must do on at least one of its
    fixtures (a declaration no fixture needs is stale: TJ-4200)."""
    outcome, every = _outcomes(parser_cls, sample, {}), bases()
    return [name for name in NEUTRAL_BASIS if _basis_differences(outcome, every, name)]


def _identical_rows_findings(parser_cls, sample: Path, parsed) -> List[str]:
    """The identical rows (`country_findings`): at each basis (`bases`) the
    rows, counts and skips by class (`_outcome_of`) under each country are
    those under no country; at no country, the outcomes under two bases
    differing in one field are the same unless the parser names the field
    in its `BASIS_INPUTS`. A refusal is an outcome like rows. `parsed`: the
    outcomes the caller has, by (country, basis), added to."""
    declared = tuple(getattr(parser_cls, "BASIS_INPUTS", ()) or ())
    out = [f"BASIS_INPUTS names {name!r}, which is no basis field{_NL}the basis is "
           f"{', '.join(NEUTRAL_BASIS)} (rules/country parse_basis)"
           for name in declared if name not in NEUTRAL_BASIS]
    outcome, every = _outcomes(parser_cls, sample, parsed), bases()
    for country in COUNTRIES:
        differ = [b for b in every if outcome(country, b) != outcome(None, b)]
        if differ:
            out.append(f"the rows under {country} differ from those under no country at "
                       f"the same basis{_NL}at {'; '.join(map(_basis_said, differ))}: a "
                       f"parse reads the country only through its basis (rules/country "
                       f"parse_basis), and the country words its messages alone")
    for name in NEUTRAL_BASIS:
        if name in declared:
            continue
        differ = _basis_differences(outcome, every, name)
        if differ:
            a, b = differ[0]
            out.append(f"the rows depend on {name}, which the parser does not declare{_NL}"
                       f"they differ between {name} = {_said(a[name])} and "
                       f"{_said(b[name])}: name it in the class's BASIS_INPUTS (a basis "
                       f"input), or book the same rows under either")
    return out


def country_findings(parser_cls, sample: Path, *, known=()) -> List[str]:
    """The countries: under each country's settings (`ParseSettings`: the
    stablecoin fold, the plans taxed as ordinary, the wording) the fixture
    parses without a refusal, passes the schema and leaves no row
    unaccounted. The rows and the skip count may differ between the
    countries' bases by design (the golden and `expected_skips` are the
    default settings'), and only there: the identical rows
    (`_identical_rows_findings`) hold that at a fixed basis each country's
    rows, counts and skips by class are those of no country, and that a
    basis field the parser does not declare in its `BASIS_INPUTS` changes
    none of them. A finding in `known` (the default parse's) is not
    repeated per country.

    What the identical rows cannot see (TJ-4201): each parse is a fresh
    parser told its settings, in one process, at an unstated account type
    (TJ-4190), and only its rows, counts and skips are compared. So a
    country or a basis read through a module global, the environment or a
    cache the first parse fills, and one that reaches only a side list such
    as `expiry_rows`, pass. No parser on the tree reads one (2026-11-17)."""
    if not callable(getattr(parser_cls, "configure", None)):
        # A parser off the template (TJ-2418): one finding, not a traceback.
        return [f"the parser has no configure{_NL}a parser derives from BaseBrokerage: "
                f"each country's settings reach it through configure"]
    out = []
    parsed: Dict[tuple, object] = {}
    for country in COUNTRIES:
        settings = ParseSettings.for_country(country)
        parser, txs, error = _parse_under(parser_cls, sample, settings)
        parsed[(country, _basis_key(parse_basis(country)))] = (
            None if error is not None else _outcome_of(parser, txs))
        if error is not None:
            out.append(f"the parser refused the fixture under {country}{_NL}{error}")
            continue
        out += [f"under {country}: {f}"
                for f in schema_findings(txs) + accounting_findings(parser)
                if f not in known]
    return out + _identical_rows_findings(parser_cls, sample, parsed)


def _claims(meta: Dict[str, object], key: str) -> Optional[Tuple[str, ...]]:
    value = meta.get(key)
    return tuple(value) if isinstance(value, tuple) else None


def check(parser_cls, fixture_dir: Path, *, expected_skips: Optional[int] = None,
          update_golden: bool = False, hint: str = "UPDATE_GOLDEN=1",
          route=None, parser_id: Optional[str] = None, account_phase: bool = True
          ) -> Tuple[List[str], int, int]:
    """Every check on the fixture folder: (findings, rows parsed, rows
    counted as skipped); `hint` the caller's switch for writing the
    golden. `expected_skips` None: the folder's fixture.toml says;
    `route` (the router, `detect.detect`) None: no detection check, which
    asks that it routes the sample to `parser_id` (None: the folder's,
    `fixture_parser_id`). `account_phase` False: the class's account phase
    is not checked (a caller checking a parser's second folder, TJ-2425).
    The kinds (`kind_findings`) are the folder's claims (fixture.toml) of
    the rows and of the cash events its parse builds, and the
    kinds the parser declares (`parser_kinds`); that each declared kind is
    claimed is the parser's, over its folders (`declared_findings`).
    The golden is rewritten only when the output
    passes the schema: a non-conformant output is never recorded as the
    contract; the cash golden (CASH_GOLDEN) whenever the parse's cash
    events are taken. They are taken only for a folder that asks for them
    (it claims `cash_kinds` or holds the cash golden): a folder of one's
    own with neither is not held to them."""
    sample, golden = Path(fixture_dir) / "sample.csv", Path(fixture_dir) / "expected.json"
    if not sample.is_file():
        return [f"no fixture{_NL}{sample} is not a file"], 0, 0
    try:
        meta = read_fixture_meta(fixture_dir)
    except ValueError as e:
        return [f"the folder's fixture.toml cannot be read{_NL}{e}"], 0, 0
    if expected_skips is None:
        expected_skips = meta.get("expected_skips")
    try:
        parser, txs = parse_fixture(parser_cls, sample)
    except _REFUSALS as e:
        return [f"the parser refused the fixture{_NL}{e}"], 0, 0
    schema = schema_findings(txs)
    if update_golden and schema:
        schema.append(f"the golden was not rewritten{_NL}fix the schema "
                      f"findings first")
    accounting = accounting_findings(parser, expected_skips)
    pid = parser_id or str(meta.get("parser") or Path(fixture_dir).name)
    cash_golden = Path(fixture_dir) / CASH_GOLDEN
    asks_cash = _claims(meta, "cash_kinds") is not None or cash_golden.is_file()
    cash, refused = cash_events_of(pid, parser) if asks_cash else ([], [])
    kinds = refused + kind_findings(
        txs, getattr(parser, "expiry_rows", ()), _claims(meta, "kinds"),
        cash, _claims(meta, "cash_kinds"), parser_kinds(parser_cls))
    out = (schema
           + golden_findings(txs, golden, update=update_golden and not schema,
                             hint=hint)
           + ([] if refused or not asks_cash else cash_golden_findings(
               cash, cash_golden, update=update_golden, hint=hint))
           + determinism_findings(parser_cls, sample, first=txs)
           + accounting
           + kinds
           + (account_phase_findings(parser_cls) if account_phase else [])
           + (detection_findings(pid, sample, route=route) if route else [])
           + country_findings(parser_cls, sample, known=set(schema) | set(accounting)))
    counts = _row_counts(parser)
    return out, len(txs), counts.skipped if counts else 0
