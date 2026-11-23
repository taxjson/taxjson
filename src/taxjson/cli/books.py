"""The Build the books commands: run, compare, snapshot, crypto-sends, find-missing-history, opening."""
import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from taxjson.project.accounts import _transfers_accounts, accounts_of, account_of
from taxjson.project.settings import settings_of
from taxjson.lib import project_layout as _PL
from taxjson.lib import out as _out
from taxjson.lib.country import command_country_problem
from taxjson.lib.in_kind import IN_KIND_FILE
from taxjson.lib.out import exit_text, note as _note_out, show_blocks as _show_blocks
from taxjson.lib.pipeline import income_dating_flags, option_timing_flags
from taxjson.lib.report_fmt import fmt_qty
from taxjson.lib.stage_msg import emit_line

from taxjson.cli.common import (
    _accounts_skipped_for_no_inputs, _exec_tool, _raise_requirements,
    _relay_child_stderr, _shared_inputs_note, _unanswered_covers, _view_msg,
)
from taxjson.pipeline.config import _country, input_files, load_config, load_settings
from taxjson.pipeline.inputs import _home_currency, _tax_date
from taxjson.pipeline.messages import (
    _details, _die, _die_input, _out_wrap, _PROG, _run_concise, _say,
)
from taxjson.pipeline.run import (
    _cmd_run, _project_base_files, _year_short_rows,
)
from taxjson.pipeline.sanity import _json_out, _mask_ids_in_path, _project_file
from taxjson.pipeline.stages import (
    _crypto_broker_files, _crypto_sends_problems, _crypto_sends_tt,
    _disposing_word, _dup_warning, _missing_history_arg, _send_decisions,
    _sends_json_shown, _sends_tt_shown,
)
from taxjson.pipeline.tools import _cmd
from taxjson.lib.cli_diag import tax_year as _tax_year_arg
from taxjson.lib.numeric import positive_float_arg
from taxjson.lib.futures_prefixes import FUTURES_PREFIXES
from taxjson.model import money as M


def cmd_run(args: argparse.Namespace) -> None:
    """`tjs run` (_cmd_run). On a person's console without --details
    each message is one line (Essentials first, docs/output-style.md):
    said to the stages too (lib/stage_msg.CONCISE_ENV, whose own messages
    reach the console), and taken back when the run ends."""
    import os
    from taxjson.lib.stage_msg import CONCISE_ENV
    old = os.environ.get(CONCISE_ENV)
    if _run_concise():
        os.environ[CONCISE_ENV] = "1"
    else:
        os.environ.pop(CONCISE_ENV, None)
    try:
        _cmd_run(args)
    finally:
        if old is None:
            os.environ.pop(CONCISE_ENV, None)
        else:
            os.environ[CONCISE_ENV] = old


def _cannot_write_decisions(path, e: OSError) -> str:
    """One line for a sends.json that cannot be saved (a read-only
    inputs/<acct>/, a full disk): it was a PermissionError traceback on
    sends.json.part (re-audit A2-1416)."""
    return (f"cannot write {path}: {e.strerror or e} — nothing was "
            f"saved; make {Path(path).parent}/ writable and re-run.")


def cmd_crypto_sends(args: argparse.Namespace) -> None:
    """`tjs crypto-sends`: every outgoing crypto transfer that did
    not arrive in another of your crypto accounts, with your decision (self /
    gift / payment), its fair value and the ready .tt line; --set
    records a decision, --write regenerates the account's crypto_sends.tt
    (inputs/<acct>/, or with shared exports work/crypto_sends/<acct>/)."""
    from taxjson.lib import crypto_sends as CS
    root = Path(args.dir).resolve()
    cfg = load_config(root)
    accts = CS.crypto_accounts(cfg)
    if not accts:
        _die("no crypto account in taxjson.toml",
             "A crypto account is an [accounts.X] with `crypto = true`.")
    acct = (args.account or "").strip() or None
    if acct and acct not in accts:
        _die(f"{acct!r} is not a crypto account — crypto accounts: "
             f"{', '.join(accts)}.")
    sets = list(getattr(args, "set", None) or [])
    unsets = list(getattr(args, "unset", None) or [])
    if (sets or unsets or args.write) and not acct:
        if len(accts) != 1:
            _die("--set/--unset/--write need an account",
                 f"`tjs crypto-sends <{'|'.join(accts)}> --set ID="
                 f"{'|'.join(_send_decisions(_country(cfg.get('settings'))))}`")
        acct = accts[0]
    if (args.note is not None or args.price is not None
            or getattr(args, "unpair", False)) and not sets:
        _die("--note/--price/--unpair only apply with --set ID=DECISION.")
    if args.json and (sets or unsets or args.write):
        _die("--json applies to the listing only.")
    if sets and unsets:
        _die("--set and --unset go in separate commands.")
    cache = root / "work"
    if not any((cache / f"{a}_{b}_transfers.json").exists()
               for a in accts for b in ("kraken", "coinbase")):
        _die("no crypto transfer evidence in work/",
             "Run `tjs run` first (the parse keeps withdrawals/sends "
             "in work/<acct>_<exchange>_transfers.json).")
    if unsets:
        # Removing a saved decision needs no current evidence (A2-1163).
        from taxjson.lib.crypto_sends import MANIFEST_NAME as _MN
        _man = _PL.inputs_dir(root) / acct / _MN
        try:
            for sid in unsets:
                sid = sid.strip()
                if not CS.clear_decision(_man, sid):
                    _die(f"no saved decision for {sid!r} in "
                         f"{_sends_json_shown(root, acct)}.")
                if sid.startswith(CS.ARRIVAL_PREFIX):
                    _out.put(f"removed: {sid} (back to the default: the same "
                             f"coin as a `self` send is those coins returning)")
                    continue
                _out.put(f"removed: {sid} (undecided again — `tjs run` "
                         f"asks, or `--set {sid}=...`)")
        except ValueError as e:
            _die(str(e))
        except OSError as e:
            _die_input(_cannot_write_decisions(_man, e))
        _out.put(f"Regenerate the .tt lines: `tjs crypto-sends {acct} "
                 f"--write` (or just `tjs run`).")
        return
    files = _crypto_broker_files(root, cfg)
    # Evidence parsed before an export changed: a send that has since
    # arrived would still be listed as unmatched, and a decision saved
    # for it is then dropped by the next run (re-audit A2-1010).
    stale = CS.stale_evidence(cache, files, skip=_transfers_accounts(cfg))
    if stale and (sets or args.write):
        _die(f"the transfer evidence is not current for "
             f"{'; '.join(stale)}",
             "Run `tjs run` first, then decide (a send may have "
             "arrived since).")
    if stale:
        _view_msg("warning", f"the transfer evidence is not current for "
                  f"{'; '.join(stale)}",
                  "Run `tjs run` before deciding (a send may have "
                  "arrived since).")
    try:
        if sets:
            _crypto_sends_set(args, root, cfg, acct, sets, files)
            if not args.write:
                _out.put(f"Regenerate the .tt lines: `tjs crypto-sends "
                         f"{acct} --write` (or just `tjs run`).")
                return
        _price_fn = CS.yahoo_usd_price(root)
        report = CS.build_report(root, cfg, files, _price_fn, want=acct,
                                 exports_only=True)
        for w in getattr(_price_fn, "attention", ()):
            _view_msg("warning", f"crypto id: {w}")
        if args.write:
            status, dups = _crypto_sends_tt(root, acct, report)
            n = len(CS.tt_entries(report["accounts"][acct])[0])
            _out.put(f"{_sends_tt_shown(root, acct)}: {status} "
                     f"({n} BUYSELL line(s)); `tjs run` books it.")
            for w in _dup_warning(acct, dups, root):
                _view_msg("warning", w)
            return
    except ValueError as e:
        _die(str(e))
    if args.json:
        _json_out(report, schema="crypto-sends")
        return
    _print_crypto_sends(root, report, _details(args))


def _crypto_sends_set(args: argparse.Namespace, root: Path, cfg: Dict[str, Any],
                      acct: Optional[str], sets: List[Any],
                      files: Dict[str, List[Tuple[str, Path]]]) -> None:
    """crypto-sends --set: each ID=DECISION checked against the
    account's evidence (a stablecoin arrival, the network fee hidden in a
    send that arrived short, a send, a send paired with an arrival) and
    saved to its sends.json."""
    from taxjson.lib import crypto_sends as CS
    light = CS.build_report(root, cfg, files, None, want=acct,
                            with_pool=False, exports_only=True)
    adoc = light["accounts"][acct]
    by_id = {s["id"]: s for s in adoc["sends"]}
    fee_ids = {f["id"]: f for f in adoc["network_fees"]}
    for item in sets:
        if "=" not in item:
            # A usage error: exit 2 (backlog TJ-0241).
            _die_input(f"--set expects ID=DECISION, got "
                       f"{item!r}.")
        sid, dec = (x.strip() for x in item.rsplit("=", 1))
        dec = dec.lower()
        if sid.startswith(CS.ARRIVAL_PREFIX):
            # What a stablecoin arrival is, for the USD pool
            # (backlog TJ-0652).
            if light["base_currency"] == "USD":
                _die(f"{sid}: a project in US dollars keeps no USD "
                     f"pool, so an arrival takes no decision.")
            if sid not in adoc["arrival_ids"]:
                _die(f"no stablecoin arrival {sid!r} that pairs with "
                     f"no send in account {acct!r}",
                     f"`tjs crypto-sends {acct}` names those taken "
                     f"as coins returning.")
            if dec not in CS.ARRIVAL_DECISIONS:
                _die(f"decision {dec!r} for {sid} — expected one of "
                     f"{', '.join(CS.ARRIVAL_DECISIONS)}.")
            if args.price is not None or args.unpair:
                _die("--price and --unpair apply to a send, not an "
                     "arrival.")
            try:
                CS.record_arrival(Path(adoc["manifest"]), sid, dec,
                                  note=args.note)
            except OSError as e:
                _die_input(_cannot_write_decisions(adoc["manifest"], e))
            _out.put(f"saved: {sid} = {dec}  "
                     f"({_sends_json_shown(root, acct)})")
            continue
        if sid in fee_ids:
            # The network fee hidden in a send that arrived
            # short: a sale at fair value; the owner can only
            # price it (re-audit A2-0240).
            if dec != CS.FEE_DECISION or args.price is None:
                _die(f"{sid} is the network fee hidden in a send "
                     f"that arrived short — a sale at fair value",
                     f"Give its price: `--set {sid}="
                     f"{CS.FEE_DECISION} --price P`.")
            try:
                CS.record_decision(Path(adoc["manifest"]), sid, dec,
                                   note=args.note, price=args.price,
                                   summary=fee_ids[sid]["summary"],
                                   price_currency=light[
                                       "base_currency"])
            except OSError as e:
                _die_input(_cannot_write_decisions(adoc["manifest"], e))
            _out.put(f"saved: {sid} priced at {args.price:g} "
                     f"{light['base_currency']} per "
                     f"{fee_ids[sid]['symbol']}  "
                     f"({_sends_json_shown(root, acct)})")
            continue
        if dec not in CS.DECISIONS:
            _die(f"decision {dec!r} for {sid} — expected one of "
                 f"{', '.join(_send_decisions(_country(cfg.get('settings'))))}.")
        _gift_no = command_country_problem(
            "crypto-sends", _country(cfg.get("settings")), dec)
        if _gift_no:
            # lib/country.COMMAND_COUNTRY["crypto-sends:gift"]
            _die(f"{_gift_no}",
                 "taxjson cannot book a US gift yet. `self` keeps "
                 "the coins and their basis in your books, so a "
                 "later sale uses them (docs/limits.md); if you "
                 "were paid, `payment`.")
        summary = by_id[sid]["summary"] if sid in by_id else ""
        unpair = None
        if sid not in by_id:
            paired = CS.matched_send(root, cfg, acct, sid, files)
            if paired is None:
                _die(f"no send {sid!r} in account {acct!r}",
                     f"`tjs crypto-sends {acct}` lists the "
                     f"ids.")
            if dec in CS.DISPOSING and not args.unpair:
                # The pairing calls it your own move: a gift or
                # payment needs the owner to say the arrival is
                # unrelated (re-audit A2-0004).
                _die(f"{sid} is paired with an arrival — booked as "
                     f"your own move",
                     f"The arrival: {paired}.",
                     f"If that arrival is unrelated and the coins "
                     f"left your ownership: `--set {sid}={dec} "
                     f"--unpair`.")
            summary = paired.split(" arrived as ")[0]
        if args.unpair:
            if dec not in CS.DISPOSING:
                _die("--unpair applies to a gift or payment.")
            unpair = True
        try:
            CS.record_decision(Path(adoc["manifest"]), sid, dec,
                               note=args.note, price=args.price,
                               summary=summary, unpair=unpair,
                               price_currency=light[
                                   "base_currency"])
        except ValueError as e:
            _die(str(e))
        except OSError as e:
            _die_input(_cannot_write_decisions(adoc["manifest"], e))
        _out.put(f"saved: {sid} = {dec}"
                 f"{' (unpaired)' if unpair else ''}  "
                 f"({_sends_json_shown(root, acct)})")


