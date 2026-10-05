#!/usr/bin/env python3
"""taxjson-missing-history - find stocks with missing cost-basis data.

Two ways acquisition data goes missing, both of which distort a year's gain:

  1. TRUNCATED HISTORY - the export doesn't reach back to when a position was
     opened, so the engine sees only the sale and the running share count goes
     NEGATIVE. The tell-tale sign of a missing buy.

  2. $0-COST CORP ACTION - shares received through a merger/spinoff are booked
     by the broker with value 0, so they enter the pool at a ZERO basis and
     inflate the gain when sold. These never go negative (received +N, sold
     −N), so #1 can't catch them - this is its complement.

Either way the tool splits the findings by whether they bear on a given year:

  * AFFECTS <year>   - has an in-year sale drawing on the missing basis, so it
                       distorts that year's gain. Worth fixing before you file.
  * not relevant     - the issue is confined to other years (or has since
                       resolved). Safe to ignore for <year>.

Feed it the per-account base file the pipeline already builds - the FULL
transaction history, not the year-filtered gains file:

  taxjson-missing-history --year 2025 work/margin_base.json
  taxjson-missing-history --year 2025 work/*_base.json
  taxjson-missing-history work/margin_base.json     # no year scope

To fix an AFFECTS row, add the purchase. When the broker states its cost
(IB's Basis on a sale coded C, a Questrade transfer-in's book value),
--write-purchases drafts the .tt lines for you to review, into a file the
run does not read (inputs/<account>/purchases_draft.tt.txt in a project):
  taxjson find-missing-history --write-purchases
  # fill in each YYYY-MM-DD / COST, then rename the file to .tt
Only when a purchase cannot be recovered, list the sale in a
missing-history file and re-run. In a project:
  taxjson find-missing-history --write-missing-history   # writes missing_history.json
  # then prune any real shorts from it, and: taxjson run
Standalone:
  taxjson-gains --country canada --year <year> \
      --suggest-missing-history missing_history.json <base.json>
  # review/prune, then: taxjson-gains --country canada \
  #     --incomplete-history missing_history.json ...   (usa for a US project)
"""

import argparse
import json
import sys
from pathlib import Path

from taxjson.lib.cli_diag import guard_main, tax_year
from taxjson.lib.core import load_transactions
from taxjson.lib.missing_history import (
    detect_missing_history, assess_tax_year_relevance, detect_zero_basis_acquisitions,
    detect_corp_action_links, detect_unbacked_covers, stale_missing_history_entries,
    stale_entry_message, DRAFT_NAME, DATE_PLACEHOLDER as _PH_DATE,
    COST_PLACEHOLDER as _PH_COST,
)


def _load_all(paths):
    """(transactions, [paths that failed to load])."""
    txs = []
    failed = []
    for p in paths:
        try:
            txs.extend(load_transactions(Path(p)))
        except (OSError, ValueError) as e:
            print(f"error loading {p}: {e}", file=sys.stderr)
            failed.append(str(p))
    return txs, failed


def _is_derivative(symbol: str) -> bool:
    from taxjson.lib.core import is_option_symbol
    return (is_option_symbol(symbol or '')
            or (symbol or '').startswith(('F:', '/', '\\')))


def _print_section(title, rows, *, show_year_cols):
    if not rows:
        return
    print(f"\n{title}")
    if show_year_cols:
        hdr = (f"{'Symbol':<24} {'Account':<10} {'Cur':<4} {'PeakShort':>12} "
               f"{'FirstNeg':<12} {'InYrSales':>10} {'InYrProceeds':>14} {'Reg'}")
    else:
        hdr = (f"{'Symbol':<24} {'Account':<10} {'Cur':<4} {'PeakShort':>12} "
               f"{'FirstNeg':<12} {'Sales<0':>10} {'EndPos':>12} {'Reg'}")
    print("-" * len(hdr))
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        c = r.candidate
        reg = "REG" if c.registered else ""
        if show_year_cols:
            print(f"{c.symbol:<24} {c.account:<10} {(c.currency or '?'):<4} "
                  f"{c.peak_short:12.4f} {c.first_negative_date:<12} "
                  f"{r.in_year_dispositions:10d} {r.in_year_proceeds:14.2f} {reg}")
        else:
            print(f"{c.symbol:<24} {c.account:<10} {(c.currency or '?'):<4} "
                  f"{c.peak_short:12.4f} {c.first_negative_date:<12} "
                  f"{r.in_year_dispositions:10d} {c.end_position:12.4f} {reg}")
        if getattr(c, 'broker_says_closing', False):
            # (indented: a detail line, not a table row — the checklist
            # counts rows only)
            print(f"    broker says closing (IB code C): the sale closed a "
                  f"position bought before the data"
                  + (f" (IB Basis {c.broker_basis})" if c.broker_basis
                     else "")
                  + " — add the missing purchase; it is not a short sale"
                  + (" or a written option" if _is_derivative(c.symbol)
                     else "") + "."
                  + (" `--write-purchases` drafts the line from IB's "
                     "figure for you to review."
                     if c.broker_basis and not c.symbol.startswith(
                         ('F:', '/', '\\')) else ""))


