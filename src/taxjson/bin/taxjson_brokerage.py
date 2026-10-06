#!/usr/bin/env python3
"""
taxjson_brokerage.py

Adapter for converting a brokerage's export (CSV, JSON, etc.) into a list of
TaxTransaction JSON objects.

Usage:
    taxjson-brokerage --brokerage <id> <input-file> [<input-file> ...] [--transfers]

`--brokerage <id>` selects which brokerage's import routine to load from
`lib/brokerages/` (e.g., interactive_brokers, rbc_direct, questrade, webull).

Multiple input files are accepted; their parsed transactions concatenate
in argument order — useful when a broker splits the year across two CSV
exports or when one account has separate trade vs dividend files.

The script prints a JSON array to STDOUT – each entry conforms to the
TaxTransaction schema defined in lib/core.py.

Options:
    --transfers     Include transfer transactions in output (default: exclude)
"""

from taxjson.lib.stage_msg import emit_line
import csv
import hashlib
import inspect
import json
import sys
import argparse
import re
from pathlib import Path

from taxjson.lib.cli_diag import guard_main, tax_year
from taxjson.lib.core import (register_brokerage, TaxTransaction,
                              load_brokerage, is_option_symbol)
from taxjson.lib.brokerages.base import (BaseBrokerage, BrokerageParseError,
                                         decode_broker_text,
                                         shown_name, source_identity,
                                         source_key)
from taxjson.lib.country import country_arg
from taxjson.lib.brokerages.schema import ATTENTION_TAG as SCHEMA_ATTENTION_TAG
from taxjson.lib.brokerages.schema import validate_transactions
from taxjson.lib.brokerages import ib_extractor
from taxjson.lib.brokerages import questrade
from taxjson.lib.brokerages import rbc_direct
from taxjson.lib.brokerages import webull
from taxjson.lib.brokerages import kraken
from taxjson.lib.brokerages import coinbase
from taxjson.lib.brokerages import generic

register_brokerage("interactive_brokers", ib_extractor.IbBrokerage)
register_brokerage("ib", ib_extractor.IbBrokerage)
register_brokerage("rbc_direct", rbc_direct.RbcBrokerage)
register_brokerage("rbc", rbc_direct.RbcBrokerage)
register_brokerage("questrade", questrade.QuestradeBrokerage)
register_brokerage("qt", questrade.QuestradeBrokerage)
register_brokerage("webull", webull.WebullBrokerage)
register_brokerage("wb", webull.WebullBrokerage)
register_brokerage("kraken", kraken.KrakenBrokerage)
register_brokerage("kr", kraken.KrakenBrokerage)
register_brokerage("coinbase", coinbase.CoinbaseBrokerage)
register_brokerage("cb", coinbase.CoinbaseBrokerage)
register_brokerage("generic", generic.GenericBrokerage)

# The id `taxjson run` passes for each parser (detect_broker); an alias
# typed on the command line is recorded under it, so the fees report
# does not split one broker into two rows (audit S026-21).
_CANONICAL_ID = {'interactive_brokers': 'ib', 'rbc': 'rbc_direct',
                 'qt': 'questrade', 'wb': 'webull', 'kr': 'kraken',
                 'cb': 'coinbase'}


# Parser fields that are evidence only (not TaxTransaction fields): kept
# on the raw rows (the --transfers-out sidecar carries them) and dropped
# quietly from the book rows. 'qty' is the legacy alias of 'quantity'.
# fee_qty / fee_currency: a crypto withdrawal's fee paid in coins
# (Kraken), shown by `taxjson transfers` (A2-0663). journal_pair: the
# id of a Questrade currency journal's two legs (lib/cross_listings).
_EVIDENCE_KEYS = frozenset({'qty', 'book_value', 'broker_account',
                            'fee_qty', 'fee_currency', 'journal_pair'})


def hash_broker_account(acct) -> str:
    """The form a broker account id takes in work/ files: sha256 of the
    id the export prints, first 10 hex (the id itself never leaves the
    parse). Dedup compares these per row and per file (R1-296, A2-0008)."""
    return hashlib.sha256(str(acct).strip().encode()).hexdigest()[:10]


def stamp_source_accounts(rows, file_accounts) -> None:
    """Replace each parser row's raw `broker_account` (when the parser
    read one) with its hash in `source_account`; a row without one gets
    the statement's account when the file names exactly one. Rows of a
    file naming several accounts and none per row stay unstamped (the
    per-file set in metadata.source_accounts still applies)."""
    single = (hash_broker_account(next(iter(file_accounts)))
              if len(file_accounts) == 1 else '')
    for t in rows:
        raw = t.pop('broker_account', None)
        t.pop('source_account', None)
        if raw is not None and str(raw).strip():
            t['source_account'] = hash_broker_account(raw)
        elif single:
            t['source_account'] = single


class SecurityOverrideError(ValueError):
    """A ticker.map EXTRACT line that cannot be read. A skipped rule
    changes ACB pooling (the USD ETF leg lands in another security's
    pool), so an unreadable line fails the parse like a malformed
    [[distributions]] entry does (audit S001-01)."""


def load_security_overrides(path: Path):
    """The symbol-extraction overrides: the EXTRACT lines of the
    ticker.map at `path`.

    Each is `EXTRACT description words | CURRENCY | SYMBOL`. These
    correct securities the currency->exchange-suffix logic mislabels: a
    parser stamps `.US` on any USD row, but a security can trade in USD
    on a non-US exchange — a TSX fund with a US-dollar class (CAD class
    ZZD.TO, USD class ZZD.U.TO) whose USD leg must not become a
    fictional `ZZD.US` (which would collide with an unrelated US-listed
    issuer). Description is the only field that reliably tells those
    two `ZZD`s apart.

    CURRENCY may be '*' to match any currency; a currency code is
    case-insensitive ('usd' == 'USD'). A malformed EXTRACT line (or a
    line with no ticker.map keyword — an old
    ticker_extraction_overrides.txt line pasted in) raises
    SecurityOverrideError naming the line. Returns a list of
    (desc_substring_lower, CURRENCY, symbol) tuples in file order.
    """
    from taxjson.lib.ticker_map import read_side_rules
    rules = read_side_rules(path)
    bad = [m for m in rules.problems
           if ": EXTRACT" in m or "no ticker.map keyword" in m]
    if bad:
        raise SecurityOverrideError(
            f"{shown_name(path)}: {bad[0]}"
            + (f" (and {len(bad) - 1} more)" if len(bad) > 1 else ""))
    return list(rules.extract)