def _print_crypto_sends(root: Path, report: Dict[str, Any],
                        details: bool = False) -> None:
    """The crypto-sends listing in the house layout (docs/output-style.md):
    a title, one section per crypto account, each send's id and decision
    with its facts aligned under it (the ready .tt line never wrapped),
    and a last line saying what to do."""
    settings = load_settings(root, missing_ok=True)
    from taxjson.lib import crypto_sends as CS
    from taxjson.lib.out import Doc, Verbatim
    base = report["base_currency"]
    _usa = _country(settings) == "usa"
    # Under ledger v2 fx-cash reads a stablecoin send as US dollars going
    # out and owns its currency gain once the move is declared; the
    # listing gives that declaration, never "add it by hand" (backlog
    # TJ-0457: both together counted the gain twice).
    _v2 = str(settings.fx_cash_ledger or "v1"
              ).strip().lower() == "v2"
    doc = Doc("CRYPTO SENDS — outgoing transfers that did not arrive in "
              "another of your crypto accounts")
    if _usa:
        # A US donor's gift is not a disposition (the recipient takes
        # over the basis); `gift` is refused (COMMAND_COUNTRY).
        doc.para("A move to your own wallet is not a sale (self); a "
                 "payment is a sale at fair market value. A gift is not a "
                 "sale for a US donor, and taxjson cannot book it yet: self "
                 "keeps the coins and their basis (docs/limits.md)."
                 if details else
                 "self: a move to your own wallet, no sale; payment: a sale "
                 "at fair market value. A gift cannot be booked yet: self "
                 "keeps the coins and their basis.")
    else:
        doc.para("A move to your own wallet is not a sale (self); a gift "
                 "or a payment is a disposition at fair market value."
                 if details else
                 "self: a move to your own wallet, no sale; gift or "
                 "payment: a disposition at fair market value.")
    fx_by_year: Dict[str, float] = {}
    last = None
    for acct, adoc in report["accounts"].items():
        sends = adoc["sends"]
        doc.section(f"{acct} — {len(sends)} unmatched send(s), "
                    f"{adoc['undecided']} undecided")
        if details or adoc["matched"]:
            doc.para(f"{adoc['matched']} matched to an arrival (self-"
                     f"custody moves, `tjs transfers`).", indent="  ")
        _short = adoc.get("network_fees") or []
        if _short:
            # R1-26: the coins lost in transit paid the network fee — a
            # disposition at fair value, written to crypto_sends.tt.
            doc.para(f"{len(_short)} matched send(s) arrived SHORT — the "
                     f"difference is the network fee paid in the coin, a "
                     f"sale at fair value booked in {CS.TT_NAME}:",
                     indent="  ")
            for _s in _short:
                _fv = _s["fair_value"]
                _val = (f"{M.shown(_fv['value'], 2):,.2f} {base}  [{_fv['source']}]"
                        if _fv else "UNPRICED (lookup failed or offline)")
                doc.item(f"{_s['id']}: {_s['summary']}, "
                         f"{CS.fmt_qty(_s['quantity'])} {_s['symbol']} "
                         f"short = {_val}", indent="  ")
        _crypto_sends_facts(sends, doc, base, _v2, Verbatim, fx_by_year, report)
        entries, unpriced = CS.tt_entries(adoc)
        tt = Path(adoc["tt_file"])
        want = CS.render_tt(acct, entries, report["country"])
        have = CS.read_tt(tt)              # a BOM re-save (A2-1405)
        doc.blank()
        if unpriced:
            doc.message("warning", f"NOT BOOKED — no fair value for "
                        f"{', '.join(e['id'] for e in unpriced)}",
                        details=[f"Give it with `--set ID="
                                 f"{_disposing_word(report['country']).replace('/', '|')}"
                                 f" --price P` (a `-fee` id: `--set "
                                 f"ID=fee --price P`)."])
        if want == have:
            last = (f"{_sends_tt_shown(root, acct)}: up to date "
                    f"({len(entries)} line(s))." if have else
                    f"No {_disposing_word(report['country'])} or network "
                    f"fee needs a sale line.")
        else:
            last = (f"{_sends_tt_shown(root, acct)}: OUT OF DATE — run "
                    f"`tjs crypto-sends {acct} --write` (or `tjs "
                    f"run`).")
        doc.para(last)
        for w in _dup_warning(acct, CS.duplicate_lines(
                _PL.inputs_dir(root) / acct, entries), root):
            doc.message("warning", w)
        for w in _crypto_sends_problems(acct, adoc):
            doc.message("warning", w)
        for sid in adoc["orphans"]:
            doc.message("note", f"sends.json has a decision for {sid}, "
                        f"which is no longer a send in the exports"
                        + ("" if details else ": it is ignored"),
                        details=(["Inputs or local_timezone changed? It "
                                  "is ignored."] if details else []))
        if adoc["repeated_hour"]:
            # Nothing maps them (backlog TJ-1145): saved keys stay as they
            # are, and the owner checks them.
            doc.message("note", f"{len(adoc['repeated_hour'])} saved "
                        f"decision(s) are of a send in the hour the clocks "
                        f"repeat: check them if saved before v0.39.0",
                        details=adoc["repeated_hour"] + [
                            "That release changed the time such a send's id "
                            "holds (01:50 EDT now reads 01:25), so a decision "
                            "saved earlier may now be on another send of the "
                            "same coin and quantity, or on none. Compare each "
                            "with `tjs crypto-sends --details`, then --set "
                            "the right one and --unset the other."])
        for aid in adoc["arrival_orphans"]:
            doc.message("note", f"sends.json has a decision for arrival "
                        f"{aid}, which is not a stablecoin arrival pairing "
                        f"with no send: it is ignored")
        for a in adoc["arrivals"]:
            if a["unused"]:
                doc.message("note", f"arrival {a['id']} is saved as `return`, "
                            f"but no `self` send of {a['symbol']} had left "
                            f"before it: it came in as new money")
        if adoc["undecided"]:
            last = (f"Decide: tjs crypto-sends {acct} --set "
                    f"ID={'self|payment' if _usa else 'self|gift|payment'}"
                    f" [--note TEXT]   (then --write)")
            doc.line(last)
    _crypto_sends_fx_gain(fx_by_year, doc, details, base, _v2)
    _crypto_sends_pool_notes(report, doc)
    doc.print()


def _crypto_sends_facts(sends: Any, doc: Any, base: Any, _v2: bool, Verbatim: Any,
                        fx_by_year: Dict[str, float], report: Dict[str, Any]) -> None:
    """tjs crypto-sends: each unmatched send of an account, its id and
    decision on a line and its facts aligned under it (what left, its
    fair value, a stablecoin's currency gain and fx-cash v2 line, the
    ready .tt line); a disposed stablecoin's allowed gain is added to
    fx_by_year."""
    from taxjson.lib import crypto_sends as CS
    for e in sends:
        dec = (e["decision"] or "PENDING").upper()
        ref = f"   ref {e['ref']}" if e["ref"] else ""
        unp = "   (unpaired by you)" if e.get("unpaired") else ""
        doc.blank().line(f"  {e['id']}   {dec}{ref}{unp}")
        fee = ("" if not e["fee_booked"] else
               f" (plus a {CS.fmt_qty(e['fee_booked'])} {e['symbol']} "
               f"withdrawal fee — cash, not a sale)" if e["stable"] else
               f" ({CS.fmt_qty(e['fee_booked'])} {e['symbol']} network "
               f"fee already booked from the ledger)")
        pairs = [("send", f"{e['summary']}{fee}")]
        fv = e["fair_value"]
        if fv:
            pairs.append(("fair value",
                          f"{CS.fmt_price(fv['price'])} {base}/"
                          f"{e['symbol']} = {M.shown(fv['value'], 2):,.2f} {base}  "
                          f"[{fv['source']}]"))
        else:
            pairs.append(("fair value",
                          f"UNPRICED (lookup failed or offline) — "
                          f"`--set {e['id']}=... --price P` gives it"))
        if e["stable"]:
            fx = e["fx"]
            if base == "USD":
                pairs.append(("stablecoin", "US-dollar cash: no sale "
                              "line and no currency gain in a USD "
                              "project"))
            elif fx:
                tag = ("FX gain" if e["decision"] in CS.DISPOSING
                       else "FX gain if gift/payment")
                pairs.append(("stablecoin", "US-dollar cash in the "
                              "books: no sale line"))
                pairs.append((tag, f"value {M.shown(fx['value'], 2):,.2f} - ACB "
                              f"{M.shown(fx['acb'], 2):,.2f} (USD pool average "
                              f"cost) = {M.shown(fx['gain'], 2):+,.2f} {base}"))
                if fx.get("superficial"):
                    pairs.append(("superficial", "that loss is likely "
                                  "SUPERFICIAL (s.54): USD/stablecoins "
                                  "acquired within 30 days and still "
                                  "held — denied"))
                if fx.get("overdrawn"):
                    pairs.append(("pool", "the pool was short: the "
                                  "ledgers do not show where part of "
                                  "this USD came from"))
                if _v2 and e["decision"]:
                    # The line fx-cash ledger v2 asks for, in a .tt
                    # of the account: it then counts this gain.
                    how = (f"proceeds={M.shown(fx['value'], 2):.2f}"
                           if e["decision"] in CS.DISPOSING
                           else "kept")
                    pairs.append(("fx-cash v2", Verbatim(
                        f"CASHMOVE {e['date']} USD "
                        f"-{M.shown(fx['units'], 2):.2f} {how}")))
                if e["decision"] in CS.DISPOSING:
                    y = e["date"][:4]
                    allowed = 0.0 if (fx["gain"] < 0 and
                                      fx.get("superficial")) \
                        else fx["gain"]
                    fx_by_year[y] = fx_by_year.get(y, 0.0) + allowed
            else:
                pairs.append(("stablecoin", "US-dollar cash in the "
                              "books: no sale line (FX gain "
                              "unavailable: no USD rate)"))
        elif e["tt"]:
            lead = ".tt" if e["decision"] in CS.DISPOSING else \
                f".tt if {_disposing_word(report['country'])}"
            pairs.append((lead, Verbatim(e["tt"])))
        if e["note"]:
            pairs.append(("your note", e["note"]))
        if e.get("refused"):
            pairs.append(("REFUSED", e["refused"]))
        doc.kv(pairs, indent="    ")


