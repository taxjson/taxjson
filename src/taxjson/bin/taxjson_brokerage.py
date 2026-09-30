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

import csv
import inspect
import json
import sys
import argparse
import re
from pathlib import Path

from taxjson.lib.core import (register_brokerage, TaxTransaction,
                              load_brokerage, is_option_symbol)
from taxjson.lib.brokerages.base import BaseBrokerage, BrokerageParseError
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


# Parser fields that are evidence only (not TaxTransaction fields): kept
# on the raw rows (the --transfers-out sidecar carries them) and dropped
# quietly from the book rows. 'qty' is the legacy alias of 'quantity'.
_EVIDENCE_KEYS = frozenset({'qty', 'book_value'})


class SecurityOverrideError(ValueError):
    """A ticker_extraction_overrides.txt line that cannot be read. A
    skipped rule changes ACB pooling (the USD ETF leg lands in another
    security's pool), so an unreadable line fails the parse like a
    malformed distributions.map line does (audit S001-01)."""


_OVR_CURRENCY_RE = re.compile(r'^(?:[A-Z]{3}|\*)$')


def load_security_overrides(path: Path):
    """Parse a security-overrides file.

    Each non-comment line is `description-substring | currency | symbol`,
    '|'-separated. These correct securities the currency->exchange-suffix
    logic mislabels: a parser stamps `.US` on any USD row, but a security
    can trade in USD on a non-US exchange — the Global X US Dollar ETF
    trades only on the TSX (CAD class DLR.TO, USD class DLR.U.TO), so its
    USD leg must not become a fictional `DLR.US` (which would collide
    with US-listed Digital Realty Trust). Description is the only field
    that reliably tells those two `DLR`s apart.

    The currency field may be '*' to match any currency; a currency code
    is case-insensitive ('usd' == 'USD'). A leading BOM is ignored. A
    malformed line raises SecurityOverrideError naming the line.
    Returns a list of (desc_substring_lower, CURRENCY, symbol) tuples.
    """
    overrides = []
    text = path.read_text(encoding='utf-8-sig')
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip().lstrip('\ufeff')
        if not line or line.startswith('#'):
            continue
        parts = [p.strip() for p in line.split('|')]
        if len(parts) != 3 or not parts[0] or not parts[2]:
            raise SecurityOverrideError(
                f"{path.name} line {n}: malformed security-override line "
                f"{raw.strip()!r} — expected `description | currency | "
                f"symbol` (currency may be '*')")
        desc_sub, currency, symbol = parts
        currency = currency.upper()
        if not _OVR_CURRENCY_RE.match(currency):
            raise SecurityOverrideError(
                f"{path.name} line {n}: currency {parts[1]!r} is not a "
                f"3-letter code or '*'")
        overrides.append((desc_sub.lower(), currency, symbol))
    return overrides