def _print_zero_section(title, rows):
    if not rows:
        return
    print(f"\n{title}")
    hdr = (f"{'Symbol':<24} {'Account':<10} {'Cur':<4} {'ZeroQty':>10} "
           f"{'AcqDate':<12} {'InYrSales':>10} {'InYrProceeds':>14} {'Why'}")
    print("-" * len(hdr))
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        why = "corp action" if r.looks_corp_action else "$0 cost"
        print(f"{r.symbol:<24} {r.account:<10} {(r.currency or '?'):<4} "
              f"{r.zero_cost_qty:10.4f} {r.acquisition_date:<12} "
              f"{r.in_year_dispositions:10d} {r.in_year_proceeds:14.2f} {why}")
        if r.description:
            print(f"    └ {r.description}")


def _sheltered_title(yr, country) -> str:
    """Heading of the registered-account sections. It never starts with
    'AFFECTS', so the checklist does not count these rows as affecting
    the year's gain. The loss rule is named by the project's country."""
    rule = {"canada": "superficial-loss", "usa": "wash-sale"}.get(
        country or "", "cross-account loss")
    # A US project's sheltered accounts are IRAs / retirement accounts,
    # not Canada's registered accounts (A2-1319).
    kind = {"usa": "retirement-account (IRA)"}.get(country or "",
                                                   "registered-account")
    return (f"SHELTERED {yr} - {kind} positions: no reportable "
            f"gain there; the missing history matters only to the "
            f"{rule} walk:")


def _rename_sources(map_path, symbols):
    """{reported symbol: [ticker.map source spellings]} for each symbol
    (or option underlying) that a GLOBAL/TOBASE/JOURNAL rule renames
    INTO. {} without a readable map."""
    if not map_path or not symbols:
        return {}
    try:
        from taxjson.bin.taxjson_ticker_map import load_map_file
        tm = load_map_file(Path(map_path))
    except Exception as e:                          # noqa: BLE001
        print(f"taxjson-missing-history: warning: could not read "
              f"{map_path} ({e}) — renamed symbols are not traced back.",
              file=sys.stderr)
        return {}
    from taxjson.lib.core import is_option_symbol, parse_option_underlying
    by_target = {}
    for attr in ("glob", "tobase", "journal"):
        for src, dst in (getattr(tm, attr, {}) or {}).items():
            by_target.setdefault(str(dst).upper(), set()).add(str(src))
    out = {}
    for sym in symbols:
        key = str(sym or "").upper()
        if is_option_symbol(key):
            key = str(parse_option_underlying(key) or key).upper()
        if key in by_target:
            out[sym] = sorted(by_target[key])
    return out


def _project_root(path):
    """The project folder holding taxjson.toml beside a book file
    (<root>/work/<acct>_base.json) or one level up; None outside one."""
    try:
        p = Path(path).resolve()
    except (OSError, RuntimeError):
        return None
    for d in (p.parent, p.parent.parent):
        if (d / "taxjson.toml").is_file():
            return d
    return None