def _crypto_sends_fx_gain(fx_by_year: Dict[str, float], doc: Any, details: bool,
                          base: Any, _v2: bool) -> None:
    """tjs crypto-sends: the stablecoin gifts' and payments' currency
    gain by year, and what to do with it under ledger v1 or v2."""
    if fx_by_year:
        doc.section("STABLECOIN GIFTS AND PAYMENTS — currency gain")
        if not details:
            doc.line("Superficial losses are excluded.")
        doc.kv([(y, f"{M.shown(g, 2):+,.2f} {base}")
                for y, g in sorted(fx_by_year.items())], indent="  ")
        # Ledger v1 has no input for it (re-audit A2-0591): the amount
        # is added by hand to the year's s.39(1.1) currency total. Ledger
        # v2 reads the send and counts it once declared (TJ-0457).
        if _v2 and not details:
            from taxjson.lib.out import act as _act
            doc.line(_act("fx-cash v2 counts it once each `fx-cash v2` "
                          "line is in a .tt", "tjs crypto-sends --details"))
        elif _v2:
            doc.para("Superficial losses are excluded. Stablecoins are "
                     "US-dollar cash in these books, so this is a "
                     "foreign-currency gain: ITA s.39(1.1) taxes only the "
                     "year's NET currency gain beyond $200. With ledger v2 "
                     "(fx_cash_ledger = \"v2\") `tjs fx-cash` reads "
                     "each of these sends as US dollars going out and "
                     "asks what it was: put its `fx-cash v2` line above "
                     "(proceeds= its value; kept for a send to your own "
                     "wallet) in a .tt file of the account, and fx-cash "
                     "counts the gain in its net, against the pool of all "
                     "your taxable accounts' US dollars. Do NOT add this "
                     "total too: it comes from the crypto exports' pool "
                     "alone, a check on fx-cash's figure. fx-cash does "
                     "not apply the superficial-loss rule: a loss flagged "
                     "SUPERFICIAL above is excluded here, not there.",
                     indent="  ")
        elif not details:
            from taxjson.lib.out import act as _act
            doc.line(_act("add it by hand to the year's net FX gain (not "
                          "in fx-cash)", "tjs crypto-sends --details"))
        else:
            doc.para("Superficial losses are excluded. Stablecoins are "
                     "US-dollar cash in these books, so this is a "
                     "foreign-currency gain: ITA s.39(1.1) taxes only the "
                     "year's NET currency gain beyond $200. It is NOT in "
                     "any taxjson figure (`tjs fx-cash` does not read "
                     "it under the default ledger v1): add it by hand to "
                     "the year's net foreign-exchange gain or loss (the "
                     "fx-cash net before the $200) and apply the $200 to "
                     "the total. Ledger v2 (fx_cash_ledger = \"v2\") "
                     "reads these sends and counts the gain itself once "
                     "each is declared.", indent="  ")


def _crypto_sends_pool_notes(report: Dict[str, Any], doc: Any) -> None:
    """tjs crypto-sends: the USD/stablecoin pool's notes (the times it
    went short, arrivals taken as coins coming back, arrivals decided
    `new`)."""
    pool = report.get("pool")
    if pool and pool.get("overdrafts"):
        doc.blank()
        doc.message("note", f"the USD/stablecoin pool went short "
                    f"{pool['overdrafts']} time(s)",
                    details=["The ledgers do not show where that USD came "
                             "from (an earlier year's export?); the "
                             "shortfall is valued at the day's rate (no "
                             "gain)."])
    if pool and pool.get("returns"):
        # An arrival of a coin a `self` send parked is taken as those
        # coins coming back, not bought: nothing in the exports says so
        # (backlog TJ-0652).
        doc.blank()
        doc.message("note", f"{len(pool['returns'])} stablecoin "
                    f"arrival(s) taken as coins sent to your own wallet "
                    f"coming back (no new cost in the pool)",
                    details=[f"{r['date']} {fmt_qty(r['units'])} "
                             f"{r['symbol']} into {r['account']}: {r['id']}"
                             for r in pool["returns"]]
                    + ["Only the same coin is taken back. If one was new "
                       "money (the coins sent are still in your wallet): "
                       "`tjs crypto-sends ACCOUNT --set ID=new`, its cost "
                       "then joins the pool. `--set ID=return` says it is "
                       "those coins, as now."])
    fresh = [a for adoc in report["accounts"].values()
             for a in adoc["arrivals"] if a["decision"] == "new"]
    if pool and fresh:
        doc.blank()
        doc.message("note", f"{len(fresh)} stablecoin arrival(s) decided "
                    f"`new`: new money, at the day's rate in the pool",
                    details=[f"{a['date']} {fmt_qty(a['units'])} "
                             f"{a['symbol']}: {a['id']}" for a in fresh])


def cmd_compare(args: argparse.Namespace) -> None:
    """`tjs compare [NAME] [--save NAME] [--delete NAME] [--list]`:
    reports/ against a snapshot of an earlier run (lib/compare): the
    run before (`prev`, rotated by every full run), a saved one, or
    the reports as filed (`filed`). Exit 1 when a report differs or an
    account figure moved."""
    from taxjson.lib import compare as _cmp
    root = Path(args.dir).resolve()
    cfg = load_config(root)
    _json = getattr(args, "json", False)
    try:
        if args.save:
            doc = _cmp.save(root, cfg, args.save)
            if _json:
                _json_out({"saved": args.save, "taken": doc["taken"],
                           "reports": len(doc["reports"])},
                          schema="compare --save")
            else:
                _out.put(f"Saved {len(doc['reports'])} report file(s) as "
                         f"{args.save} (work/compare/{args.save}/); "
                         f"`tjs compare {args.save}` diffs against it.")
            return
        if args.delete:
            _cmp.delete(root, args.delete)
            if _json:
                _json_out({"deleted": args.delete}, schema="compare --delete")
            else:
                _out.put(f"Deleted snapshot {args.delete}.")
            return
        if args.list:
            items = _cmp.list_snapshots(root, cfg)
            if _json:
                _json_out({"snapshots": items}, schema="compare --list")
            else:
                # A live exact Doc prints each line as print() did, the moment
                # it is added.
                d = _out.Doc(exact=True, width_=0, live=True)
                for ln in _cmp.render_list(items):
                    d.line(ln)
            return
        if not (root / "reports").is_dir():
            _die_input("no reports/ — run `tjs run` first")
        doc = _cmp.compare(root, cfg, args.name or _cmp.PREV,
                           details=_details(args) or _json)
    except _cmp.CompareError as e:
        # A refusal (nothing to compare with) is exit 2; exit 1 is the
        # finding, a report that differs (cli_diag's convention).
        _die_input(str(e))
    if _json:
        _json_out(doc, schema="compare")
    else:
        # A live exact Doc prints each line as print() did, the moment
        # it is added.
        d = _out.Doc(exact=True, width_=0, live=True)
        for ln in _cmp.render(doc, details=_details(args)):
            d.line(ln)
    if doc["different"]:
        raise SystemExit(1)


def cmd_snapshot(args: argparse.Namespace) -> None:
    """`tjs snapshot`: the whole project copied to
    snapshot/<year>.<timestamp>/, laid out as it is, for `diff -r` and
    `tjs compare` (lib/compare.take_full)."""
    from taxjson.lib import compare as _cmp
    from taxjson.lib import out as _out
    root = Path(args.dir).resolve()
    cfg = load_config(root)
    try:
        doc = _cmp.take_full(root, cfg)
    except _cmp.CompareError as e:
        _die_input(str(e))
    if doc["partial_accounts"]:
        _out.emit("warning", f"reports/ hold a `tjs run --account "
                  f"{', '.join(doc['partial_accounts'])}` over the last "
                  f"full run's; the snapshot records that")
    if doc["links_leaving"]:
        _out.emit("warning", "kept as links, what they lead to is not in "
                  "the snapshot: " + ", ".join(doc["links_leaving"]))
    if getattr(args, "json", False):
        _json_out(doc, schema="snapshot")
        return
    _mb = doc["bytes"] / (1024 * 1024)
    _out.put(f"Saved snapshot {doc['name']}: {doc['files']} file(s), "
             f"{_mb:.1f} MB, in {doc['folder']}")
    d = _out.Doc(exact=True, width_=0, live=True)
    for ln in _out.kv_lines([
            ("Compare", f"`tjs compare {doc['name']}`"),
            ("Diff", f"`diff -r reports {doc['reports_folder'].rstrip('/')}`")]):
        d.line(ln)


def cmd_clean(args: argparse.Namespace) -> None:
    """`tjs clean`: what `tjs run` generated removed (work/, reports/, the
    export it wrote into exports_dir), what the inputs cannot rebuild kept
    (lib/clean); in the folder holding the year folders, every year's.
    Each run lock is held throughout: refused, nothing removed, while a
    run or close-year holds one."""
    from taxjson.lib import clean as _cl
    from taxjson.pipeline.config import _no_project_message
    base = Path(args.dir).resolve()
    if not _PL.has_config(base) and not _PL.year_dirs(base):
        _die(*_no_project_message(base, "clean"))
    dry = bool(getattr(args, "dry_run", False))
    try:
        roots = _cl.projects(base)
        with _cl.held(roots, dry_run=dry) as made:
            try:
                pl = _cl.plan(base, roots, made)
            except OSError as e:
                _die_input(f"{_PL.shown(Path(e.filename or base), base)} cannot be "
                           f"read ({e.strerror or e}): nothing was removed")
            if not dry:
                try:
                    _cl.remove(pl)
                except OSError as e:
                    _die(f"{_PL.shown(Path(e.filename or base), base)} could not be "
                         f"removed ({e.strerror or e})",
                         "What came before it is gone, the reports' .sum files "
                         "and the run's record of what it read first: the "
                         "filing commands refuse what is left and the views "
                         "warn that the books are not the clean result. Fix "
                         "it, then run `tjs clean` again.")
                except KeyboardInterrupt:
                    _out.fail("stopped part-way: the reports' .sum files and the "
                              "run's record of what it read went first, so the "
                              "filing commands refuse what is left and the views "
                              "warn", prog=f"{_PROG} clean",
                              details=("Run `tjs clean` again to finish.",), code=130)
    except _cl.Busy as e:
        where = _PL.shown(Path(str(e)), base)
        _die("another `tjs run` (or `tjs close-year`) is in progress in "
             + ("this project" if where == "." else f"{where}/")
             + ": nothing was removed — wait for it to finish (or stop it), "
               "then re-run")
    except _cl.CleanError as e:
        _die_input(str(e))
    _clean_report(args, pl, dry)