_FUTURES_PREFIXES = ('F:', '/', '\\')


def _override_matches(desc_sub: str, desc: str) -> bool:
    """`desc_sub` occurs in `desc` as whole words (base.
    extract_words_match, the one rule)."""
    from taxjson.lib.brokerages.base import extract_words_match
    return extract_words_match(desc_sub, desc)


def apply_security_override(tx: dict, overrides, hits=None) -> None:
    """Rewrite `tx['symbol']` if the transaction matches an override —
    description contains the key as whole words (case-insensitive) and
    the currency matches (or the override currency is '*'). First match
    wins. Option and futures rows are never rewritten: an issuer name
    in an option description would turn the contract into a share
    (audit R1-143). A SPLIT's `symbol_new` that repeats the old symbol
    (IB's plain-split spelling) follows the rewrite, or the split would
    become a rename back to the un-overridden listing (S001-02).
    `hits`, when given, counts matches per override index. Mutates `tx`
    in place; a no-op when `overrides` is empty. Returns the index of
    the override that matched (None when none did)."""
    if not overrides:
        return None
    sym = tx.get('symbol') or ''
    # Every futures spelling (F:, '/', '\\') is exempt, not only F:
    # (audit A2-1092).
    if sym.startswith(_FUTURES_PREFIXES) or is_option_symbol(sym):
        return None
    desc = (tx.get('description') or '').lower()
    currency = (tx.get('currency') or '').upper()
    for i, (desc_sub, ovr_currency, symbol) in enumerate(overrides):
        if (_override_matches(desc_sub, desc)
                and (ovr_currency == '*' or ovr_currency == currency)):
            if tx.get('symbol_new') and tx['symbol_new'] == sym:
                tx['symbol_new'] = symbol
            tx['symbol'] = symbol
            if hits is not None:
                hits[i] = hits.get(i, 0) + 1
            return i
    return None


_CUT_NUMBER_RE = re.compile(r'[-+(]?[$]?[\d,]*\.?\d+\)?')


def final_record_cut(path: Path) -> str:
    """Is the export's last record possibly cut short (a truncated
    download, a partial copy)? Every parser reads a cut-off final cell as
    a smaller amount ('183.00' -> '18') at exit 0 (audit A2-0110).

    A file that ends with a line break is whole. One that does not and
    whose last line leaves a quoted cell open was cut inside it: refused
    (BrokerageParseError). One whose last line ends in an unquoted
    number may have lost digits: the returned text is an ATTENTION line
    (RBC exports end without a line break, on a quoted cell). '' when
    nothing looks cut."""
    try:
        text = decode_broker_text(Path(path).read_bytes(), shown_name(path))
    except (OSError, BrokerageParseError):
        return ''               # the parser reports an unreadable file
    if not text or text[-1] in '\r\n':
        return ''
    last = text.splitlines()[-1]
    n = len(text.splitlines())
    if last.count('"') % 2:
        raise BrokerageParseError(
            f"{shown_name(path)} line {n}: the file ends inside a quoted "
            f"cell — the export was cut short; refusing a partial last "
            f"row. Download it again.")
    cell = last.rsplit(',', 1)[-1].strip()
    if ',' in last and _CUT_NUMBER_RE.fullmatch(cell):
        return (f"the file does not end with a line break and its last "
                f"line (line {n}) ends in the number {cell!r} — if the "
                f"download was cut short, that amount lost digits. "
                f"Compare it with the broker's statement.")
    return ''


def _only_nonevents(extractor) -> int:
    """The number of rows the parser counted as recognized non-events
    when that is ALL it skipped (no unclassified skip, no lint finding);
    0 otherwise."""
    counts = getattr(extractor, '_skip_counts', None) or {}
    pfx = getattr(extractor, 'KNOWN_NONEVENT_PREFIX', None)
    if not counts or not pfx or getattr(extractor, 'lint_findings', None):
        return 0
    if any(not str(c).startswith(pfx) for c in counts):
        return 0
    return sum(counts.values())


def _refusal(e: Exception) -> str:
    """The one-line text of a parser's refusal (a legacy-encoded file
    included — a codec traceback named no file, audit S059-17)."""
    if isinstance(e, UnicodeDecodeError):
        return (f"not UTF-8 or UTF-16 text (byte 0x{e.object[e.start]:02x} "
                f"at offset {e.start}) — re-export the file, or save it as "
                f"CSV UTF-8")
    return str(e)


def _dedup_evidence(per_file) -> list:
    """TRANSFER evidence rows from every input file, de-duplicated the
    way the book is (taxjson-sort --dedup): by content id, keeping for
    each id the LARGEST count any single file has. Overlapping exports
    (a re-download, a full-year statement next to a monthly one) used
    to double every custody move, and the holdings evidence netting
    then moved twice the shares (audit S026-23 / S027-00)."""
    valid = set(inspect.signature(TaxTransaction).parameters.keys())
    best: dict = {}
    order: list = []
    for rows in per_file:
        mine: dict = {}
        for t in rows:
            try:
                key = TaxTransaction(**{k: v for k, v in t.items()
                                        if k in valid}).id
            except (TypeError, ValueError):
                key = json.dumps(t, sort_keys=True, default=str)
            # Identical custody moves of two DIFFERENT broker accounts
            # are two moves (audit A2-1093 / A2-1094): the account
            # (hashed, stamped before this) is part of the key.
            key = (key, t.get('source_account') or '')
            mine.setdefault(key, []).append(t)
        for key, ts in mine.items():
            if key not in best:
                order.append(key)
                best[key] = ts
            elif len(ts) > len(best[key]):
                best[key] = ts
    return [t for key in order for t in best[key]]