def _transfer_sidecar_rows(files):
    """Every TRANSFER row of the transfer sidecars beside the book files
    (work/<account>_<broker>_transfers.json — the rows `taxjson run`
    leaves out of a taxable account's books), each stamped with its
    sidecar's account."""
    rows, seen = [], set()
    for f in files:
        d = Path(f).resolve().parent
        if d in seen:
            continue
        seen.add(d)
        for sc in sorted(d.glob("*_transfers.json")):
            try:
                doc = json.loads(sc.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            md = (doc.get("metadata") or {}) if isinstance(doc, dict) \
                else {}
            if not isinstance(md, dict) or md.get("kind") != \
                    "transfer_sidecar" or not md.get("account"):
                continue
            for t in doc.get("transactions") or []:
                if isinstance(t, dict) and t.get("action") == "TRANSFER":
                    rows.append(dict(t, account=md["account"]))
    return rows


def _write_purchases(args, txs, *, country, basis, types, journal,
                     unchecked):
    """--write-purchases: draft .tt purchase lines (tax-logic CA-ACB-15 /
    US-BASIS-08) into a file the run does not read."""
    from taxjson.lib.missing_history import (draft_purchases,
                                              format_purchase_drafts)
    from taxjson.lib.safe_write import write_atomic
    country = country or args.country
    if not country:
        print("taxjson-missing-history: error: --write-purchases needs the "
              "project's country: run it on a project's work/ files "
              "(`taxjson find-missing-history --write-purchases`) or pass "
              "--country canada|usa.", file=sys.stderr)
        return 2
    mh_pairs = set()
    if args.missing_history:
        try:
            for e in json.loads(Path(args.missing_history).read_text(
                    encoding="utf-8-sig")) or []:
                if isinstance(e, dict) and e.get("symbol") \
                        and e.get("account"):
                    mh_pairs.add((str(e["symbol"]).strip().upper(),
                                  str(e["account"]).strip()))
        except (OSError, ValueError) as e:
            print(f"taxjson-missing-history: warning: could not read "
                  f"{args.missing_history}: {e}", file=sys.stderr)
    accounts = sorted({t.account for t in txs if t.account})
    if args.account:
        accounts = [args.account]
    transfers = [r for r in _transfer_sidecar_rows(args.files)
                 if float(r.get("quantity") or 0) < 0
                 or r.get("account") in accounts]
    renamed = _rename_sources(args.ticker_map,
                              {t.symbol for t in txs if t.symbol})
    drafts, gaps = draft_purchases(
        txs, accounts=set(accounts), country=country,
        year=None if args.all_history else args.year, date_basis=basis,
        transfer_rows=transfers, registered_accounts=types or None,
        journal_symbols=journal, listed_pairs=mh_pairs,
        rename_sources=renamed)
    by_acct = {}
    for d in drafts:
        by_acct.setdefault(d.account, [[], []])[0].append(d)
    for g in gaps:
        by_acct.setdefault(g.account, [[], []])[1].append(g)
    scope = (f" with a sale in {args.year}" if args.year
             and not args.all_history else "")
    if not drafts:
        print(f"No purchase to draft{scope}: no sale with no purchase in "
              f"your files carries a broker cost (IB's Basis), and no "
              f"transfer-in states a book value.")
        for g in gaps:
            print(f"  not drafted: {g.symbol} [{g.account}] {g.date} "
                  f"{g.quantity:g} units — {g.reason}")
        return 1 if unchecked else 0
    explicit = Path(args.write_purchases) if args.write_purchases else None
    drafting = sorted(a for a, (ds, _) in by_acct.items() if ds)
    if explicit is not None and len(drafting) > 1:
        print(f"taxjson-missing-history: error: drafts for "
              f"{len(drafting)} accounts ({', '.join(drafting)}) — a .tt "
              f"file belongs to one account: name it (--account, or "
              f"`taxjson find-missing-history ACCOUNT --write-purchases "
              f"FILE`), or leave FILE out to write "
              f"inputs/<account>/{DRAFT_NAME} for each.", file=sys.stderr)
        return 2
    root = _project_root(args.files[0])
    if explicit is None and root is None:
        print(f"taxjson-missing-history: error: no project around "
              f"{args.files[0]} — pass --write-purchases FILE.",
              file=sys.stderr)
        return 2
    targets = {a: (explicit if explicit is not None
                   else root / "inputs" / a / DRAFT_NAME)
               for a in drafting}
    for a, out in targets.items():
        if (out.exists() or out.is_symlink()) and not args.force:
            print(f"taxjson-missing-history: error: {out} already exists "
                  f"— not overwritten (it may hold your edits). Rename "
                  f"what you reviewed to .tt first, or pass --force to "
                  f"replace it (the old file is kept as "
                  f"{out.name}.bak).", file=sys.stderr)
            return 1
        if out.suffix.lower() in (".tt", ".csv"):
            print(f"taxjson-missing-history: error: {out.name}: a draft "
                  f"must not end in {out.suffix} — the run would read it "
                  f"before you reviewed it (use a name ending in .txt, "
                  f"e.g. {DRAFT_NAME}).", file=sys.stderr)
            return 2
    for a in drafting:
        ds, gs = by_acct[a]
        out = targets[a]
        text = format_purchase_drafts(ds, gs, country=country, account=a)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            if out.is_file() and not out.is_symlink():
                write_atomic(out.with_name(out.name + ".bak"),
                             out.read_bytes())
                print(f"  kept the previous {out.name} as "
                      f"{out.name}.bak", file=sys.stderr)
            write_atomic(out, text)
        except OSError as e:
            print(f"taxjson-missing-history: error: cannot write {out}: "
                  f"{e}", file=sys.stderr)
            return 2
        n_lot = sum(1 for d in ds if d.source == "ib-lot")
        n_ib = sum(1 for d in ds if d.source == "ib-basis")
        n_tr = sum(1 for d in ds if d.source == "transfer")
        n_date = sum(1 for d in ds if d.date is None)
        n_cost = sum(1 for d in ds if d.cost is None)
        n_chk = sum(1 for d in ds if d.warn)
        shown = out
        try:
            shown = out.resolve().relative_to(root.resolve()) if root \
                else out
        except ValueError:
            pass
        parts = [f"{n} {what}" for n, what in (
            (n_lot, "from IB's closed lots"),
            (n_ib, "from IB's Basis"),
            (n_tr, "from a transfer-in's book value")) if n]
        print(f"Wrote {len(ds)} draft purchase line(s) for {a} to {shown} "
              f"({'; '.join(parts)}).")
        todo = [f"{n} need {what}" for n, what in (
            (n_date, f"the purchase date ({_PH_DATE})"),
            (n_cost, f"the cost ({_PH_COST})"),
            (n_chk, "a CHECK resolved")) if n]
        if todo:
            print(f"  {'; '.join(todo)}.")
        if gs:
            print(f"  {len(gs)} sale(s) or transfer-in(s) of {a} could "
                  f"not be drafted — listed at the end of the file.")
    print(f"taxjson does not read a draft. Review every line, fill in "
          f"the placeholders, then rename the file to end in .tt (e.g. "
          f"purchases.tt) and `taxjson run`; `taxjson find-missing-"
          f"history` should then drop the fixed positions.")
    return 1 if unchecked else 0


@guard_main("taxjson-missing-history")
def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", metavar="FILE",
                    help="per-account base JSON (full history), e.g. "
                         "work/<account>_base.json")
    ap.add_argument("--year", type=tax_year, metavar="YYYY",
                    help="tax year to assess relevance against; omit to list "
                         "every short position with no year scope")
    ap.add_argument("--account", metavar="NAME",
                    help="only report this account")
    ap.add_argument("--unchecked-account", action="append", default=[],
                    metavar="NAME",
                    help="an account the caller could not supply a book "
                         "for (repeatable): reported as NOT checked, so "
                         "the run never ends in an all-clear")
    ap.add_argument("--missing-history", metavar="FILE",
                    help="the project's missing_history.json: pairs it "
                         "covers (the run applies them) are listed apart, "
                         "not as work still to do")
    # The flag's old name (the file was phantoms.json): hidden, still
    # accepted with a note.
    ap.add_argument("--phantoms", metavar="FILE", dest="phantoms_old",
                    help=argparse.SUPPRESS)
    ap.add_argument("--include-options", action="store_true",
                    help="also include OCC option symbols and futures (a "
                         "negative position is normal sell-to-open, so "
                         "skipped by default — except a sale the broker "
                         "codes CLOSING, IB code C, which is always "
                         "reported)")
    ap.add_argument("--ticker-map", metavar="FILE",
                    help="the project's ticker.map: a reported symbol "
                         "that is a rename target is shown with the "
                         "broker's own ticker, where a missing buy "
                         "belongs")
    ap.add_argument("--write-purchases", metavar="FILE", nargs="?",
                    const="", default=None,
                    help="instead of the report, DRAFT .tt purchase lines "
                         "from the broker's own cost evidence (IB's Basis "
                         "on a closing sale with no purchase in the files, "
                         "a Questrade transfer-in's book value) into a "
                         "file the run does not read: default "
                         f"inputs/<account>/{DRAFT_NAME} in the project. "
                         "Review it, fill in the placeholders, and rename "
                         "it to .tt")
    ap.add_argument("--all-history", action="store_true",
                    help="with --write-purchases: draft every position, "
                         "not only those with a sale in --year")
    ap.add_argument("--force", action="store_true",
                    help="with --write-purchases: replace an existing "
                         "draft (kept as <file>.bak)")
    ap.add_argument("--country", choices=("canada", "usa"),
                    help="with --write-purchases outside a project: the "
                         "country whose rules the draft's notes follow "
                         "(a project's taxjson.toml decides otherwise)")
    args = ap.parse_args(argv)
    if args.phantoms_old:
        print("taxjson-missing-history: note: --phantoms is now "
              "--missing-history (the old flag still works).",
              file=sys.stderr)
        if not args.missing_history:
            args.missing_history = args.phantoms_old
    # The file's name as the user has it (missing_history.json, or the
    # legacy phantoms.json), for the report's wording.
    mh_name = (Path(args.missing_history).name if args.missing_history
               else "missing_history.json")

    txs, failed = _load_all(args.files)
    if not txs:
        print("No transactions loaded.", file=sys.stderr)
        # An input that could not be read is exit 2, not the 'finding'
        # code (A2-0164).
        return 2 if failed else 1
    if args.account:
        # An --account no row carries (a typo, the wrong case) filtered
        # every finding away and printed the all-clear (audit S035-07).
        present = sorted({t.account for t in txs if t.account})
        if args.account not in present:
            print(f"taxjson-missing-history: error: no row carries "
                  f"account {args.account!r} (accounts in the books: "
                  f"{', '.join(present) or 'none'})", file=sys.stderr)
            return 2
    # Anything not read is anything not checked: an all-clear (exit 0)
    # after a load error read as "nothing affects the year" in the
    # checklist (2026-09 audit R1-336, S047-18).
    unchecked = ([Path(f).name for f in failed]
                 + [f"account {a} (no book)"
                    for a in args.unchecked_account])

    def _incomplete(rc):
        if not unchecked:
            return rc
        print(f"\nINCOMPLETE: not checked: {', '.join(unchecked)} — "
              f"missing basis there is not reported.")
        print(f"taxjson-missing-history: {len(unchecked)} input(s) not "
              f"checked: {', '.join(unchecked)}", file=sys.stderr)
        return 1

    # --- 0. Reconstruct split-across-symbols mergers first, so the two halves
    #        (old removed / new received) are reported as one event rather than
    #        an unrelated short + $0-basis buy. ---
    links = detect_corp_action_links(txs, include_options=args.include_options)
    if args.account:
        links = [l for l in links if l.account == args.account]
    linked_old = {(l.old_symbol, l.account) for l in links}
    linked_new = {(l.new_symbol, l.account) for l in links}

    # --- 1. Truncated history (positions go negative) ---
    # The project's configured account types and tax_date basis, when
    # the base files sit in a project (audit S076-08, S075-16).
    from taxjson.lib.missing_history import account_types_near, tax_date_near
    types = account_types_near(args.files[0])
    from taxjson.lib.country import CountryError
    try:
        basis = tax_date_near(args.files[0])
    except CountryError as e:
        print(f"taxjson-missing-history: taxjson.toml: {e}", file=sys.stderr)
        return 2
    # The project's country names the registered-account rule in the
    # SHELTERED sections (superficial loss vs wash sale); None outside a
    # project (tax_date_near above already refused a bad country).
    from taxjson.lib.missing_history import _project_doc_near
    from taxjson.lib.country import settings_country
    country = (settings_country(_project_doc_near(args.files[0])
                                .get('settings') or {})
               if basis is not None else None)
    # The cost word in the hints: each country's own (A2-1319).
    from taxjson.lib.country import COST_TERM
    _cost = COST_TERM[country]
    if basis is None:
        basis = "settle"
        print("taxjson-missing-history: note: no taxjson.toml beside the "
              "input — a row's tax year is taken from its SETTLEMENT date "
              "(run it on a project's work/ files to use the project's "
              "country and tax_date)", file=sys.stderr)
    # ticker.map JOURNAL symbols: a same-day Norbert's-gambit pair is
    # not a one-day short with a missing purchase (audit A2-0636 /
    # A2-0309).
    journal = set()
    if args.ticker_map:
        from taxjson.lib.missing_history import journal_targets
        try:
            journal = journal_targets(args.ticker_map)
        except Exception as e:                      # noqa: BLE001
            print(f"taxjson-missing-history: warning: could not read "
                  f"{args.ticker_map} ({e}) — JOURNAL pairs are walked "
                  f"in clock order.", file=sys.stderr)
    if args.write_purchases is not None:
        return _write_purchases(args, txs, country=country, basis=basis,
                                types=types, journal=journal,
                                unchecked=unchecked)
    candidates = detect_missing_history(txs, include_options=args.include_options,
                                 include_broker_shorts=True,
                                 registered_accounts=types or None,
                                 journal_symbols=journal)
    if args.account:
        candidates = [c for c in candidates if c.account == args.account]
    # A short the broker itself marks as a short sale (RBC "SHORT." /
    # "COVER SHORT.") is a real short, not a missing buy: reported apart
    # and never offered as missing history (audit R1-8 — a real loss
    # vanished behind one).
    broker_shorts = [c for c in candidates if c.broker_marked_short]
    candidates = [c for c in candidates if not c.broker_marked_short]
    short_rows = [r for r in assess_tax_year_relevance(txs, candidates, args.year,
                                                       date_basis=basis,
                                                       journal_symbols=journal)
                  if (r.candidate.symbol, r.candidate.account) not in linked_old]

    # --- 1b. Broker-marked covers no short in the data backs (RBC
    #         "COVER SHORT.", IB code C on a buy): the short was opened
    #         before the data — the mirror of a sale going short (audit
    #         A2-0306, IB twin A2-0175). ---
    covers = [c for c in detect_unbacked_covers(
                  txs, include_options=args.include_options,
                  date_basis=basis, journal_symbols=journal)
              if not args.account or c.account == args.account]

    # --- 1c. missing-history entries today's detection would NOT propose
    #         (a broker-marked real short, a written option): applied by
    #         the run anyway, so listed for removal (A2-0639 / A2-0311). ---
    stale = []
    ph_pairs = set()
    if args.missing_history:
        try:
            for e in json.loads(Path(args.missing_history).read_text(
                    encoding="utf-8-sig")) or []:
                if isinstance(e, dict) and e.get("symbol") \
                        and e.get("account"):
                    ph_pairs.add((str(e["symbol"]).strip().upper(),
                                  str(e["account"]).strip()))
        except (OSError, ValueError):
            pass                     # reported below (the _ph read)
        stale = [e for e in stale_missing_history_entries(
                     txs, ph_pairs, file_name=mh_name)
                 if not args.account or e.account == args.account]
    stale_pairs = {(e.symbol.upper(), e.account) for e in stale}

    # --- 2. $0-cost corp-action acquisitions later sold ---
    zero_rows = [r for r in detect_zero_basis_acquisitions(
                    txs, args.year, include_options=args.include_options,
                    date_basis=basis)
                 if (r.symbol, r.account) not in linked_new
                 and (not args.account or r.account == args.account)]

    if broker_shorts:
        print(f"\n## Broker-marked short sales (real shorts, NOT missing "
              f"history): {len(broker_shorts)}")
        for c in broker_shorts:
            _how = ("codes the sale O (opening)"
                    if c.short_marker == 'IB code O'
                    else "marks the sales SHORT." if c.short_marker
                    in ('', 'SHORT.')
                    else f"marks the sales short ({c.short_marker})")
            _listed = (str(c.symbol).upper(), c.account) in stale_pairs
            print(f"  {c.symbol} [{c.account}] went short on "
                  f"{c.first_negative_date} (peak {c.peak_short:g}); the "
                  f"broker {_how} — "
                  + (f"but {mh_name} LISTS it: remove that entry."
                     if _listed else
                     f"nothing to fix, do not add it to {mh_name}."))

    if stale:
        # Heading starts with REMOVE: the checklist counts these rows.
        print(f"\n## {mh_name} entries that are not missing history: "
              f"{len(stale)}")
        print(f"REMOVE from {mh_name} - the run applies them and they "
              "move a real gain or loss off the totals:")
        for e in stale:
            print(f"{e.symbol} {e.account}")
            print(f"    {stale_entry_message(e)}")

    if covers:
        print(f"\n## Covers of a short opened before the data (missing "
              f"history): {len(covers)}")
        yr_c = str(args.year) if args.year else None
        aff = [c for c in covers if yr_c is None or c.date.startswith(yr_c)]
        oth = [c for c in covers if c not in aff]
        hdr = (f"{'Symbol':<24} {'Account':<10} {'Date':<12} "
               f"{'Unbacked':>12} {'Cost':>14} {'Marker'}")
        for title, rows in (
                ((f"AFFECTS {yr_c} - covering the short is the disposition; "
                  f"its gain or loss is missing (the books carry the buy "
                  f"as a new long). Add the short sale that opened it "
                  f"(date, proceeds) to a .tt file in this account:")
                 if yr_c else
                 "Broker-marked covers with no short in the data:", aff),
                (f"NOT relevant to {yr_c} - covers in other years:", oth)):
            if not rows:
                continue
            print(f"\n{title}")
            print("-" * len(hdr))
            print(hdr)
            print("-" * len(hdr))
            for c in rows:
                print(f"{c.symbol:<24} {c.account:<10} {c.date:<12} "
                      f"{c.unbacked_qty:12.4f} {c.proceeds:14.2f} "
                      f"{c.marker}")

    if not short_rows and not zero_rows and not links:
        scope = f" (account {args.account})" if args.account else ""
        if stale or covers:
            return _incomplete(0)
        if unchecked:
            print(f"No missing-cost-basis issues found{scope} in the "
                  f"books that loaded.")
            return _incomplete(0)
        print(f"No missing-cost-basis issues found{scope}: no negative "
              "holdings, no $0-cost corp-action shares sold, no mergers.")
        return 0

    yr = args.year
    # Pairs the missing-history file already covers (R1-339: the report
    # kept asking for the file-writing step the user had done, and the
    # checklist step never cleared).
    _ph: set = set()
    if args.missing_history:
        try:
            for e in json.loads(Path(args.missing_history).read_text(
                    encoding="utf-8-sig")) or []:
                if isinstance(e, dict):
                    _ph.add((str(e.get("symbol") or "").upper(),
                             str(e.get("account") or "").lower()))
        except (OSError, ValueError) as e:
            print(f"taxjson-missing-history: warning: could not read "
                  f"{args.missing_history}: {e}", file=sys.stderr)

    def _covered(r) -> bool:
        c = r.candidate
        return (str(c.symbol).upper(), str(c.account).lower()) in _ph

    # === Section 0: reconstructed mergers ===
    if links:
        print(f"\n## Reconstructed mergers (old symbol -> new symbol): "
              f"{len(links)} event(s)")
        for l in links:
            ratio = f", ratio {l.ratio:g} new=1 old" if l.ratio else ""
            print(f"  {l.date} [{l.account}]  {l.old_symbol} "
                  f"({l.old_company or '?'}) -> {l.new_symbol} "
                  f"({l.new_company or '?'}){ratio}")
            print(f"      {l.old_qty:g} {l.old_symbol} removed; "
                  f"{l.new_qty:g} {l.new_symbol} received at $0 basis.")
            print(f"      The old shares' {_cost} is missing (their purchase isn't "
                  f"in your data). Supply it so {l.new_symbol} carries the "
                  f"correct basis - otherwise {l.new_symbol}'s sale gain is "
                  f"overstated by that amount.")
    # === Section 1: truncated history ===
    if short_rows:
        print(f"\n## Truncated history - positions go short (missing a buy): "
              f"{len(short_rows)} pair(s)")
        if yr:
            affects = [r for r in short_rows if r.affects_year
                       and not r.candidate.registered and not _covered(r)]
            covered = [r for r in short_rows if r.affects_year
                       and not r.candidate.registered and _covered(r)]
            sheltered = [r for r in short_rows if r.affects_year
                         and r.candidate.registered]
            ignorable = [r for r in short_rows if not r.affects_year]
            print(f"   {len(affects) + len(covered)} affect tax year {yr}"
                  + (f" ({len(covered)} covered by {mh_name})"
                     if covered else "")
                  + f"; {len(sheltered)} are in registered accounts; "
                    f"{len(ignorable)} do not.")
            _print_section(f"AFFECTS {yr} - missing basis distorts this year's "
                           "gain; fix before filing:", affects, show_year_cols=True)
            # Pairs the missing-history file already covers (the run
            # applies them) are not work still to do (R1-339).
            _print_section(f"COVERED by {mh_name} - the run applies these "
                           f"openings; nothing more to do unless `taxjson "
                           f"sum` lists the sale under manual reporting:",
                           covered, show_year_cols=True)
            # A registered account has no reportable gain: listing its
            # rows under "distorts this year's gain" (and counting them
            # in the checklist) told the user to fabricate basis there
            # (audit S035-08). Their history still matters to the
            # cross-account loss rule, so they are listed apart.
            _print_section(_sheltered_title(yr, country), sheltered,
                           show_year_cols=True)
            _print_section(f"NOT relevant to {yr} - short only from other-year "
                           "sales (or since drained); safe to ignore:",
                           ignorable, show_year_cols=True)
        else:
            _print_section("Short positions (all history):", short_rows,
                           show_year_cols=False)

    # === Section 2: $0-cost corp-action acquisitions ===
    if zero_rows:
        print(f"\n## $0-cost corp-action shares that were later sold "
              f"(inflated gain): {len(zero_rows)} pair(s)")
        from taxjson.lib.missing_history import is_registered_account

        def _reg(r):
            return is_registered_account(r.account, types or None, country)
        rel = ([r for r in zero_rows if r.affects_year and not _reg(r)]
               if yr else zero_rows)
        shel = ([r for r in zero_rows if r.affects_year and _reg(r)]
                if yr else [])
        irr = [r for r in zero_rows if not r.affects_year] if yr else []
        if yr:
            print(f"   {len(rel)} affect tax year {yr}; {len(shel)} are in "
                  f"registered accounts; {len(irr)} do not.")
        _print_zero_section(
            (f"AFFECTS {yr} - sold this year against a $0 basis; the gain is "
             "overstated by the missing basis:") if yr
            else "Acquired at $0 cost and sold (all history):", rel)
        if shel:
            _print_zero_section(_sheltered_title(yr, country), shel)
        if irr:
            _print_zero_section(
                f"NOT relevant to {yr} - sold in other years; safe to ignore:",
                irr)

    # The rows show the books' symbol — AFTER ticker.map — in the base
    # currency: ABC.TO / CAD for a short the broker booked as ABC.US
    # in USD (TOBASE ABC.US ABC.TO). A missing buy entered as reported lands on a listing the
    # broker never used and breaks the holdings hand-off (S049-01).
    _renamed = _rename_sources(args.ticker_map,
                               {r.candidate.symbol for r in short_rows}
                               | {r.symbol for r in zero_rows})
    if _renamed:
        print("\nNOTE: these symbols are ticker.map's consolidated names "
              "(amounts in the base currency); the broker booked them "
              "as: " + "; ".join(f"{t} <- {', '.join(srcs)}"
                                 for t, srcs in sorted(_renamed.items()))
              + ". Enter a missing buy under the broker's symbol and "
                "currency, as the account labels it.")

    if (yr and (any(r.affects_year and not _covered(r)
                    for r in short_rows)
                or any(r.affects_year for r in zero_rows))):
        # (registered rows included: their openings still feed the
        # cross-account loss walk)
        _short_open = any(r.affects_year and not _covered(r)
                          for r in short_rows)
        _zero_open = any(r.affects_year for r in zero_rows)
        if _short_open:
            print("\nTo fix a sale with no purchase in your files, in this "
                  "order: (1) add an older export that holds the purchase "
                  "to inputs/<account>/; (2) or enter the purchase as a "
                  ".tt BUYSELL line with its real date and cost (for "
                  "shares transferred in: the original purchase at the "
                  "other broker) — `taxjson find-missing-history "
                  "--write-purchases` drafts these lines from IB's Basis "
                  "or a transfer's stated book value, for you to review; "
                  "(3) only when it cannot be recovered: "
                  "`taxjson find-missing-history --write-missing-history` "
                  "in the project writes missing_history.json — review "
                  "it, then `taxjson run` (it picks missing_history.json "
                  "up); those sales are then left out of the totals and "
                  "must be reported by hand. Standalone: taxjson-gains "
                  f"--country {country or 'canada|usa'} --year {yr} "
                  "--suggest-missing-history missing_history.json "
                  "<base.json>, then --incomplete-history "
                  "missing_history.json.")
        if _zero_open:
            from taxjson.lib.country import is_canada
            _stk = ("a stock dividend: its declared amount as a .tt "
                    "ADJUST line on the dividend date (or "
                    "[[distributions]]); " if is_canada(country) else "")
            print(f"\nTo fix $0-cost shares, give them their {_cost}: "
                  f"{_stk}a merger or spin-off: its election (`taxjson "
                  "elect`); shares transferred in: a .tt BUYSELL with the "
                  "original purchase date and cost.")
        print("Walk-through: docs/getting-started.md, step 5 "
              "(https://github.com/taxjson/taxjson/blob/main/docs/"
              "getting-started.md).")
    return _incomplete(0)


if __name__ == "__main__":
    sys.exit(main())