# The items taxjson did not write that `tjs clean` names without --details.
_FOREIGN_SHOWN = 8


def _clean_report(args: argparse.Namespace, pl: Any, dry: bool) -> None:
    """What `tjs clean` removed (or would), a line per folder; what it kept
    or left alone (Info), a folder it would not clean (Warning)."""
    from taxjson.lib import out as _out
    details = _details(args)
    for shown, why in pl.skipped:
        _out.warn(f"{shown} not cleaned: {why}")
    if getattr(args, "json", False):
        _json_out(pl.to_json(dry), schema="clean")
        return
    gone = pl.removed
    if not gone:
        _out.note("nothing to remove: no file `tjs run` wrote is here"
                  + (" (a dry run)" if dry else ""), file=sys.stdout)
    else:
        n = pl.files
        _out.put(f"Would remove {n} file(s) `tjs run` wrote (a dry run: nothing "
                 f"was removed)" if dry else
                 f"Removed {n} file(s) `tjs run` wrote: the next `tjs run` "
                 f"builds them again from the inputs")
        rows = []
        for f in gone:
            v = f"{f.files} file(s)" if f.files else "an empty folder"
            rows.append((f.shown, v + (f", {f.what}" if f.what else "")))
        _out.put(_out.kv_lines(rows, indent="  "))
        if details:
            _out.put([f"  - {_PL.shown(p, pl.base)}"
                      + (f"/ ({n} file(s))" if d else "")
                      for f in gone for p, n, d in f.entries])
    sys.stdout.flush()
    if pl.foreign:
        shown = pl.foreign if details else pl.foreign[:_FOREIGN_SHOWN]
        _out.note(f"left {len(pl.foreign)} item(s) taxjson did not write: "
                  + ", ".join(shown)
                  + (f" +{len(pl.foreign) - len(shown)} more (`tjs clean "
                     f"--details` lists them)" if len(shown) < len(pl.foreign) else "")
                  + "; remove them yourself if you do not need them",
                  whole=True)
    if pl.kept:
        _out.note(f"left {len(pl.kept)} item(s) of taxjson's alone"
                  + ("" if details else " (`tjs clean --details` says why)")
                  + ": " + ", ".join(p for p, _w in pl.kept[:3])
                  + (f" +{len(pl.kept) - 3} more" if len(pl.kept) > 3 else ""),
                  details=[f"{p}: {w}" for p, w in pl.kept])
    for _where, year in pl.filed:
        _out.note(f"{year} is filed: run `tjs run` before `tjs check-filed`; "
                  f"filed/ stays, the last drift verdict "
                  f"{'would go' if dry else 'went'} with work/")
    if pl.stray:
        _out.note("left alone, as no command writes them now (remove them "
                  "yourself if you do not need them): "
                  + ", ".join(p for p, _w in pl.stray),
                  details=[f"{p}: {w}" for p, w in pl.stray] if details else ())
    if dry and gone:
        _out.put("Run `tjs clean` without --dry-run to remove them.")


# What `tjs opening` writes into a .tt line from a positions
# report's cells (security review M2): anything else is skipped, named.
_OPENING_SYMBOL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]*\Z")
_OPENING_CURRENCY_RE = re.compile(r"[A-Z]{3}\Z")


def _opening_iso_day(v: str) -> bool:
    if not re.match(r"\d{4}-\d{2}-\d{2}\Z", v or ""):
        return False
    try:
        datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _opening_padded_day(v: str) -> Optional[str]:
    """`2025-1-1` written as a .tt line takes it (2025-01-01); None when
    `v` is no real day in any spelling (2025-02-30, 1/1/2025)."""
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", (v or "").strip())
    if not m:
        return None
    day = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return day if _opening_iso_day(day) else None


def _opening_lines(report, *, country: str, base_currency: str,
                   snapshot: str, mult: Optional[Dict[str, float]] = None):
    """(OPENING .tt lines, [skipped (symbol, reason)], [notes]) for one
    positions report's rows (lib/positions_reports). Long positions
    with a stated cost only: a short (a written option, a short sale)
    depends on its write; a row without a cost is never written at a
    market value (CA-OPEN-01). A US project needs each lot's own
    purchase date and a US-dollar cost (US-OPEN-01/02).

    An option whose contract size is not 100 (a mini) is written with
    it (`x<size>`) only when the project's ticker.map declares that size
    (`mult`: its MULT lines, {SYMBOL or root: size}): the exercise and
    assignment rows of the option carry no size of their own, and its
    premium is spread over the units each contract delivers. Without
    the line it is skipped, naming the line to add (backlog TJ-0963)."""
    from decimal import Decimal
    from taxjson.lib.option_symbols import is_option_symbol
    from taxjson.lib.positions_reports import COST_LOTS
    lines: List[str] = []
    skipped: List[Tuple[str, str]] = []
    notes: List[str] = []
    usa = country == "usa"
    lots_kind = False
    for r in sorted(report.rows, key=lambda x: (x.symbol, x.lot_date)):
        # Written raw into the .tt line: a newline or a space in the
        # symbol would book whatever follows as its own line (security
        # review M2). Named escaped in the skip list.
        safe_sym = bool(_OPENING_SYMBOL_RE.match(r.symbol or ""))
        sym = r.symbol if safe_sym else ascii(r.symbol)
        q = float(r.quantity or 0.0)
        if abs(q) < 1e-12:
            continue
        if r.asset_type in ("cash",):
            continue
        if q < 0:
            skipped.append((sym, "a short position (its premium or "
                                 "proceeds depend on the write: enter "
                                 "the write as a BUYSELL line)"))
            continue
        if r.asset_type == "future" \
                or (r.symbol or "").startswith(FUTURES_PREFIXES):
            skipped.append((sym, "a futures contract (booked on its own "
                                 "basis: enter the opening trade as a "
                                 "BUYSELL line)"))
            continue
        # A holdings file's cost in the project's base currency at the
        # acquisition dates (taxjson-export's base_total_cost) is the
        # tax cost itself: kept as stated, never the native cost
        # converted again at the snapshot day's rate (backlog TJ-0990).
        base_known = (getattr(r, "base_cost", None) is not None
                      and str(getattr(r, "base_currency", "") or "")
                      .upper() == base_currency.upper())
        if not base_known and (r.cost is None or not r.cost_kind):
            skipped.append((sym, "the report states no cost for it (a "
                                 "market value is never a cost) — write "
                                 "its line by hand from a statement "
                                 "with book cost"))
            continue
        cost = float(r.base_cost if base_known else r.cost)
        if cost < 0:
            skipped.append((sym, f"a negative cost {M.shown(cost, 2):,.2f}"))
            continue
        cur = (base_currency.upper() if base_known
               else (r.cost_currency or r.currency or "").upper())
        if not cur:
            skipped.append((sym, "no currency for its cost"))
            continue
        if not _OPENING_CURRENCY_RE.match(cur):
            skipped.append((sym, f"its currency {ascii(cur)} is not a "
                                 f"3-letter code"))
            continue
        lot = r.lot_date or ""
        if lot and not _opening_iso_day(lot):
            skipped.append((sym, f"its lot date {ascii(lot)} is not a "
                                 f"YYYY-MM-DD date"))
            continue
        if usa:
            if not lot:
                skipped.append((sym, "no purchase date: a US opening is "
                                     "one line per lot with its own date "
                                     "(US-OPEN-01) — list the lots in a "
                                     "holdings TOML (`acquired = ...`) "
                                     "or write the lines by hand"))
                continue
            if cur != base_currency.upper():
                skipped.append((sym, f"its cost is in {cur}: a US lot's "
                                     f"basis is its {base_currency} cost "
                                     f"on its purchase date (US-OPEN-02)"))
                continue
        if lot and lot > snapshot:
            skipped.append((sym, f"its lot date {lot} is after the "
                                 f"snapshot date {snapshot}"))
            continue
        if not safe_sym:
            skipped.append((sym, "not a symbol a .tt line can carry "
                                 "(letters, digits and ._:/+- only)"))
            continue
        if r.cost_kind == COST_LOTS:
            lots_kind = True
        # The exact quantity, no exponent: a rounded one (8 significant
        # digits) would leave a residue at the closing sale.
        qtext = format(Decimal(repr(q)), "f")
        if "." in qtext:
            qtext = qtext.rstrip("0").rstrip(".")
        # An option's contract size other than 100 rides on the line
        # (`x<size>`, as a BUYSELL line's) once ticker.map declares it:
        # the units its exercise delivers, which spread its cost
        # (backlog TJ-0963).
        size = ""
        if is_option_symbol(sym) and r.multiplier \
                and float(r.multiplier) not in (0.0, 100.0):
            from taxjson.lib.tt_format import size_text
            from taxjson.lib.option_symbols import parse_option_underlying
            from taxjson.lib.markets import strip_listing_suffix
            m = float(r.multiplier)
            # The run's lookup (markets.option_contract_size): the
            # contract, the class share, the root, the underlying as
            # written (backlog TJ-1200).
            o_und = parse_option_underlying(sym) or ""
            o_share = strip_listing_suffix(o_und)
            said = next((float((mult or {})[k]) for k in (
                sym.upper(), o_share.upper(),
                o_und.split(".", 1)[0].upper(), o_und.upper())
                         if k and k in (mult or {})), None)
            if said is None or abs(said - m) > 1e-9:
                skipped.append((sym, f"a contract size of {size_text(m)} "
                                     f"(not 100): add `MULT "
                                     f"{o_share or sym} {size_text(m)}` to "
                                     f"ticker.map (each exercise or "
                                     f"assignment of it delivers "
                                     f"{size_text(m)} units a contract), "
                                     f"then run `tjs opening` again"))
                continue
            size = f" x{size_text(m)}"
        lines.append(f"OPENING {snapshot} {sym} {qtext} {cur} "
                     f"{M.shown(cost, 2):.2f}" + (f" {lot}" if lot else "") + size)
    if lots_kind and not usa:
        notes.append("the report's cost is the broker's LOT basis (each "
                     "lot at its own price, sales matched by the broker's "
                     "lot method); Canadian ACB is the average cost of "
                     "all identical shares (s.47). They differ when part "
                     "of a position was sold before the snapshot — check "
                     "those with `tjs sanity` once the books are "
                     "built.")
    return lines, skipped, notes