@guard_main("taxjson-brokerage")
def main():
    parser = argparse.ArgumentParser(
        description="Convert broker CSV exports (Interactive Brokers, "
                    "Questrade, RBC, Webull,\nKraken, Coinbase, or a "
                    "generic column mapping) to taxjson JSON.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    taxjson-brokerage --brokerage rbc_direct rbc_export.csv
    taxjson-brokerage --brokerage ib statement_2025.csv statement_2026.csv --transfers
    taxjson-brokerage --brokerage rbc rbc_margin.csv --account Margin
    taxjson-brokerage --brokerage qt questrade_tfsa.csv --account TFSA
        """
    )
    parser.add_argument(
        "--brokerage",
        dest="brokerage_id",
        required=True,
        help="Brokerage ID (e.g., rbc_direct, ib, qt, kraken, coinbase)",
    )
    parser.add_argument(
        "input_files",
        nargs="+",
        metavar="input_file",
        help=(
            "Input CSV file(s). Multiple files are parsed in order and "
            "their transactions concatenated — handy when a broker splits "
            "the year across separate exports."
        ),
    )
    parser.add_argument("--transfers", action="store_true",
                       help="Include transfer transactions in output")
    parser.add_argument("--transfers-out", metavar="PATH", default=None,
                       help="Without --transfers: write the excluded "
                            "TRANSFER rows to this sidecar JSON instead "
                            "of discarding them (custody evidence for "
                            "`taxjson transfers`)")
    parser.add_argument("--strict", action="store_true",
                       help="Exit nonzero when the parsed output violates "
                            "the transaction schema (default: report "
                            "violations as warnings and continue)")
    parser.add_argument("--lint", action="store_true",
                       help="Audit mode: print the per-category skipped-row "
                            "table and style-level schema findings; exit "
                            "nonzero on schema errors or unaccounted rows")
    parser.add_argument(
        "--security-overrides",
        metavar="FILE",
        default=None,
        help=(
            "A ticker.map whose EXTRACT lines (`EXTRACT description "
            "words | CURRENCY | SYMBOL`) rewrite the parsed ticker for "
            "securities the currency->exchange suffix mislabels (e.g. "
            "a TSX-listed fund's US-dollar class, which would otherwise "
            "collide with a US-listed ticker of the same name). "
            "`taxjson run` passes the project's ticker.map "
            "when it has EXTRACT lines."
        ),
    )
    parser.add_argument(
        "--account",
        dest="account_name",
        metavar="NAME",
        default=None,
        help=(
            "Account label for every transaction (e.g. 'Margin', 'RRSP', "
            "'TFSA'). Applied before tx-id hashing so downstream tools see "
            "the label consistently and the wash-sale report shows the "
            "account type instead of the parser default. When omitted, the "
            "parser's own DEFAULT_ACCOUNT (e.g. 'IB', 'Kraken') is kept — "
            "previously this defaulted to the literal 'default' which "
            "clobbered the meaningful label."
        ),
    )
    parser.add_argument(
        "--override-log", metavar="PATH", default=None,
        help=("With --security-overrides: write which parsed (symbol, "
              "currency) pairs an override renamed, and which it left "
              "alone, as JSON — `taxjson run` renames the corporate-"
              "action rows of the same security the same way."))
    parser.add_argument(
        "--country", type=country_arg, default=None,
        metavar="{canada,ca,usa,us}",
        help=(
            "Whose rules the country-specific parse choices follow: "
            "(1) IB --foreign-roc: canada -> 'dividend' (ITA s.90(1)), "
            "usa -> 'acb' (a nondividend distribution lowers basis, "
            "§301(c)(2)); (2) Kraken / Coinbase USD stablecoins: canada "
            "-> US-dollar cash (CA-CRYPTO-02), usa -> property like any "
            "coin (US-CRYPTO-02). It also picks the tax words of the "
            "parse notes (RBC: T3 / ACB vs Form 1099-DIV / basis). "
            "Without it both choices take the neutral answer — a "
            "foreign issuer's return of capital lowers the cost and a "
            "stablecoin is property — and a note says so. `taxjson run` "
            "passes the project's country."
        ),
    )
    parser.add_argument(
        "--foreign-roc", dest="foreign_roc", choices=("dividend", "acb"),
        default=None,
        help=(
            "IB only: how a '(Return of Capital)' distribution from a "
            "NON-Canadian issuer is booked — 'dividend' (ITA s.90(1): a "
            "non-resident corporation's distribution is a dividend unless "
            "it reduces paid-up capital; Canada only) or 'acb' (a cost reduction). Default: from "
            "--country (canada: dividend, usa: acb), else acb. "
            "Canadian-issuer ROC is always a cost reduction; a payment in "
            "lieu is always income."
        ),
    )
    parser.add_argument(
        "--futures-settle", dest="futures_settle",
        choices=("trade", "next_day"), default="trade",
        help=(
            "IB and the generic importer: settle date of futures and "
            "futures options — "
            "'trade' (default; variation margin settles the P/L daily, so "
            "the disposition is the trade date) or 'next_day' (the "
            "clearing house's premium settlement day)."
        ),
    )
    parser.add_argument(
        "--account-type", dest="account_type",
        choices=("taxable", "sheltered"), default=None,
        help=(
            "Whether the account is taxable. Parsers use it only to "
            "decide whether a taxable-account caveat is worth a warning "
            "(Questrade: a dividend booked net of non-resident "
            "withholding, a transfer-in with no book value). `taxjson "
            "run` passes it from the account's `type`."
        ),
    )
    parser.add_argument(
        "--exercise-fee", dest="exercise_fee", type=float, default=None,
        metavar="FEE",
        help=(
            "Webull: the broker's exercise/assignment charge on the stock "
            "leg (`[accounts.<name>] exercise_fee`, passed by `taxjson "
            "run`). With it, a $0 option close plus a stock trade at the "
            "strike carrying exactly this charge is booked as an "
            "exercise/assignment; without it nothing is inferred and each "
            "such pair is named for you to check."
        ),
    )
    parser.add_argument(
        "--year-end-posting", dest="year_end_posting", default=None,
        metavar="MM-DD",
        help=(
            "RBC: the day of the next year by which RBC has posted the "
            "tax year's back-dated Dec-31 book-cost adjustments "
            "(`[accounts.<name>] year_end_posting`, passed by `taxjson "
            "run`; default 06-30). Exports that hold the year's rows, all "
            "taken before it, get a note that they may be missing."
        ),
    )
    parser.add_argument(
        "--combined-broker-accounts", dest="combined_broker_accounts",
        action="store_true",
        help=(
            "Every broker account in these statements is yours and "
            "taxable together (`[accounts.<name>] combined_broker_accounts "
            "= true`, passed by `taxjson run`): a statement spanning "
            "several broker accounts is a one-line note, not an ATTENTION "
            "line. With --account-type sheltered it is refused unless the "
            "statement shows every account is the same plan."
        ),
    )
    parser.add_argument(
        "--tax-year", dest="tax_year", type=tax_year, default=None,
        metavar="YYYY",
        help=(
            "The tax year the books are for. Parsers whose exports carry "
            "their own timestamp check it against the year (RBC: an "
            "export taken before the year ended cannot hold the rest of "
            "it). `taxjson run` passes the project year."
        ),
    )
    parser.add_argument(
        "--ticker-map", dest="ticker_map", metavar="FILE", default=None,
        help=(
            "The project's ticker.map (`taxjson run` passes it). The "
            "parsers' identity hints (a ticker change booked without a "
            "corporate-action row, an income row on an untraded listing) "
            "are dropped for a pair the map already joins; --lint keeps "
            "them. The map changes no row here — renames apply later."
        ),
    )
    parser.add_argument(
        "--symbol-codes", dest="symbol_codes", metavar="FILE",
        default=None,
        help=(
            "Questrade: the internal security codes `taxjson run` "
            "resolved from the project's other exports (work/<acct>"
            "_symbol_codes.state, lib/symbol_codes): every row of a "
            "resolved code is booked under its ticker, said in one note. "
            "A ticker.map rule for the code wins."
        ),
    )
    parser.add_argument(
        "--rates", dest="rates", metavar="FILE", default=None,
        help=(
            "The run's currency rates (work/to_base.csv, `taxjson run` "
            "passes it). Coinbase and Kraken turn a stablecoin fill valued "
            "in CAD, EUR, ... into US dollars through it before the de-peg "
            "check (a warning only; nothing is booked from it)."
        ),
    )
    args = parser.parse_args()

    brokerage_id = args.brokerage_id.lower()
    brokerage_id = _CANONICAL_ID.get(brokerage_id, brokerage_id)
    input_paths = [Path(p) for p in args.input_files]
    missing = [p for p in input_paths if not p.exists()]
    if missing:
        # Environment error (exit 2), unlike schema/lint FINDINGS (exit 1).
        for p in missing:
            emit_line(f"taxjson-brokerage: error: no such file: {p}")
        sys.exit(2)

    # The market lists' per-symbol overrides (lib/markets: STABLE,
    # VENUE, EVENING, MULT, GLOBAL crypto codes) come from this map;
    # without one, from TAXJSON_TICKER_MAP (`taxjson run` sets it).
    from taxjson.lib.markets import use_ticker_map
    use_ticker_map(args.ticker_map)
    if args.ticker_map:
        if not Path(args.ticker_map).exists():
            emit_line(f"taxjson-brokerage: error: no such file: --ticker-map "
                  f"{args.ticker_map}")
            sys.exit(2)
        if not args.lint:
            # Hints the map already answers stay quiet (A2-1056).
            from taxjson.lib.brokerages.base import set_ticker_map
            try:
                set_ticker_map(Path(args.ticker_map))
            except (OSError, ValueError) as e:
                emit_line(f"taxjson-brokerage: error: {args.ticker_map}: {e}")
                sys.exit(2)
    if args.symbol_codes and not Path(args.symbol_codes).exists():
        emit_line(f"taxjson-brokerage: error: no such file: --symbol-codes "
                  f"{args.symbol_codes}")
        sys.exit(2)
    if args.rates:
        if not Path(args.rates).exists():
            emit_line(f"taxjson-brokerage: error: no such file: --rates "
                  f"{args.rates}")
            sys.exit(2)
        from taxjson.lib.brokerages._crypto_common import set_depeg_rates
        try:
            set_depeg_rates(Path(args.rates), None)
        except (OSError, ValueError) as e:
            emit_line(f"taxjson-brokerage: error: {args.rates}: {e}")
            sys.exit(2)
    try:
        extractor_class = load_brokerage(brokerage_id)
    except ValueError as e:
        # A usage error (exit 2), one line — it was a traceback (R1-262).
        emit_line(f"taxjson-brokerage: error: {e} (known: "
              f"{', '.join(sorted(set(_CANONICAL_ID.values()) | {'generic'}))}"
              f")")
        sys.exit(2)
    valid_keys = set(inspect.signature(TaxTransaction).parameters.keys())
    try:
        overrides = (load_security_overrides(Path(args.security_overrides))
                     if args.security_overrides else [])
    except (OSError, SecurityOverrideError) as e:
        # An unreadable file is an environment error: exit 2, as its
        # sibling tools exit (re-audit A2-1421); a malformed line stays
        # a data error (1).
        emit_line(f"taxjson-brokerage: error: {e}")
        sys.exit(2 if isinstance(e, OSError) else 1)
    override_hits: dict = {}
    # (symbol|CURRENCY) the overrides renamed (-> new symbols) or left
    # alone — the corp-action stage's rows of the same security follow
    # the rename through --override-log (S004-00).
    override_renamed: dict = {}
    override_kept: set = set()
    # override index -> the distinct raw symbols it rewrote (A2-0109).
    override_raw: dict = {}
    normalized = []
    # Parser-declared contract multipliers, parallel to `normalized`:
    # they feed the schema notional check below (an ERROR for rows that
    # declare one). Option and futures rows also keep theirs on the
    # TaxTransaction (`multiplier`); a share's 1 is checked here only.
    multipliers = []
    dropped_keys = {}
    lint_problems = 0
    kept_aside_per_file: list = []

    # Account-wide context: a parser that learns identities (a symbol's
    # listing, an option code's contract, a temporary code's company)
    # or de-duplicates overlapping downloads needs ALL of the account's
    # files at once — per-file state made the answer depend on how the
    # rows were split across yearly exports (RBC, 2026-09 audit).
    shared_context = None
    _prepare = getattr(extractor_class, 'prepare_files', None)
    if _prepare is not None:
        try:
            # The tax year, for a parser whose account-level coverage
            # check needs it (IB statement periods, audit A2-0262).
            _pp = inspect.signature(_prepare).parameters
            _pkw: dict = {}
            if 'tax_year' in _pp:
                _pkw['tax_year'] = args.tax_year
            # The label's combined_broker_accounts and type, for an
            # account-level 'statements span N accounts' check.
            if 'combined' in _pp:
                _pkw['combined'] = args.combined_broker_accounts
            if 'taxable' in _pp and args.account_type:
                _pkw['taxable'] = args.account_type == 'taxable'
            if 'symbol_codes' in _pp and args.symbol_codes:
                _pkw['symbol_codes'] = args.symbol_codes
            shared_context = _prepare(input_paths, **_pkw)
        except csv.Error as e:
            emit_line(f"taxjson-brokerage: error: the CSV module refused an "
                  f"input file ({e}) — see the per-file error below by "
                  f"parsing the files one at a time.")
            sys.exit(2)
        except (BrokerageParseError, ValueError, UnicodeDecodeError) as e:
            emit_line(f"taxjson-brokerage: error: {_refusal(e)}")
            sys.exit(1)
        # Per-statement coverage against the tax year (RBC "as of"
        # timestamps, audit S063-22).
        _cov = getattr(extractor_class, 'coverage_messages', None)
        if _cov is not None and args.tax_year and shared_context is not None:
            _ckw = {}
            if args.year_end_posting is not None:
                _ckw['year_end_posting'] = args.year_end_posting
            try:
                _cov_msgs = _cov(shared_context, args.tax_year,
                                 country=args.country, **_ckw)
            except ValueError as e:
                emit_line(f"taxjson-brokerage: error: --year-end-posting: {e}")
                sys.exit(2)
            for _m in _cov_msgs:
                emit_line(_m)

    # s.90(1) is Canadian law: never the default without a country
    # (partition INPUTS-03), and refused for a US filer.
    foreign_roc = args.foreign_roc
    if args.country is not None:
        # The ownership table (lib/country FLAG_VALUE_COUNTRY: the
        # "dividend" value is ITA s.90(1), Canada-only; A2-0719).
        from taxjson.lib.country import flag_country_problems
        _fp = flag_country_problems(args.country,
                                    {"--foreign-roc": foreign_roc},
                                    tool="taxjson-brokerage: error")
        if _fp:
            for _m in _fp:
                emit_line(_m)
            sys.exit(2)
    if foreign_roc is None:
        foreign_roc = "dividend" if args.country == "canada" else "acb"

    parsed_files = []
    for input_path in input_paths:
        # Fresh extractor per file so any extractor-level state (e.g.
        # IB's `unhandled_ca_tickers` warning bucket) doesn't bleed
        # across files and emit confused diagnostics.
        extractor = extractor_class()
        if shared_context is not None:
            extractor.account_context = shared_context
            if hasattr(extractor, 'defer_ca_messages') and getattr(
                    extractor_class, 'reconcile_files', None) is not None:
                # Printed after the cross-statement pass below, which
                # may undo an event of this file (A2-1091).
                extractor.defer_ca_messages = True
        # Which law the parser's messages cite (never a tax choice).
        extractor.country = args.country
        # The EXTRACT lines: a parser's listing hint the map answers is
        # not printed.
        extractor.security_overrides = list(overrides or [])
        if hasattr(extractor, 'foreign_return_of_capital'):
            extractor.foreign_return_of_capital = foreign_roc
        if hasattr(extractor, 'futures_settle'):
            extractor.futures_settle = args.futures_settle
        if hasattr(extractor, 'country'):
            # The notes' tax words (RBC: T3 / ACB vs Form 1099-DIV /
            # basis); None = neutral words. Booking is unchanged.
            extractor.country = args.country
        if hasattr(extractor, 'stablecoins_as_cash'):
            # USD stablecoins are US-dollar cash (Canada's stated
            # approximation, CA-CRYPTO-02) or property like any coin
            # (a US project: US-CRYPTO-02; partition COMMANDS-13).
            # Without a country: property, the neutral answer, as
            # --foreign-roc defaults to the neutral cost reduction —
            # it used to be Canada's cash model (re-audit A2-0742,
            # A2-1238).
            extractor.stablecoins_as_cash = args.country == "canada"
        if args.account_type and hasattr(extractor, 'account_taxable'):
            extractor.account_taxable = args.account_type == 'taxable'
        extractor.combined_broker_accounts = args.combined_broker_accounts
        # The sidecar's reader (`taxjson run`) says what a transfer-in
        # costs, once it knows which ones a .tt line covers.
        extractor.transfer_costs_checked_downstream = bool(
            args.transfers_out and not args.transfers)
        if args.exercise_fee is not None and hasattr(extractor,
                                                     'exercise_fee'):
            extractor.exercise_fee = args.exercise_fee
        try:
            _cut = final_record_cut(input_path)
            transactions = extractor.parse_file(input_path)
            if _cut:
                emit_line(f"warning: ATTENTION: {shown_name(input_path)}: "
                      f"{_cut}")
        except csv.Error as e:
            # A >128KB field (or other csv-module limit) surfaced as a
            # raw traceback; name the file and the limit instead
            # (2026-09 security audit).
            emit_line(f"taxjson-brokerage: error: {shown_name(input_path)}: the "
                  f"CSV module refused the file ({e}). A single field "
                  f"exceeding {csv.field_size_limit()} characters is "
                  f"the usual cause — inspect/trim the offending row.")
            sys.exit(2)
        except (BrokerageParseError, ValueError, UnicodeDecodeError) as e:
            # The parser refused the file rather than guess (missing
            # required column, unparseable money, a row whose money does
            # not add up, a Cash Report mismatch, a non-activity report).
            # A finding in the DATA: exit 1, one line, no traceback —
            # also for the parsers whose refusals are plain ValueErrors
            # (RBC's RbcFormatError, Kraken, Coinbase) and for a file in
            # a legacy encoding (audit R1-262 / S059-17).
            msg = _refusal(e)
            shown = shown_name(input_path)
            emit_line(f"taxjson-brokerage: error: "
                  f"{msg if msg.startswith(shown) else f'{shown}: {msg}'}")
            sys.exit(1)
        parsed_files.append((input_path, extractor, transactions))
        if args.country is None and hasattr(extractor,
                                            'stablecoins_as_cash'):
            from taxjson.lib.brokerages._crypto_common import \
                USD_STABLECOINS
            _stab = sorted({str(t.get('symbol') or '').upper()
                            for t in transactions}
                           & set(USD_STABLECOINS))
            if _stab:
                emit_line(f"taxjson-brokerage: note: {shown_name(input_path)}: "
                      f"{', '.join(_stab)} booked as property like any "
                      f"coin (no --country given); a Canadian filer "
                      f"passes --country canada (USD stablecoins are "
                      f"US-dollar cash).")
        if args.country is None and args.foreign_roc is None:
            # The issuer's ISIN is in IB's description ("QZRX(US...)").
            _froc = [t for t in transactions
                     if t.get('type') == 'roc'
                     and (m := re.search(r'\(([A-Z]{2})[A-Z0-9]{9}\d\)',
                                         str(t.get('description') or '')))
                     and m.group(1) != 'CA']
            if _froc:
                emit_line(f"taxjson-brokerage: note: {shown_name(input_path)}: "
                      f"{len(_froc)} return(s) of capital from a "
                      f"non-Canadian issuer booked as a cost reduction "
                      f"(no --country given); a Canadian filer passes "
                      f"--country canada (ITA s.90(1): a dividend).")

    # Cross-statement pass (IB): a `Ca` cancellation, commission refund
    # or cash in lieu whose row sits in ANOTHER of the account's
    # statements is applied there, and an overlapping statement that
    # holds the unadjusted original gets the same adjustment, so dedup
    # keeps one row (audit S059-04, A2-0023 / A2-0024 / A2-0088).
    _reconcile = getattr(extractor_class, 'reconcile_files', None)
    if _reconcile is not None and shared_context is not None:
        _reconcile(parsed_files)

    # An option trade on its expiry day is clamped to the expiry by each
    # parser — but only against the expiry rows of ITS file. The expiry
    # of a Dec-31 0DTE contract posts the next business day, i.e. in the
    # NEXT yearly export, so the clamp runs again over all of the
    # account's files (audit S055-22).
    _all_expiries = [e for _, _ex, _ in parsed_files
                     for e in getattr(_ex, 'expiry_rows', None) or ()]
    if len(parsed_files) > 1 and _all_expiries:
        BaseBrokerage.clamp_settlement_across(
            [t for _, _, _txs in parsed_files for t in _txs], _all_expiries)

    # The generic importer's mappings may name the real broker
    # ([broker].name): the source is then recorded as generic:<name>,
    # so the fees report attributes it (audit S027-05). One parse holds
    # one broker — `taxjson run` splits the generic files by name.
    source_label = brokerage_id
    if brokerage_id == 'generic':
        _names = {getattr(ex, 'broker_name', None)
                  for _p, ex, _t in parsed_files}
        if len(_names) > 1:
            _shown = ", ".join(sorted(n or "(none)" for n in _names))
            emit_line(f"taxjson-brokerage: error: the generic files' mappings "
                  f"name different brokers ([broker].name: {_shown}) — "
                  f"parse each broker's files in a separate call "
                  f"(`taxjson run` does this).")
            sys.exit(2)
        _nm = next(iter(_names), None) if _names else None
        if _nm:
            source_label = f"generic:{_nm}"

    # Provenance for cross-file dedup (bin/taxjson_sort.plan_dedup,
    # audit R1-296): each row's input file (its masked shown name, made
    # unique within this parse) and, per file, the broker accounts the
    # export names — hashed, account ids never reach work/ files.
    _source_names: dict = {}
    _source_keys: dict = {}
    source_accounts: dict = {}
    for input_path, extractor, _txs in parsed_files:
        _nm = shown_name(input_path)
        _base, _k = _nm, 1
        while _nm in _source_names.values():
            _k += 1
            _nm = f"{_base}#{_k}"
        _source_names[id(extractor)] = _nm
        # A masked name carries a key (hash of the real name), so files
        # parsed in separate calls whose names differ only in an
        # account-number token stay separate sources (audit A2-0159).
        _source_keys[id(extractor)] = source_key(input_path)
        _accts = extractor.statement_accounts() \
            if hasattr(extractor, 'statement_accounts') else set()
        # The accounts the parser read per row count too (a Questrade
        # export of two accounts, an RBC Account column) — every parser
        # that sees the broker account reports it (audit A2-0008).
        _accts = {str(a).strip() for a in _accts if str(a).strip()} | {
            str(t['broker_account']).strip() for t in _txs
            if str(t.get('broker_account') or '').strip()}
        if _accts:
            source_accounts[source_identity(
                _nm, _source_keys[id(extractor)])] = sorted(
                hash_broker_account(a) for a in _accts)
        stamp_source_accounts(_txs, _accts)

    _bare_warned: set = set()
    for input_path, extractor, transactions in parsed_files:
        _kept_this_file = 0     # TRANSFER evidence rows set aside below
        _source = _source_names[id(extractor)]
        _source_key = _source_keys[id(extractor)]

        # Correct mislabeled tickers FIRST — before the TRANSFER rows
        # are set aside (the sidecar used to keep the un-overridden
        # symbol, so an evidenced depot flip never matched: S027-01)
        # and before the id hash is computed, so dedup and every
        # downstream tool see the right symbol.
        for t in transactions:
            _before = t.get('symbol') or ''
            _hit = apply_security_override(t, overrides, override_hits)
            if _hit is not None and _before:
                override_raw.setdefault(_hit, set()).add(
                    f"{_before} ({(t.get('currency') or '').upper()})")
            if overrides and _before and not is_option_symbol(_before) \
                    and not _before.startswith(_FUTURES_PREFIXES):
                _key = f"{_before}|{(t.get('currency') or '').upper()}"
                if t.get('symbol') != _before:
                    override_renamed.setdefault(_key, set()).add(
                        t.get('symbol'))
                    # A listed symbol renamed to a bare one (A2-0304).
                    from taxjson.bin.taxjson_ticker_map import \
                        bare_rename_target
                    if (bare_rename_target(_before, t.get('symbol'))
                            and (_before, t.get('symbol'))
                            not in _bare_warned):
                        _bare_warned.add((_before, t.get('symbol')))
                        emit_line(f"warning: ATTENTION: "
                              f"a ticker.map EXTRACT line renames "
                              f"{_before} to {t.get('symbol')}, which has "
                              f"no market suffix — a bare symbol is read "
                              f"as crypto / an unknown listing (a Canadian "
                              f"dividend on it is counted as foreign). "
                              f"Write the listing "
                              f"({t.get('symbol')}.TO, "
                              f"{t.get('symbol')}.US).")
                else:
                    override_kept.add(_key)

        if not args.transfers:
            # Custody evidence, not tax events: a taxable book's basis
            # comes from the actual buy/sell history, so TRANSFER rows
            # stay OUT of the book — but they are kept aside (see
            # --transfers-out) instead of silently deleted: a depot
            # flip or broker migration is exactly what explains a
            # confusing position later (`taxjson transfers` reads the
            # sidecar).
            _tr = [tx for tx in transactions
                   if tx.get('action', '').upper() == 'TRANSFER']
            _kept_this_file = len(_tr)
            if _tr:
                kept_aside_per_file.append(_tr)
                print(f"  {shown_name(input_path)}: {len(_tr)} TRANSFER "
                      f"row(s) kept aside (custody evidence, not tax "
                      f"events — view with `taxjson transfers`)",
                      file=sys.stderr)
                if brokerage_id in ('kraken', 'kr', 'coinbase', 'cb'):
                    # A Kraken Hybrid Earn move is still yours
                    # (crypto-sends decides it `self` automatically):
                    # not a possible disposition (audit A2-1078).
                    _sends = sum(1 for t in _tr
                                 if float(t.get('quantity') or 0) < 0
                                 and not str(t.get('description') or '')
                                 .lower().startswith('hybridearn'))
                    if _sends and args.country == "usa":
                        # A US donor's gift is not a sale (US-SEND-02;
                        # audit A2-0721, A2-0740, A2-1286).
                        emit_line(f"  NOTE: {_sends} crypto withdrawal/"
                            f"send(s) among them — if any paid for "
                            f"something (payment), each is a taxable "
                            f"SALE at fair market value: `taxjson "
                            f"crypto-sends` lists them with the fair "
                            f"value and writes the .tt sale for each "
                            f"payment (a gift is not a sale for a US "
                            f"donor; it and self-custody moves need "
                            f"nothing).")
                    elif _sends:
                        emit_line(f"  NOTE: {_sends} crypto withdrawal/"
                            f"send(s) among them — if any left your "
                            f"ownership (gift or payment), each is a "
                            f"taxable DISPOSITION at fair market "
                            f"value: `taxjson crypto-sends` lists "
                            f"them with the fair value and writes the "
                            f".tt sale for each gift/payment "
                            f"(self-custody moves need nothing)"
                            + ("" if args.country else
                               "; with --country usa only a payment "
                               "is a sale")
                            + ".")
            transactions = [tx for tx in transactions if tx.get('action', '').upper() != 'TRANSFER']

        # Per-file count so the user can see at a glance how many
        # transactions each input contributed. A 0-tx count from a
        # non-empty file is upgraded to a stderr WARNING so a
        # silently-broken parser (e.g. a header-detection regression)
        # can't slip past unnoticed.
        try:
            file_size = input_path.stat().st_size
        except OSError:
            file_size = 0
        if not transactions and file_size > 0 and _kept_this_file:
            # Every parsed row was custody evidence (a deposit-only
            # Kraken ledger, say): the parser worked — it's not the
            # regression the warning below is for.
            print(f"  {shown_name(input_path)}: 0 tax objects "
                  f"({_kept_this_file} TRANSFER row(s) kept aside)",
                  file=sys.stderr)
        elif (not transactions and file_size > 0
              and getattr(extractor, 'zero_tx_reason', None)):
            # The parser knows why this file books nothing (a Kraken
            # ledger whose trade rows are all booked from the trades
            # export) — not the regression the warning below is for,
            # and a warning on every correct run trains users to
            # ignore real ones.
            print(f"  {shown_name(input_path)}: 0 tax objects "
                  f"({extractor.zero_tx_reason})", file=sys.stderr)
        elif (not transactions and file_size > 0
              and _only_nonevents(extractor)):
            # Every row was recognized and counted as a non-event (a
            # deposit-only RBC file, a Questrade FX conversion, a Kraken
            # Earn allocation, a stablecoin buy): the parser read it all
            # — not the regression the warning below is for (audit
            # A2-0301, A2-0303).
            print(f"  {shown_name(input_path)}: 0 tax objects "
                  f"({_only_nonevents(extractor)} recognized non-event "
                  f"row(s))", file=sys.stderr)
        elif not transactions and file_size > 0:
            emit_line(f"warning: {shown_name(input_path)} parsed to 0 transactions "
                f"({file_size} bytes input, brokerage={brokerage_id}): "
                f"NONE of its rows are in the books. Check the CSV "
                f"header / format — silent zero-tx output is usually a "
                f"changed export layout or a parser regression.")
        else:
            print(f"  {shown_name(input_path)}: {len(transactions)} tax objects",
                  file=sys.stderr)

        if args.lint and getattr(extractor, '_rows_seen', None) is not None:
            seen = extractor._rows_seen
            consumed = extractor._rows_consumed
            skipped = sum(extractor._skip_counts.values())
            unaccounted = seen - consumed - skipped
            emit_line(f"lint: {shown_name(input_path)}: rows={seen} consumed={consumed} "
                  f"skipped={skipped} unaccounted={unaccounted}")
            if unaccounted:
                # A row neither classified nor counted: the parser has a
                # code path that drops data with no accounting at all.
                lint_problems += 1
        # Parser-reported findings (e.g. RBC: an unclassified row that
        # moves shares or cash, an unmatched reorganization leg): already
        # warned about on stderr; under --lint each one is a failure.
        _findings = getattr(extractor, 'lint_findings', None) or []
        if args.lint and _findings:
            for _f in _findings:
                emit_line(f"lint: {shown_name(input_path)}: {_f}")
            lint_problems += len(_findings)

        for t in transactions:
            if 'qty' in t and 'quantity' not in t:
                t = {**t, 'quantity': t['qty']}
            # Unknown keys are dropped — but no longer silently: a typo'd
            # or newly-invented parser field would otherwise vanish here
            # with zero signal ('qty' is exempt: aliased above).
            for k in t:
                if k not in valid_keys and k not in _EVIDENCE_KEYS \
                        and k not in ('qty', 'multiplier'):
                    dropped_keys[k] = dropped_keys.get(k, 0) + 1
            clean = {k: v for k, v in t.items() if k in valid_keys}
            # The declared contract size is kept on option and futures
            # rows (the holdings export, the .tt check and the what-if
            # read it — audit S026-22); a share's 1 is not news.
            _sym = str(clean.get('symbol') or '')
            if not (is_option_symbol(_sym)
                    or _sym.startswith(('F:', '/', '\\'))):
                clean.pop('multiplier', None)
            elif clean.get('multiplier') is not None:
                try:
                    clean['multiplier'] = float(clean['multiplier'])
                except (TypeError, ValueError):
                    clean.pop('multiplier', None)
            # Only override the parser's account label when --account
            # was explicitly given. Defaulting to the literal "default"
            # (the old behaviour) silently erased the per-parser
            # DEFAULT_ACCOUNT ("IB", "Kraken", etc.) for users who
            # forgot the flag.
            if args.account_name is not None:
                clean['account'] = args.account_name
            clean['source'] = _source
            if _source_key:
                clean['source_key'] = _source_key
            normalized.append(TaxTransaction(**clean))
            multipliers.append(t.get('multiplier'))

    # One override line that rewrote two DIFFERENT raw securities pools
    # them (IB 'QZL' vs 'QZL B', 'QZN' vs 'QZN PRA': the key matches
    # whole words, and a class letter or series is a word of its own —
    # audit A2-0109): said loudly, with the line to narrow.
    for _i, _syms in sorted(override_raw.items()):
        if len(_syms) > 1:
            _d, _c, _s = overrides[_i]
            emit_line(f"warning: ATTENTION: security override "
                  f"'{_d} | {_c} | {_s}' rewrote {len(_syms)} different "
                  f"raw symbols into {_s}: {', '.join(sorted(_syms))} — "
                  f"they now share ONE cost pool. If they are different "
                  f"securities (a share class, a preferred series, a "
                  f"warrant), make the description key longer so it "
                  f"matches only one.")

    for key, n in sorted(dropped_keys.items()):
        emit_line(f"warning: parser emitted unknown field {key!r} on {n} "
              f"transaction(s) — not part of the TaxTransaction schema, "
              f"DROPPED. Fix the parser or add the field to the schema.")

    # Schema validation on what downstream actually sees. Errors are
    # violations that corrupt tax math; they abort only under --strict
    # (or --lint) so a mid-season odd export still produces output.
    errors, schema_warnings = validate_transactions(
        [({**t.to_dict(), 'multiplier': m} if m else t.to_dict())
         for t, m in zip(normalized, multipliers)], lint=args.lint)
    for w in schema_warnings:
        if w.startswith(SCHEMA_ATTENTION_TAG):
            # `taxjson run` echoes ATTENTION lines to the console
            # (echo_parse_stats); the rest stay in the .sum (S065-12).
            emit_line(f"warning: ATTENTION: schema: "
                  f"{w[len(SCHEMA_ATTENTION_TAG):]}")
        else:
            emit_line(f"warning: schema: {w}")
    for e in errors:
        emit_line(f"{'error' if (args.strict or args.lint) else 'warning: schema VIOLATION'}: {e}")
    # Exit-code convention (AUDIT-2026-07-ui §1C4): schema violations and
    # lint problems are FINDINGS in the data → exit 1. Exit 2 is reserved
    # for usage/environment errors (bad args, missing files).
    if errors and (args.strict or args.lint):
        sys.exit(1)
    if args.lint and lint_problems:
        sys.exit(1)

    output_data = {
        "transactions": [t.to_dict() for t in normalized],
        "metadata": {
            "format_version": "1.0",
            "source_brokerage": source_label,
            "input_files": [str(p) for p in input_paths],
            **({"source_accounts": source_accounts}
               if source_accounts else {}),
        }
    }
    if args.transfers_out and not args.transfers:
        # Sidecar always written (even empty) so a re-parse that no
        # longer finds transfers replaces a stale sidecar rather than
        # leaving last run's rows behind. Atomic: .part + rename.
        _sp = Path(args.transfers_out)
        # Same account label the book rows get — the sidecar rows were
        # split off BEFORE normalization, so they still carry the
        # parser default ('IB').
        if args.account_name:
            for _rows in kept_aside_per_file:
                for _t in _rows:
                    _t['account'] = args.account_name
        kept_aside = _dedup_evidence(kept_aside_per_file)
        from taxjson.lib.cli_diag import write_text_atomic
        write_text_atomic(_sp, json.dumps(
            {"transactions": kept_aside,
             "metadata": {"kind": "transfer_sidecar",
                          "account": args.account_name,
                          "brokerage": brokerage_id}},
            indent=2, sort_keys=True))
    if args.override_log:
        _lp = Path(args.override_log)
        from taxjson.lib.cli_diag import write_text_atomic
        write_text_atomic(_lp, json.dumps(
            {"renamed": {k: sorted(v)
                         for k, v in sorted(override_renamed.items())},
             "kept": sorted(override_kept)}, indent=2))
    json.dump(output_data, sys.stdout, indent=2, sort_keys=True)
    print()


if __name__ == "__main__":
    main()