def _override_matches(desc_sub: str, desc: str) -> bool:
    """`desc_sub` occurs in `desc` as whole words: IB's description is
    the bare ticker, so a plain substring test made 'BN' rewrite ABNB
    and BNTX (audit S012-09)."""
    return re.search(r'(?<![a-z0-9])' + re.escape(desc_sub)
                     + r'(?![a-z0-9])', desc) is not None


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
    in place; a no-op when `overrides` is empty."""
    if not overrides:
        return
    sym = tx.get('symbol') or ''
    if sym.startswith('F:') or is_option_symbol(sym):
        return
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
            return


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
            mine.setdefault(key, []).append(t)
        for key, ts in mine.items():
            if key not in best:
                order.append(key)
                best[key] = ts
            elif len(ts) > len(best[key]):
                best[key] = ts
    return [t for key in order for t in best[key]]


def main():
    parser = argparse.ArgumentParser(
        description="Convert brokerage CSV to taxjson format",
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
            "Path to a security-overrides file. Each line is "
            "`description-substring | currency | symbol` and rewrites "
            "the parsed ticker for securities the currency->exchange "
            "suffix mislabels (e.g. the TSX-listed Global X US Dollar "
            "ETF, whose USD class would otherwise collide with a "
            "US-listed ticker of the same name)."
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
        "--foreign-roc", dest="foreign_roc", choices=("dividend", "acb"),
        default="dividend",
        help=(
            "IB only: how a '(Return of Capital)' distribution from a "
            "NON-Canadian issuer is booked — 'dividend' (default; ITA "
            "s.90(2) deems a non-resident corporation's distribution a "
            "dividend) or 'acb' (ACB reduction). Canadian-issuer ROC is "
            "always an ACB reduction; a payment in lieu is always income."
        ),
    )
    parser.add_argument(
        "--futures-settle", dest="futures_settle",
        choices=("trade", "next_day"), default="trade",
        help=(
            "IB only: settle date of futures and futures options — "
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
    args = parser.parse_args()

    brokerage_id = args.brokerage_id.lower()
    input_paths = [Path(p) for p in args.input_files]
    missing = [p for p in input_paths if not p.exists()]
    if missing:
        # Environment error (exit 2), unlike schema/lint FINDINGS (exit 1).
        for p in missing:
            print(f"taxjson-brokerage: error: no such file: {p}",
                  file=sys.stderr)
        sys.exit(2)

    extractor_class = load_brokerage(brokerage_id)
    valid_keys = set(inspect.signature(TaxTransaction).parameters.keys())
    try:
        overrides = (load_security_overrides(Path(args.security_overrides))
                     if args.security_overrides else [])
    except (OSError, SecurityOverrideError) as e:
        print(f"taxjson-brokerage: error: {e}", file=sys.stderr)
        sys.exit(1)
    override_hits: dict = {}
    normalized = []
    # Parser-declared contract multipliers, parallel to `normalized`
    # (not a TaxTransaction field — they feed only the schema notional
    # check below, which is an ERROR for rows that declare one).
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
            shared_context = _prepare(input_paths)
        except csv.Error as e:
            print(f"taxjson-brokerage: error: the CSV module refused an "
                  f"input file ({e}) — see the per-file error below by "
                  f"parsing the files one at a time.", file=sys.stderr)
            sys.exit(2)
        except BrokerageParseError as e:
            print(f"taxjson-brokerage: error: {e}", file=sys.stderr)
            sys.exit(1)

    parsed_files = []
    for input_path in input_paths:
        # Fresh extractor per file so any extractor-level state (e.g.
        # IB's `unhandled_ca_tickers` warning bucket) doesn't bleed
        # across files and emit confused diagnostics.
        extractor = extractor_class()
        if shared_context is not None:
            extractor.account_context = shared_context
        if hasattr(extractor, 'foreign_return_of_capital'):
            extractor.foreign_return_of_capital = args.foreign_roc
        if hasattr(extractor, 'futures_settle'):
            extractor.futures_settle = args.futures_settle
        if args.account_type and hasattr(extractor, 'account_taxable'):
            extractor.account_taxable = args.account_type == 'taxable'
        try:
            transactions = extractor.parse_file(input_path)
        except csv.Error as e:
            # A >128KB field (or other csv-module limit) surfaced as a
            # raw traceback; name the file and the limit instead
            # (2026-09 security audit).
            print(f"taxjson-brokerage: error: {input_path.name}: the "
                  f"CSV module refused the file ({e}). A single field "
                  f"exceeding {csv.field_size_limit()} characters is "
                  f"the usual cause — inspect/trim the offending row.",
                  file=sys.stderr)
            sys.exit(2)
        except BrokerageParseError as e:
            # The parser refused the file rather than guess (missing
            # required column, unparseable money, a row whose money does
            # not add up, a Cash Report mismatch, a non-activity report).
            # A finding in the DATA: exit 1, one line, no traceback.
            print(f"taxjson-brokerage: error: {input_path.name}: {e}",
                  file=sys.stderr)
            sys.exit(1)
        parsed_files.append((input_path, extractor, transactions))

    # A Corporate Actions `Ca` cancellation whose original sits in
    # ANOTHER of the account's statements (IB: booked in the 2025
    # statement, cancelled and rebooked in the 2026 one) undoes it there
    # (audit S059-04); the rest are warned about once.
    for _i, (_p, _ex, _txs) in enumerate(parsed_files):
        for _ca in getattr(_ex, 'unmatched_ca', None) or ():
            _done = False
            for _j in range(len(parsed_files) - 1, -1, -1):
                _other = parsed_files[_j][1]
                if _j != _i and getattr(_other, 'ca_undo', None) \
                        and _other.ca_undo(_ca):
                    print(f"note: {_p.name}: IB cancelled (Ca) "
                          f"{_ca['desc']!r} ({_ca['qty']:g} on "
                          f"{_ca['date']}); its original in "
                          f"{parsed_files[_j][0].name} is undone.",
                          file=sys.stderr)
                    _done = True
                    break
            if not _done and getattr(_ex, 'account_context', None) \
                    is not None:
                print(ib_extractor.unmatched_ca_warning(_ca),
                      file=sys.stderr)

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

    for input_path, extractor, transactions in parsed_files:
        _kept_this_file = 0     # TRANSFER evidence rows set aside below

        # Correct mislabeled tickers FIRST — before the TRANSFER rows
        # are set aside (the sidecar used to keep the un-overridden
        # symbol, so an evidenced depot flip never matched: S027-01)
        # and before the id hash is computed, so dedup and every
        # downstream tool see the right symbol.
        for t in transactions:
            apply_security_override(t, overrides, override_hits)

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
                print(f"  {input_path.name}: {len(_tr)} TRANSFER "
                      f"row(s) kept aside (custody evidence, not tax "
                      f"events — view with `taxjson transfers`)",
                      file=sys.stderr)
                if brokerage_id in ('kraken', 'kr', 'coinbase', 'cb'):
                    _sends = sum(1 for t in _tr
                                 if float(t.get('quantity') or 0) < 0)
                    if _sends:
                        print(
                            f"  NOTE: {_sends} crypto withdrawal/"
                            f"send(s) among them — if any left your "
                            f"ownership (gift or payment), each is a "
                            f"taxable DISPOSITION at fair market "
                            f"value: `taxjson crypto-sends` lists "
                            f"them with the fair value and writes the "
                            f".tt sale for each gift/payment "
                            f"(self-custody moves need nothing).",
                            file=sys.stderr)
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
            print(f"  {input_path.name}: 0 tax objects "
                  f"({_kept_this_file} TRANSFER row(s) kept aside)",
                  file=sys.stderr)
        elif (not transactions and file_size > 0
              and getattr(extractor, 'zero_tx_reason', None)):
            # The parser knows why this file books nothing (a Kraken
            # ledger whose trade rows are all booked from the trades
            # export) — not the regression the warning below is for,
            # and a warning on every correct run trains users to
            # ignore real ones.
            print(f"  {input_path.name}: 0 tax objects "
                  f"({extractor.zero_tx_reason})", file=sys.stderr)
        elif not transactions and file_size > 0:
            print(
                f"warning: {input_path.name} parsed to 0 transactions "
                f"({file_size} bytes input, brokerage={brokerage_id}): "
                f"NONE of its rows are in the books. Check the CSV "
                f"header / format — silent zero-tx output is usually a "
                f"changed export layout or a parser regression.",
                file=sys.stderr,
            )
        else:
            print(f"  {input_path.name}: {len(transactions)} tax objects",
                  file=sys.stderr)

        if args.lint and getattr(extractor, '_rows_seen', None) is not None:
            seen = extractor._rows_seen
            consumed = extractor._rows_consumed
            skipped = sum(extractor._skip_counts.values())
            unaccounted = seen - consumed - skipped
            print(f"lint: {input_path.name}: rows={seen} consumed={consumed} "
                  f"skipped={skipped} unaccounted={unaccounted}",
                  file=sys.stderr)
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
                print(f"lint: {input_path.name}: {_f}", file=sys.stderr)
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
            # Only override the parser's account label when --account
            # was explicitly given. Defaulting to the literal "default"
            # (the old behaviour) silently erased the per-parser
            # DEFAULT_ACCOUNT ("IB", "Kraken", etc.) for users who
            # forgot the flag.
            if args.account_name is not None:
                clean['account'] = args.account_name
            normalized.append(TaxTransaction(**clean))
            multipliers.append(t.get('multiplier'))

    for key, n in sorted(dropped_keys.items()):
        print(f"warning: parser emitted unknown field {key!r} on {n} "
              f"transaction(s) — not part of the TaxTransaction schema, "
              f"DROPPED. Fix the parser or add the field to the schema.",
              file=sys.stderr)

    # Schema validation on what downstream actually sees. Errors are
    # violations that corrupt tax math; they abort only under --strict
    # (or --lint) so a mid-season odd export still produces output.
    errors, schema_warnings = validate_transactions(
        [({**t.to_dict(), 'multiplier': m} if m else t.to_dict())
         for t, m in zip(normalized, multipliers)], lint=args.lint)
    for w in schema_warnings:
        print(f"warning: schema: {w}", file=sys.stderr)
    for e in errors:
        print(f"{'error' if (args.strict or args.lint) else 'warning: schema VIOLATION'}: {e}",
              file=sys.stderr)
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
            "source_brokerage": brokerage_id,
            "input_files": [str(p) for p in input_paths],
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
        _tmp = _sp.with_suffix(_sp.suffix + ".part")
        _tmp.write_text(json.dumps(
            {"transactions": kept_aside,
             "metadata": {"kind": "transfer_sidecar",
                          "account": args.account_name,
                          "brokerage": brokerage_id}},
            indent=2, sort_keys=True), encoding="utf-8")
        _tmp.replace(_sp)
    json.dump(output_data, sys.stdout, indent=2, sort_keys=True)
    print()


if __name__ == "__main__":
    main()