def cmd_opening(args: argparse.Namespace) -> None:
    """`tjs opening ACCOUNT FILE [--date D] [--dry-run] [--force]`:
    turn a broker's positions report (lib/positions_reports: an IB
    Activity Statement's Open Positions, an RBC Holdings Export, a
    `[[holding]]` TOML) into an opening balance —
    inputs/<ACCOUNT>/opening_<date>.tt with one OPENING line per long
    position (per lot with its date in a US project). The snapshot then
    replaces the account's earlier rows of those symbols
    (lib/opening; tax-logic CA-OPEN-01..03 / US-OPEN-01..03)."""
    from taxjson.lib.brokerages.base import shown_name
    from taxjson.lib.out import note as _note, warn as _warn, wrap as _wrap
    from taxjson.lib.brokerages.detect import kind_label
    from taxjson.lib.positions_reports import (PositionsReportError,
                                               read_positions)
    root = Path(args.dir).resolve()
    cfg = load_config(root)
    settings = settings_of(cfg)
    country = _country(settings)
    base = str(settings.base_currency or "").upper() \
        or _home_currency(settings)
    accounts = accounts_of(cfg)
    name = args.account
    if name not in accounts:
        _die(f"{name!r} is not an account in taxjson.toml",
             f"Accounts: {', '.join(sorted(accounts)) or '-'}.")
    if account_of(accounts, name).crypto:
        _die(f"account {name} is a crypto account",
             "An opening balance is for share accounts: enter the coins' "
             "purchases as BUYSELL lines.")
    path = Path(args.file).expanduser()
    if not path.is_file():
        path = _project_file(root, args.file) or path
    if not path.is_file():
        _die_input(f"{_mask_ids_in_path(str(path))} is not a file",
                   "A relative FILE is looked for from the shell's folder, "
                   "then from the project's.")
    try:
        report = read_positions(path)
    except PositionsReportError as e:
        _die_input(str(e))
    snapshot = _opening_snapshot(args, path, report)
    if args.date and report.as_of and args.date != report.as_of:
        _note(f"the report is as of {report.as_of}; the opening is dated "
              f"{args.date} as given (--date).", prog="tjs opening")
    _opening_broker_accounts(path, report)
    # The contract sizes ticker.map declares (MULT lines): a mini
    # option's opening needs its own (backlog TJ-0963).
    _mult: Dict[str, float] = {}
    _tmp = _PL.ticker_map_path(root)
    if _tmp.is_file():
        from taxjson.lib.ticker_map import read_side_rules
        try:
            _mult = dict(read_side_rules(_tmp).mult)
        except Exception:                           # noqa: BLE001
            _mult = {}
    lines, skipped, notes = _opening_lines(report, country=country,
                                           base_currency=base,
                                           snapshot=snapshot, mult=_mult)
    acct_dir, out = _opening_target(root, name, snapshot, lines)
    header = [
        f"# Opening balance of account {name} on {snapshot} "
        f"(`tjs opening`).",
        # A control character of the file name or an account cell (a
        # newline) would end the comment and book the rest as a line
        # (security review L3).
        re.sub(r"[\x00-\x1f\x7f]", "?",
               f"# From: {_mask_ids_in_path(shown_name(path))} — "
               f"{kind_label(report.kind)}"
               + (f", accounts {', '.join(report.accounts)}"
                  if report.accounts else "") + "."),
        "# An OPENING line sets a position and its cost; it is not a "
        "purchase (tax-logic",
        "# CA-OPEN-01 / US-OPEN-01). The account's rows of these symbols "
        "dated on or",
        "# before the snapshot day are left out of the books "
        "(CA-OPEN-03 / US-OPEN-03).",
        "#       snapshot    symbol  qty  currency  total-cost  "
        "[lot-date]",
    ]
    text = "\n".join(header + lines) + "\n"
    # Each line read back as the run reads it, before anything is
    # written: a file the next run refuses is never published (TJ-0317).
    from taxjson.lib.tt_format import parse_tt_line
    for _i, _ln in enumerate(lines, len(header) + 1):
        try:
            parse_tt_line(_ln, name, source=f"{out.name}:{_i}")
        except ValueError as e:
            _die(f"an OPENING line would not read back: {e}",
                 "Nothing written: fix that position's row in the "
                 "report (or leave it out), then `tjs opening` "
                 "again.")
    if getattr(args, "dry_run", False):
        sys.stdout.write(text)
    elif not lines:
        pass
    else:
        if out.exists() and not args.force:
            _die(f"{_PL.shown(out, root)} exists",
                 f"Pass --force to replace it (the old file is kept as "
                 f"{out.name}.bak, or the next free .bakN).")
        acct_dir.mkdir(parents=True, exist_ok=True)
        # A .tt file is the user's: written as every user-file writer
        # writes one (lib/safe_write.write_user_file) — a read-only file
        # or a link leaving the project (the folder holding the year
        # folders, with shared inputs) is refused, a link inside it
        # keeps pointing at its target, which is replaced; the previous
        # contents are kept as the next free .bak/.bakN (security review
        # M1). It used to be replaced like a generated file: a read-only
        # file rewritten, a link replaced by a file (backlog TJ-0586).
        from taxjson.lib.cli_diag import cannot_write
        from taxjson.lib.safe_write import OutsideLinkError, write_user_file
        _shown_out = _PL.shown(out, root)
        try:
            bak = write_user_file(out, text, _PL.write_boundary(root))
        except OutsideLinkError as e:
            _die_input(str(e), "Nothing was written.")
        except OSError as e:
            _head, _fix = cannot_write(_shown_out, e, out)
            _die_input(_head, " ".join(x for x in (_fix, "Nothing was "
                                                         "written.") if x))
        if bak is not None:
            _note(f"kept the previous {out.name} as {bak.name}",
                  prog="tjs opening")
        _shared_inputs_note(root, out, "tjs opening")
    for n in report.notes + notes:
        _note(n, prog="tjs opening")
    for sym, why in skipped:
        _warn(f"skipped {sym}: {why}", prog="tjs opening")
    if not lines:
        _die(f"no position of {shown_name(path)} could be written (see "
             f"above) — nothing written")
    where = ("(dry run — nothing written)" if getattr(args, "dry_run",
                                                       False)
             else f"-> {_PL.shown(out, root)}")
    d = _out.Doc(exact=True, width_=0, live=True)
    for ln in _wrap(f"{len(lines)} OPENING line(s) for account {name} as "
                    f"of {snapshot} {where}"
                    + (f"; {len(skipped)} position(s) skipped (see above)"
                       if skipped else "")):
        d.line(ln)
    if not getattr(args, "dry_run", False):
        for ln in _wrap("Next: `tjs run`, then `tjs "
                        "find-missing-history` and `tjs sanity` (it "
                        "compares quantities and costs)."):
            d.line(ln)


def _opening_snapshot(args: argparse.Namespace, path: Path, report: Any) -> Any:
    """tjs opening: the opening's day, --date or else the report's;
    refused when there is none, or when it is not YYYY-MM-DD (the
    padded form the .tt reader takes)."""
    from taxjson.lib.brokerages.base import shown_name
    snapshot = args.date or report.as_of
    if not snapshot:
        _die(f"{shown_name(path)} does not say which day its positions "
             f"are for",
             "Pass --date YYYY-MM-DD (the statement's date).")
    if not _opening_iso_day(snapshot):
        # The padded form the .tt reader takes (taxjson-convert-tt
        # _check_date): strptime let `--date 2025-1-1` through, and
        # the opening_2025-1-1.tt written stopped the next run
        # (backlog TJ-0317).
        _padded = _opening_padded_day(snapshot)
        _die(f"{'--date' if args.date else shown_name(path) + ': the date'}"
             f" {snapshot!r} is not a YYYY-MM-DD date",
             (f"Write it {_padded}" if _padded else
              "Give the statement's day as YYYY-MM-DD (e.g. 2025-01-31)")
             + (" (--date)." if args.date else
                ", or pass --date YYYY-MM-DD."))
    return snapshot


def _opening_broker_accounts(path: Path, report: Any) -> None:
    """tjs opening: a positions report that lists more than one broker
    account refused (counted by each row's whole account key; a
    consolidated statement's rows carry none)."""
    from taxjson.lib.brokerages.base import shown_name
    # Counted by each row's key of the whole account id: the masked
    # label (two characters + ***) is the same for two ids that share
    # their first two characters. A consolidated IB statement's rows
    # carry no one account's key (one position for all its accounts
    # combined).
    by_key: Dict[str, str] = {}
    for r in report.rows:
        k = r.account_key or r.account
        if k:
            by_key.setdefault(k, r.account)
    brokers = sorted(by_key.values())
    if len(brokers) > 1:
        alike = (" — ids that differ after the two characters shown"
                 if len(set(brokers)) < len(brokers) else "")
        _die(f"{shown_name(path)} lists {len(brokers)} broker accounts "
             f"({', '.join(brokers)}{alike})",
             "An opening is one taxjson account's: export each broker "
             "account's positions separately (or split the file).")


def _opening_target(root: Path, name: str, snapshot: str,
                    lines: List[str]) -> Tuple[Path, Path]:
    """tjs opening: the account's folder and its opening_<snapshot>.tt,
    refused when another .tt of the account already has OPENING lines
    for one of these symbols, compared as the books spell them
    (ticker.map's joins)."""
    acct_dir = _PL.inputs_dir(root) / name
    out = acct_dir / f"opening_{snapshot}.tt"
    # Another opening file of this account for the same symbols would
    # count them twice (two .tt files are two records — never deduped).
    from taxjson.lib.tt_format import strip_tt_comment, tt_lines
    # Compared as the books spell them: the merge stages map both
    # openings through the project's ticker.map (GLOBAL, TOBASE), where
    # same-day OPENING rows add up (lots) — an earlier OPENING of
    # SYN.A.TO under GLOBAL SYN.A.TO SYN.TO and a new SYN.TO snapshot
    # counted the shares twice (backlog TJ-0825, TJ-0814).
    _bmap: Dict[str, str] = {}
    try:
        from taxjson.lib.missing_history import declaration_maps
        _bmap = declaration_maps(root).base
    except Exception:                               # noqa: BLE001
        _bmap = {}      # a map the run refuses: the run names it

    def _booked(sym: str) -> str:
        sym = sym.upper()
        if not _bmap:
            return sym
        from taxjson.lib.missing_history import map_symbol_through
        try:
            return str(map_symbol_through(sym, _bmap)).upper()
        except Exception:                           # noqa: BLE001
            return sym
    syms = {_booked(ln.split()[2]) for ln in lines}
    clash = []
    # Every .tt the run reads (input_files: the suffix in any case — a
    # START.TT too).
    for f in input_files(acct_dir, ".tt"):
        if f.resolve() == out.resolve():
            continue
        try:
            text = f.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        for ln in tt_lines(text):
            parts = strip_tt_comment(ln).split()
            if len(parts) >= 3 and parts[0] == "OPENING" \
                    and _booked(parts[2]) in syms:
                _was = parts[2].upper()
                _as = _booked(_was)
                clash.append((f.name, _was if _as == _was else
                              f"{_was} (the books' {_as})"))
    if clash:
        _die(f"{_PL.shown(acct_dir / clash[0][0], root)} already has "
             f"OPENING lines for "
             f"{', '.join(sorted({s for _f, s in clash})[:6])}",
             "One opening per symbol per account (the spellings "
             "ticker.map joins are one symbol): remove that file, or "
             "the lines, first.")
    return acct_dir, out


def _late_rename_trades(root: Path) -> Dict[Tuple[str, str],
                                              Tuple[str, str]]:
    """(SYMBOL, account) -> (rename date, new symbol): the trades in an
    old ticker after its dated rename that no declaration resolves
    (lib/renames.unresolved_late, as the run warns): a short they make
    is no purchase missing from the files (backlog TJ-0442)."""
    from taxjson.lib.renames import unresolved_late
    try:
        rows = unresolved_late(root, load_config(root))
    except (ValueError, OSError, SystemExit):
        return {}
    out: Dict[Tuple[str, str], Tuple[str, str]] = {}
    for x in rows:
        k = (str(x.get("symbol") or "").upper(), str(x.get("account")))
        v = (str(x.get("rename_date") or "")[:10],
             str(x.get("renamed_to") or ""))
        if k not in out or v < out[k]:
            out[k] = v
    return out


def _write_missing_history_tt(root: Path, cache: Path,
                              rows: List[Dict[str, Any]], year: Any,
                              outside: bool, files: List[Path],
                              flag: str, covers: Sequence[Any] = ()) -> None:
    """`find-missing-history --write-missing-history` (no FILE): one
    `OPENING <date> <SYMBOL> <qty> cost=unknown` line per suggested pair
    in inputs/<account>/missing_history.tt, dated the day before the
    account's first row, its quantity the units the run opens for it
    (the deepest shortage of the rows through the tax year's end; for a
    pair short only after it, of every row). `covers`: the unanswered
    covers (lib/first_run.unanswered_covers), each pair's units a negative
    line. Merged with the lines the account's .tt files have: never
    written twice; a symbol opened with another quantity is listed and
    left as it is."""
    from taxjson.lib import missing_history as MH
    prog = f"{_PROG} find-missing-history"
    if outside:
        rows_by = _year_short_rows(root, files, year)
        rows = [e for e in rows
                if (r := rows_by.get((e.get("symbol"), e.get("account"))))
                is not None and not r.year_listed]
    lines: List[Any] = []
    unsized: List[str] = []
    nodate: List[str] = []
    # Short, but not sized: two holdings renamed into one symbol (the
    # rows cannot tell which the missing units were; backlog TJ-0959).
    ambiguous: List[str] = []
    # A short a trade in an old ticker after its rename makes: its
    # purchase is in the files, so an OPENING line is never its fix
    # (backlog TJ-0442: "the rows never go short" said of it).
    _late = _late_rename_trades(root)
    late: List[str] = []
    for e in sorted(rows, key=lambda e: (str(e.get("account")),
                                         str(e.get("symbol")))):
        sym = str(e.get("symbol") or "").upper()
        acct = str(e.get("account") or "")
        if (sym, acct) in _late:
            d, new = _late[(sym, acct)]
            late.append(f"`RENAME {d} {sym} {new} late=fold` ({acct})")
            continue
        q = e.get(MH.QUANTITY_KEY)
        how = (f"the shortage of the rows through {e.get('_sized_through')}"
               if q and e.get("_sized_through") else "")
        if not q and e.get("_not_sized"):
            ambiguous.append(f"{sym} / {acct}: {e.get('_not_sized')}")
            continue
        if not q:
            q = e.get("_quantity_all_rows")
            how = "the shortage of every row"
        if not q or not sym or not acct:
            unsized.append(f"{sym} / {acct}")
            continue
        d = MH.default_opening_date(cache, acct)
        if d is None:
            nodate.append(f"{sym} / {acct}")
            continue
        lines.append(MH.TtLine(acct, sym, float(q), d,
                               f"find-missing-history: {how}"))
    # A short held before the data (TJ-0844): the units the broker codes
    # as closing with no short in the rows.
    _longs = {(ln.symbol, ln.account) for ln in lines}
    both: List[str] = []
    _short_q: Dict[Tuple[str, str], float] = {}
    for c in covers:
        k = (c.symbol, c.account)
        _short_q[k] = _short_q.get(k, 0.0) + float(c.unbacked_qty)
    for (sym, acct), q in sorted(_short_q.items()):
        if (sym, acct) in _longs:
            both.append(f"{sym} / {acct}")
            continue
        d = MH.default_opening_date(cache, acct)
        if d is None:
            nodate.append(f"{sym} / {acct}")
            continue
        lines.append(MH.TtLine(acct, sym, -q, d,
                               "find-missing-history: the broker codes the "
                               "cover closing, with no short in the rows"))
    try:
        plan = MH.plan_tt_write(root, lines)
    except MH.TtOpeningError as e:
        _die_input(f"{flag}: the accounts' OPENING cost=unknown lines "
                   f"cannot be read — fix them first; nothing was written:",
                   *str(e).splitlines())
    for ln in MH.tt_differ_lines(plan):
        _say("warning", f"{flag}: {ln}", prog=prog)
    if late:
        _say("warning", f"{flag}: not written: {len(late)} short(s) a "
             f"trade in an old ticker after its rename makes",
             "The purchase is in your files under the old ticker: "
             "declare the late trades with a .tt line, not an opening — "
             + "; ".join(late) + " (`late=separate` when it is another "
             "security; `tjs renames` prints the lines).", prog=prog)
    if ambiguous:
        _say("warning", f"{flag}: not written: {len(ambiguous)} sale(s) "
             f"with no purchase of a symbol two holdings were renamed "
             f"into", *ambiguous, prog=prog)
    if both:
        _say("warning", f"{flag}: not written: {len(both)} symbol(s) with "
             f"both a sale with no purchase and a cover with no short",
             "One line per symbol per account opens one side only: add "
             "the older rows to a .tt file: " + ", ".join(both) + ".",
             prog=prog)
    if unsized or nodate:
        _say("note", f"{flag}: not written: "
             + ", ".join(unsized + nodate),
             "No shortage to size (the rows never go short), or no books "
             "for the account (run `tjs run`).", prog=prog)
    if not plan.n_add:
        _say("note", f"nothing to add: "
             + (f"{len(plan.same)} line(s) already there"
                if plan.same else "no line to write for the pair(s) above"
                if late or unsized or nodate or ambiguous or both
                or plan.differ
                else "no sale with no purchase in your files"),
             prog=prog)
        return
    from taxjson.lib.safe_write import OutsideLinkError
    try:
        written = MH.apply_tt_plan(plan)
    except OutsideLinkError as e:
        _die_input(f"{flag}: {e}")
    except OSError as e:
        _die_input(f"{flag}: cannot write: {e}")
    # OPENING cost=unknown lines: read from v0.27.0 (lib/requires); with
    # shared exports by every year.
    _raise_requirements(root, ("missing_history_tt",),
                        all_years=_PL.shared_inputs(root), quiet=True)
    # Wrapped at the house width, as the --write-purchases note is
    # (docs/output-style.md, Width; backlog TJ-0175).
    _notes = [f"Wrote {len(plan.add[p.parent.name])} OPENING cost=unknown "
              f"line(s) to {_PL.shown(p, root)} (sales with no purchase in "
              f"your files)" for p in written]
    _notes.append(("These inputs are shared by every year's project: the "
                   "lines apply to every year. "
                   if _PL.shared_inputs(root) else "")
                  + "Review each line: remove any that is a real short "
                  "position, and give the units held before your data and "
                  "the date (before the first sale they cover). `tjs "
                  "run` reads them.")
    if _short_q:
        _notes.append("A negative quantity is a short held before your "
                      "data: date it before its cover, whose gain or loss "
                      "then goes to manual reporting.")
    _show_blocks((_out_wrap(_note, file=sys.stderr) for _note in _notes),
                 sys.stderr)


def _relay_gains_stderr(text: Optional[str], tmp_path: str, name: str) -> None:
    """The stderr of a gains child that wrote to the temporary `tmp_path`,
    for the file `name`: each message names the file, and one about the
    temporary file goes, with its details."""
    _relay_child_stderr("\n".join(
        ln if ln[:1].isspace() or tmp_path in ln else f"{ln} [{name}]"
        for ln in (text or "").splitlines()
        if not (ln[:1].isspace() and tmp_path in ln)),
        handled=(tmp_path,), nested=False)


def _gains_suggestions(args, root: Path, f: Path, year, outside,
                       _flag: str) -> List[dict]:
    """What the gains child suggests for the file `f`, through a temporary
    file removed on every path (a child that fails exits here)."""
    settings = load_settings(root, missing_ok=True)
    import json
    import tempfile
    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        cmd = _cmd("taxjson-gains") + ["--suggest-missing-history",
                                       tmp_path]
        # The project's jurisdiction, as the pipeline passes it
        # (taxjson-gains requires it). (Sheltered books
        # stay in: a short in a registered account is the MOST
        # certain missing purchase, and `run` applies the file to every
        # account's gains stage.)
        cmd += ["--country", _country(settings)]
        # The project's date basis, as the report and the gains
        # stage year-scope a sale: without it the helper fell back
        # to the country default, so a trade-dated Canadian
        # project's Dec 31 sale settling in January was filtered
        # out as another year's though the report said AFFECTS
        # (backlog TJ-0316).
        cmd += ["--tax-date", _tax_date(settings)]
        # The project's written-option timing and income dating,
        # as the gains stage runs (re-audit A2-1361: the standalone
        # close-timing default and its note leaked in here).
        cmd += option_timing_flags(settings)
        cmd += income_dating_flags(settings)
        if year:
            cmd += ["--year", str(year)]
        if args.include_options:
            cmd += ["--include-options-in-suggestions"]
        if args.all_history or outside:
            cmd += ["--all-history"]
        cmd += [str(f)]
        # Capture the inner tool's stderr and relay it, without what it
        # says of the temp file (our merged summary replaces that), an
        # error's fix lines always. Devnulling ALL of it swallowed the
        # "Filtered N candidate(s)... Use --all-history" warning — the
        # only signal that a year-scoped run dropped a pair.
        from taxjson.pipeline.dispatch import run_cmd as _run_cmd
        proc = _run_cmd(cmd, capture_output=True)
        if proc.stdout:
            sys.stdout.write(proc.stdout)
        _relay_gains_stderr(proc.stderr, tmp_path, f.name)
        if proc.returncode != 0:
            sys.exit(exit_text(f"tjs find-missing-history {_flag}: "
                               f"taxjson-gains failed on {f.name} "
                               f"(exit {proc.returncode})."))
        try:
            return json.loads(Path(tmp_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _refuse_stale_books_write(root: Path, flag: str) -> None:
    """Exit 1 before find-missing-history writes lines from books older
    than the inputs (the last full run's record of what it read, as
    run-clean compares it): a purchase just added to a .tt file is not
    in them, so the line written for its sale opened a phantom position
    (backlog TJ-0077). The files the write flags change themselves do
    not count: missing_history.tt (merged by the write, which reads it)
    and taxjson.toml (the write pins requires_taxjson there), so a
    second write before a run still merges. The banner says the rest,
    and every run-state problem."""
    try:
        from taxjson.lib.checklist import (FINGERPRINT_VERSION, fingerprint_changes,
                                           input_fingerprint)
        from taxjson.lib.fingerprint_record import _load_fingerprint_doc
        cfg = load_config(root, missing_ok=True)
        doc = _load_fingerprint_doc(root)
        if not doc or doc.get("version") != FINGERPRINT_VERSION:
            return
        changed = fingerprint_changes(doc["files"],
                                      input_fingerprint(root, cfg))
    except Exception:                                   # noqa: BLE001
        return
    stale = [k for k in changed
             if k.rsplit("/", 1)[-1] not in ("missing_history.tt",
                                             "taxjson.toml")]
    if stale:
        _die(f"{flag}: the books in work/ are older than the inputs "
             f"(changed since the last full run: {', '.join(stale[:3])}"
             f"{' ...' if len(stale) > 3 else ''})",
             "Lines written from them could open a position the inputs "
             "already fix (a purchase just added to a .tt file). Run "
             "`tjs run` first; nothing was written.")


def _is_configured_account(root: Path, name: str) -> bool:
    """`name` is an account of the project's taxjson.toml."""
    try:
        accts = _PL.read_config_soft(root).get("accounts") or {}
    except Exception:                               # noqa: BLE001
        return False
    return isinstance(accts, dict) and name in accts


def _refuse_missing_history_file(name: str) -> None:
    """Exit 2: `--write-missing-history FILE` (the JSON candidates
    before v0.27.0) — the flag writes .tt lines and takes no FILE."""
    _die_input(f"--write-missing-history takes no FILE ({name}): it writes "
               f"dated .tt lines (OPENING <date> <SYMBOL> <qty> "
               f"cost=unknown) into inputs/<account>/missing_history.tt, "
               f"merged with the lines there — missing_history.json is no "
               f"longer read.")


def cmd_find_missing_history(args: argparse.Namespace) -> None:
    """Convenience wrapper over `taxjson-missing-history`: resolve the account
    base file(s) from the project and default the year from taxjson.toml."""
    root = Path(args.dir).resolve()
    cache = root / "work"
    # --gen-phantoms: the old spelling of --write-missing-history (hidden,
    # still accepted with a note).
    if getattr(args, "write_missing_history", None) is None:
        args.write_missing_history = None
    # A FILE after the flag (the JSON candidates before v0.27.0; the old
    # --gen-phantoms FILE) reads as the account: refused with a word.
    # A configured account is never a FILE: an account name may hold a
    # period (`[accounts."margin.old"]`), which read as a suffix
    # (backlog TJ-0816).
    _file = getattr(args, "gen_phantoms", None) or (
        args.account if args.write_missing_history and args.account
        and (Path(args.account).suffix or "/" in args.account)
        and not _is_configured_account(root, args.account) else None)
    if _file:
        _refuse_missing_history_file(_file)
    if (getattr(args, "write_purchases", None) is not None
            and args.write_missing_history is not None):
        _die_input("--write-purchases and --write-missing-history write "
                   "different files: run them one at a time.")
    # Per-account full-history base files only — skip the raw/native
    # derivatives and the combined sheltered file (its accounts are
    # already covered individually).
    all_files = _project_base_files(cache)
    if args.account:
        f = cache / f"{args.account}_base.json"
        if not f.exists():
            _die_input(f"no {f.name} in {cache} (run `tjs run` first, "
                       f"or check the name).")
        files = [f]
    else:
        files = all_files
        if not files:
            sys.exit(exit_text(f"tjs find-missing-history: no base files in {cache} "
                               f"(run `tjs run` first)."))
    outside = bool(getattr(args, "outside_year", False))
    if outside and args.write_missing_history is None:
        _die_input("--outside-year goes with --write-missing-history (it "
                   "writes the pairs that do not bear on the tax year).")
    if outside and args.all_history:
        _die_input("--outside-year and --all-history choose different "
                   "pairs: give one.")

    year = args.year
    if year is None:
        year = load_settings(root, missing_ok=True).year
    if (args.write_missing_history is not None
            or getattr(args, "write_purchases", None) is not None):
        _refuse_stale_books_write(
            root, "--write-missing-history"
            if args.write_missing_history is not None
            else "--write-purchases")

    # --write-missing-history: instead of the diagnostic report, write
    # the sales with no purchase in the files (the truncated-history rows
    # — positions that go negative, the subset an opening balance
    # actually fixes) as dated .tt lines (inputs/<account>/
    # missing_history.tt: OPENING <date> <SYMBOL> <qty> cost=unknown —
    # lib/missing_history). $0-cost corp-action rows are intentionally
    # NOT emitted; those need a merger/spinoff basis, not a synthetic
    # opening balance. The candidates (incl. year-scoping and each one's
    # size) come from `taxjson-gains --suggest-missing-history`, which
    # takes one base file, so we run it per account and merge them
    # (keyed by symbol+account).
    if args.write_missing_history is not None:
        _find_missing_history_write(args, root, cache, files, all_files, year, outside)
        return

    # Every book is read (a Canadian ACB pool spans the taxable
    # accounts: lib/missing_history.year_pool); ACCOUNT narrows the
    # report with --account.
    cmd = _cmd("taxjson-missing-history") + [
        str(f) for f in (all_files if args.account and files[0] in all_files
                         else files)]
    if (args.account and files[0] in all_files and len(all_files) > 1
            and getattr(args, "write_purchases", None) is None):
        cmd += ["--account", args.account]
    if getattr(args, "write_purchases", None) is not None:
        # Drafts of purchase lines from the broker's cost evidence
        # (tax-logic CA-ACB-15 / US-BASIS-08): the tool writes them
        # beside the account's inputs, as .tt.txt the run never reads.
        cmd += ["--write-purchases", args.write_purchases]
        if args.force:
            cmd += ["--force"]
        if args.all_history:
            cmd += ["--all-history"]
        if args.account:
            cmd += ["--account", args.account]
    if not args.account:
        # The glob above sees only books that exist: a CONFIGURED
        # account whose book was never built (a failed parse, deferred
        # elections) was skipped silently and the checklist marked the
        # step done (2026-09 audit S047-18). Accounts `run` skipped for
        # having no inputs at all are expected to have none.
        _have = {f.name[: -len("_base.json")] for f in files}
        _quiet = _accounts_skipped_for_no_inputs(root)
        for _n in sorted((load_config(root, missing_ok=True).get("accounts") or {})):
            if _n not in _have and _n not in _quiet:
                emit_line(f"tjs find-missing-history: note: account {_n} "
                          f"has no work/{_n}_base.json — not checked (run "
                          f"`tjs run`).", file=sys.stderr)
                cmd += ["--unchecked-account", _n]
    if year:
        cmd += ["--year", str(year)]
    if args.include_options:
        cmd += ["--include-options"]
    _mh_arg = _missing_history_arg(root)
    if _mh_arg is not None:
        cmd += ["--missing-history", str(_mh_arg)]
    if (_PL.ticker_map_path(root)).exists():
        # Name the broker's ticker of a renamed symbol (S049-01).
        cmd += ["--ticker-map", str(_PL.ticker_map_path(root))]
    if _details(args) and getattr(args, "write_purchases", None) is None:
        cmd += ["--details"]
    if getattr(args, "write_purchases", None) is None:
        for (_s, _a), (_d, _n) in sorted(_late_rename_trades(root).items()):
            # A short a late trade in a renamed ticker makes is that
            # rename's .tt line to add, not a purchase (TJ-0442).
            cmd += ["--late-rename", _a, _s, _d, _n]
    if (cache / IN_KIND_FILE).is_file() and getattr(
            args, "write_purchases", None) is None:
        # A short an in-kind withdrawal NOT booked explains is that
        # move's INKIND line to add, not a purchase (backlog TJ-0439).
        cmd += ["--in-kind", str(cache / IN_KIND_FILE)]
    _exec_tool(cmd)


def _find_missing_history_write(args: argparse.Namespace, root: Path, cache: Path,
                                 files: List[Path], all_files: List[Path], year: Any,
                                 outside: bool) -> None:
    """find-missing-history --write-missing-history: the sales with no
    purchase in the books (each account's gains child), merged; a short
    a same-day journal leg covers left out; the covers with no short
    held, by the year's scope; written as dated .tt lines."""
    _flag = ("--write-missing-history --outside-year" if outside
             else "--write-missing-history")
    if outside and not year:
        _die_input(f"{_flag}: no tax year — set [settings] year in "
                   f"taxjson.toml or pass --year.")

    merged: Dict[Tuple[str, str], dict] = {}
    for f in files:
        for e in _gains_suggestions(args, root, f, year, outside, _flag):
            merged[(e.get("symbol"), e.get("account"))] = e

    # A short that a same-day journal leg covers (Norbert's gambit,
    # RBC's ordinal times put the sale first) is not missing
    # history: an entry for it pulled the sale off Schedule 3 and
    # invented the shares (audit A2-0309). taxjson-gains knows no
    # journal, so the journal symbols (a JOURNAL line, a join of the
    # run, a broker journal of the exports — lib/missing_history.
    # walk_journal_symbols) are re-checked here with the walk that
    # knows them.
    _tm_path = _PL.ticker_map_path(root)
    if merged:
        from taxjson.lib.missing_history import (account_types_near,
                                                  detect_missing_history,
                                                  walk_journal_symbols)
        from taxjson.lib.tx_load import load_transactions
        _journal = walk_journal_symbols(
            cache, _tm_path if _tm_path.is_file() else None)
        _js = {k for k in merged
               if str(k[0] or "").upper() in _journal}
        if _js:
            _txs = []
            for f in files:
                try:
                    _txs.extend(load_transactions(f))
                except (OSError, ValueError):
                    pass
            _still = {(c.symbol, c.account) for c in detect_missing_history(
                _txs, include_options=True, include_broker_shorts=True,
                registered_accounts=(account_types_near(files[0])
                                     or None) if files else None,
                journal_symbols=_journal)}
            for k in sorted(_js - _still):
                del merged[k]
                _note_out(f"left out {k[0]} / {k[1]}: its short is the "
                          f"same-day leg of a journal between the "
                          f"security's lines (Norbert's gambit), not "
                          f"missing history.", whole=True)

    rows = list(merged.values())
    # A cover the broker codes closing with no short held: through the
    # year (`--all-history`: every one; `--outside-year`: the later).
    _ys = str(year) if year else ""
    _cfg = load_config(root, missing_ok=True)
    _covers = _unanswered_covers(
        root, dict(_cfg, settings=settings_of(_cfg).replace(year=None)),
        args.account or None)
    if outside:
        _covers = [c for c in _covers if c.date[:4] > _ys]
    elif not args.all_history:
        _covers = [c for c in _covers if not _ys or c.date[:4] <= _ys]
    _write_missing_history_tt(root, cache, rows, year, outside,
                              all_files or files, _flag, _covers)


def _register_run(sub: argparse._SubParsersAction) -> None:
    p_run = sub.add_parser(
        "run",
        help="Build the books and every report from inputs/",
        description="Run the full pipeline: parse every broker file in "
                    "the accounts' folders (inputs/, or [settings] "
                    "inputs_dir), convert to the base currency, apply "
                    "ticker.map, corporate actions and your elections, "
                    "compute the gains with the superficial-loss / "
                    "wash-sale pass across all your accounts, and write "
                    "reports/. Every stage is rebuilt, every run. The "
                    "run ends with the broker positions "
                    "cross-check when there are positions snapshots "
                    "(holdings/, found without a setting). "
                    "Commands chain: `tjs run sum`.")
    p_run.add_argument("--account", help="Process only one account")
    p_run.add_argument("--no-input", action="store_true",
                       help="Never prompt (scripts/CI): unresolved corp-action "
                            "elections write work/pending_elections.json "
                            "and exit 3 — resolve with `tjs elect "
                            "... --set`, then re-run")
    p_run.add_argument("--strict", action="store_true",
                       help="Promote per-account validation ERRORs "
                            "(oversold positions, malformed rows) to "
                            "fatal — the run stops instead of "
                            "publishing reports with a DIAGNOSTICS "
                            "banner. Recommended for CI/cron")
    p_run.set_defaults(func=cmd_run)


def _register_crypto_sends(sub: argparse._SubParsersAction) -> None:
    p_csend = sub.add_parser(
        "crypto-sends",
        help="Decide the crypto sends that left your accounts",
        description="Crypto withdrawals/sends that did not arrive in "
                    "another of your crypto accounts. A send is paired "
                    "with an arrival of the same coin on another "
                    "exchange, or on the same exchange in another "
                    "account, from 10 minutes before to 3 days after the "
                    "send, losing at most 10% to the network fee; a "
                    "paired send is your own move. Decide each unpaired "
                    "send: self (own wallet) / gift (Canada only) / "
                    "payment, with its fair value and the .tt BUYSELL "
                    "line; --write generates "
                    "inputs/<acct>/crypto_sends.tt. Stablecoins get the "
                    "currency-gain calculation instead of a sale line.")
    p_csend.add_argument("account", nargs="?",
                         help="Crypto account (default: all)")
    p_csend.add_argument("--set", action="append", metavar="ID=DECISION",
                         help="Record a decision (self | gift | payment; "
                              "a US project refuses gift) "
                              "for a send id from the listing; "
                              "repeatable. A `-fee` id (a network fee "
                              "hidden in a send that arrived short) takes "
                              "`ID-fee=fee --price P`; an `in-` id (a "
                              "stablecoin arrival) takes new | return")
    p_csend.add_argument("--unpair", action="store_true",
                         help="With --set ID=gift|payment (US: payment) "
                              "on a send the "
                              "tool paired with an arrival: keep it "
                              "unpaired (that arrival was unrelated)")
    p_csend.add_argument("--unset", action="append", metavar="ID",
                         help="Remove the saved decision for a send id "
                              "(it is undecided again: `tjs run` "
                              "asks) or an arrival id; repeatable")
    p_csend.add_argument("--note", metavar="TEXT",
                         help="With --set: a note kept with the decision "
                              "and written into crypto_sends.tt")
    p_csend.add_argument("--price", type=positive_float_arg, metavar="P",
                         help="With --set: fair value per coin in the base "
                              "currency, when the price lookup fails "
                              "(finite, at least 0.00000001; re-deciding a "
                              "send to another decision drops it)")
    p_csend.add_argument("--write", action="store_true",
                         help="(Re)generate inputs/<acct>/crypto_sends.tt "
                              "from the decisions (idempotent)")
    p_csend.add_argument("--json", action="store_true",
                         help="Emit JSON instead of text")
    p_csend.set_defaults(func=cmd_crypto_sends)


def _register_compare(sub: argparse._SubParsersAction) -> None:
    p_cmp = sub.add_parser(
        "compare",
        help="Diff the reports with an earlier run's",
        description="Compare reports/ with a snapshot of an earlier "
             "run: the run before (`prev`, kept by every full run), one "
             "saved with --save, or the reports as filed (`filed`, kept "
             "by close-year), or a full copy made by `tjs snapshot`. "
             "Shows the account figures that moved, the "
             "report files and the inputs that changed; exit 1 when a "
             "report differs.")
    p_cmp.add_argument("name", nargs="?", default=None,
                       help="prev (default), filed, a saved name, or a "
                            "`tjs snapshot` name or its folder")
    p_cmp.add_argument("--save", metavar="NAME",
                       help="Keep the current reports/ as NAME")
    p_cmp.add_argument("--delete", metavar="NAME",
                       help="Remove the snapshot NAME")
    p_cmp.add_argument("--list", action="store_true",
                       help="List the snapshots")
    p_cmp.add_argument("--json", action="store_true",
                       help="Emit JSON instead of text")
    p_cmp.set_defaults(func=cmd_compare)


def _register_snapshot(sub: argparse._SubParsersAction) -> None:
    p_snap = sub.add_parser(
        "snapshot",
        help="Copy the whole project, to diff or compare later",
        description="Copy the project (reports, books, inputs, settings "
             "and maps) to snapshot/<year>.<timestamp>/, laid out as the "
             "project is, beside the year folders in a multi-year project. "
             "`diff -r` reads it file by file, `tjs compare NAME` "
             "reads it, and taxjson runs in it. It holds your real figures.")
    p_snap.add_argument("--json", action="store_true",
                        help="Emit JSON instead of text")
    p_snap.set_defaults(func=cmd_snapshot)


def _register_clean(sub: argparse._SubParsersAction) -> None:
    p_clean = sub.add_parser(
        "clean",
        help="Remove what `tjs run` wrote (work/, reports/)",
        description="Remove what `tjs run` generated in the project: the "
             "files of work/ and reports/ taxjson writes, and the files the "
             "run wrote into exports_dir, so the next `tjs run` builds every "
             "file again from the inputs. A file goes only by a name taxjson "
             "gives one; any other (and every .tt file) stays and is named. "
             "What cannot be rebuilt stays: taxjson.toml, the "
             "maps, inputs/, holdings/, filed/, snapshot/, inputs_redact/, "
             "and in work/ the lock files, the slip-key salt, `tjs watch`'s "
             "baseline, the price caches, the generated crypto-sends .tt and "
             "the snapshots `tjs compare --save` kept. A link is never "
             "followed. In the folder holding the year folders it cleans "
             "every year. Refused, nothing removed, while a `tjs run` holds "
             "the project's lock, or when a folder of your files is in work/ "
             "or reports/.")
    p_clean.add_argument("--dry-run", action="store_true",
                         help="List what would be removed; remove nothing")
    p_clean.add_argument("--json", action="store_true",
                         help="Emit JSON instead of text")
    p_clean.set_defaults(func=cmd_clean)


def _register_find_missing_history(sub: argparse._SubParsersAction) -> None:
    p_fmh = sub.add_parser(
        "find-missing-history",
        help="Find positions whose purchase history is missing",
        description="Find positions with missing cost basis — a "
             "truncated buy history (the export starts after the "
             "purchase) or $0-basis shares from a corporate action — "
             "that distort a year's gain. Pairs the project's "
             ".tt OPENING cost=unknown lines already cover are listed "
             "apart; --write-missing-history writes such lines.")
    p_fmh.add_argument("account", nargs="?", help="Account (default: all)")
    p_fmh.add_argument("--year", type=_tax_year_arg,
                       help="Tax year to scope relevance (default: config year)")
    p_fmh.add_argument("--include-options", action="store_true",
                       help="Also check option positions")
    p_fmh.add_argument("--write-missing-history", action="store_const",
                       const=True, default=None,
                       help="Instead of the report, write the sales with "
                            "no purchase in your files (truncated-history "
                            "rows) as dated lines `OPENING <date> <SYMBOL> "
                            "<qty> cost=unknown` in inputs/<account>/"
                            "missing_history.tt (merged with the lines "
                            "there; `tjs run` reads them)")
    # The flag's old name, hidden (refused with FILE like the new one).
    p_fmh.add_argument("--gen-phantoms", metavar="FILE",
                       help=argparse.SUPPRESS)
    p_fmh.add_argument("--all-history", action="store_true",
                       help="With --write-missing-history or "
                            "--write-purchases, emit every candidate, not "
                            "just those affecting the tax year")
    p_fmh.add_argument("--outside-year", action="store_true",
                       help="With --write-missing-history: ADD to the "
                            "file only the positions that do NOT bear on "
                            "the tax year (no row of the year draws on or "
                            "touches them; Canada: no other taxable "
                            "account trades the symbol that year), keeping "
                            "every entry already there. `tjs run` then "
                            "stops listing them; the year's numbers do not "
                            "change")
    p_fmh.add_argument("--write-purchases", metavar="FILE", nargs="?",
                       const="", default=None,
                       help="Instead of the report, DRAFT .tt purchase "
                            "lines from the broker's own cost evidence "
                            "(IB's Basis on a closing sale with no "
                            "purchase in your files, a transfer-in's "
                            "stated book value) into a file the run "
                            "does NOT read (default: inputs/<account>/"
                            "purchases_draft.tt.txt). Review it, fill in "
                            "the YYYY-MM-DD / COST placeholders, then "
                            "rename it to .tt")
    p_fmh.add_argument("--force", action="store_true",
                       help="With --write-missing-history or "
                            "--write-purchases, replace an existing FILE "
                            "(a reviewed file is otherwise refused; the "
                            "old file is kept as FILE.bak, or the next "
                            "free .bakN)")
    p_fmh.set_defaults(func=cmd_find_missing_history)


def _register_opening(sub: argparse._SubParsersAction) -> None:
    p_open = sub.add_parser(
        "opening",
        help="Opening balance from a broker's positions report",
        description="Turn a broker's positions report (an Interactive "
             "Brokers Activity Statement's Open Positions, an RBC "
             "Holdings Export, or a [[holding]] TOML) into "
             "inputs/ACCOUNT/opening_DATE.tt: one OPENING line per long "
             "position with its quantity and the report's book cost "
             "(never its market value) — per lot with its purchase date "
             "in a US project. An opening balance is not a purchase "
             "(no superficial-loss / wash-sale window), and it replaces "
             "the account's rows of those symbols dated on or before it "
             "(`tjs tax-logic`: CA-OPEN / US-OPEN).")
    p_open.add_argument("account", help="The taxjson account it opens")
    p_open.add_argument("file", help="The positions report")
    p_open.add_argument("--date", metavar="YYYY-MM-DD",
                        help="The snapshot day (default: the report's "
                             "own as-of date; required when it has none)")
    p_open.add_argument("--dry-run", action="store_true",
                        help="Print the lines instead of writing the file")
    p_open.add_argument("--force", action="store_true",
                        help="Replace an existing opening_DATE.tt (kept "
                             "as .bak, or the next free .bakN)")
    p_open.set_defaults(func=cmd_opening)


def register(sub: argparse._SubParsersAction) -> None:
    """This group's commands, for the `tjs` parser."""
    _register_run(sub)
    _register_crypto_sends(sub)
    _register_compare(sub)
    _register_snapshot(sub)
    _register_clean(sub)
    _register_find_missing_history(sub)
    _register_opening(sub)
