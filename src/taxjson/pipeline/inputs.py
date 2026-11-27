"""The inputs/ folders: the files found, the broker each is read as, the per-account settings."""
import hashlib
import math
import sys
from datetime import date as date_cls, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from taxjson.project.settings import Settings
from taxjson.lib import project_layout as _PL
from taxjson.lib.safe_write import write_atomic as _write_atomic

from taxjson.pipeline import run_state
from taxjson.pipeline.config import _config_shown, _country, input_files
from taxjson.pipeline.diagnostics import _split_msg
from taxjson.pipeline.freshness import _read_work_stamp
from taxjson.pipeline.messages import (
    _die, _out_wrap, _PROG, _run_concise, _say, _say_once,
)
from taxjson.pipeline.tools import _echo_captured, _write_work_stamp
from taxjson.model import money as M


# The config advertises `country = "canada | ca | usa | us"` and the gains
# engine accepts all four, but taxjson-corp-actions only knows the canonical
# names. Normalize once so an alias like "ca" doesn't abort the corp-actions
# stage at argparse (which restricts --country to RULES_BY_COUNTRY's keys).
# The one resolver lives in lib/country.py (partition audit R1): an
# unknown or missing country is refused everywhere with one message.


# (headline, detail) — `_say("note", *_US_EXPERIMENTAL_NOTE)`.
_US_EXPERIMENTAL_NOTE = (
    "the US engine is EXPERIMENTAL",
    "Its rules are implemented and unit-tested but have not been "
    "validated against a real account. Treat the output as a draft, and "
    "consider contributing a redacted export (`tjs redact`) so it "
    "can be.")


def _normalize_country(c: str) -> str:
    """Canonical canada|usa (lib/country.canonical_country); dies with
    the resolver's message on anything else."""
    from taxjson.lib.country import CountryError, canonical_country
    try:
        return canonical_country(c)
    except CountryError as e:
        _die(str(e))


def _home_currency(settings: Settings) -> str:
    """The currency the project's country files in (CAD / USD)."""
    from taxjson.lib.country import home_currency
    return home_currency(_country(settings))


def _base(settings: Settings) -> str:
    """[settings] base_currency, else the country's currency. The
    config readers refuse a base that is not the country's."""
    b = settings.base_currency
    return str(b).strip().upper() if b else _home_currency(settings)


def _tax_date(settings: Settings) -> str:
    """The date basis in force: [settings] tax_date, else the country
    default (lib/country.settings_tax_date)."""
    from taxjson.lib.country import CountryError, resolve_tax_date
    try:
        return resolve_tax_date(_country(settings), settings.tax_date)
    except CountryError as e:
        _die(str(e))


def _country_has_corp_rules(country: str) -> bool:
    """Whether taxjson-corp-actions has election rules for this
    jurisdiction (both countries do: RULES_BY_COUNTRY)."""
    from taxjson.lib.corp_actions import RULES_BY_COUNTRY
    return _normalize_country(country) in RULES_BY_COUNTRY


def _sheltered_elections(settings: Optional[Settings]) -> str:
    """[settings] sheltered_elections ("zero" | "ask"), refused by name
    when it is neither (lib/config_check refuses it first)."""
    from taxjson.lib.corp_actions import sheltered_elections_mode
    try:
        return sheltered_elections_mode(settings)
    except ValueError as e:
        _die(str(e))


def _sheltered_default_events(corp_files: List[Path]
                              ) -> Dict[str, Dict[str, str]]:
    """{event id: {date, kind (spinoff | merger), symbol}} of the events
    the corp stage files booked by the sheltered default
    (corp_actions.SHELTERED_DEFAULT: their rows carry the tag)."""
    import json as _json
    from taxjson.lib.corp_actions import SHELTERED_DEFAULT
    seen: Dict[str, Dict[str, str]] = {}
    for f in corp_files:
        try:
            rows = _json.loads(f.read_text(encoding="utf-8")).get(
                "transactions", [])
        except (OSError, ValueError, AttributeError):
            continue
        for r in rows:
            if not isinstance(r, dict) \
                    or r.get("corp_election") != SHELTERED_DEFAULT:
                continue
            eid = str(r.get("corp_event_id") or "?")
            ev = seen.setdefault(eid, {"date": str(r.get("date") or ""),
                                       "kind": "spinoff", "symbol": ""})
            if r.get("action") == "SPLIT":
                ev["kind"], ev["symbol"] = "merger", str(
                    r.get("symbol_new") or "")
            elif (r.get("action") == "BUYSELL" and not ev["symbol"]
                  and float(r.get("quantity") or 0) > 0):
                ev["symbol"] = str(r.get("symbol") or "")
    return seen


def _note_sheltered_defaults(name: str, corp_files: List[Path]) -> None:
    """One Info line per run for each spin-off or merger of a sheltered
    account booked without asking (corp_actions.SHELTERED_DEFAULT): the
    rows carry the tag, so a cached corp stage says it too. Not a
    warning, and not pending: no tax in the account depends on its cost
    (CA-CORP-11 / US-CORP-12)."""
    from taxjson.lib.corp_actions import sheltered_default_text
    seen = _sheltered_default_events(corp_files)
    for eid, ev in sorted(seen.items(), key=lambda x: (x[1]["date"], x[0])):
        _say_once(("sheltered-default", name, eid), "note",
                  sheltered_default_text(name, ev["kind"], ev["symbol"],
                                         ev["date"], eid),
                  indent="  ", file=sys.stdout)


def _warn_zero_value_spinoffs(name: str, is_taxable: bool,
                              corp_files: List[Path], cache: Path,
                              declared: Iterable[str] = ()) -> None:
    """A taxable spin-off booked at $0 with no value saved books no
    dividend income and a $0 cost for the new shares: a later sale
    overstates the gain by the same amount. It stays loud on EVERY run —
    on the console and, through a `.diag` sidecar, in the account's .sum
    — until a value is set (2026-09 audit: it was silent after the
    prompt). `declared`: the event ids whose election DECLARES the $0
    (`fmv_per_share=0` written by the user: corp_actions.
    declares_zero_value) — answered, so one Info line each (a `note:`
    in the sidecar, which the checklist does not count). Registered
    accounts: no tax effect, no warning."""
    import json as _json
    from taxjson.lib.corp_actions import (ALLOCATED_BASIS_HINT,
                                          declared_zero_value_text,
                                          zero_basis_rollover_rows,
                                          zero_value_merger_rows,
                                          zero_value_spinoff_rows)
    diag = cache / f"{name}_corp_spinoff_value.diag"
    lines: List[str] = []
    notes: List[str] = []
    declared = set(declared)
    if is_taxable:
        for f in corp_files:
            try:
                rows = _json.loads(f.read_text(encoding="utf-8")).get(
                    "transactions", [])
            except (OSError, ValueError):
                continue
            for r in zero_value_spinoff_rows(rows):
                eid = r.get("corp_event_id", "?")
                if eid in declared:
                    notes.append(declared_zero_value_text(
                        name, str(r.get("symbol") or ""),
                        str(r.get("date") or ""), eid))
                    continue
                lines.append(
                    f"warning: {name}: spin-off {r.get('symbol')} on "
                    f"{r.get('date')} (event {eid}) is booked at $0 — no "
                    f"dividend income and a $0 cost for the new shares, "
                    f"so a later sale overstates the gain by the same "
                    f"amount. Set its value: tjs elections {name} --set "
                    f"{eid}={r.get('corp_election')} --hint "
                    f"fmv_per_share=<value>")
            # A taxable merger at $0 (the "0 to defer" FMV, with or
            # without cash-in-lieu): a fake loss on the old shares and a
            # $0 cost for the new ones (audits S020-06, S074-00).
            for r in zero_value_merger_rows(rows):
                eid = r.get("corp_event_id", "?")
                lines.append(
                    f"warning: {name}: merger into {r.get('symbol')} on "
                    f"{r.get('date')} (event {eid}) is booked at $0 — the "
                    f"old shares' proceeds are only any cash-in-lieu (a "
                    f"fake loss) and the new shares cost $0. Set its "
                    f"value: tjs elections {name} --set "
                    f"{eid}={r.get('corp_election')} --hint "
                    f"fmv_per_share=<value>")
            # An s.86.1 / §355 rollover allocated $0: the parent keeps
            # its whole cost, the spin-off's sale books the gain
            # (audits S073-21, S074-04).
            for r in zero_basis_rollover_rows(rows):
                eid = r.get("corp_event_id", "?")
                el = r.get("corp_election")
                lines.append(
                    f"warning: {name}: spin-off {r.get('symbol')} on "
                    f"{r.get('date')} (event {eid}, {el}) is booked with "
                    f"$0 allocated cost — the parent keeps all of it and "
                    f"the spin-off's sale books the gain. Set it: tjs "
                    f"elections {name} --set {eid}={el} --hint "
                    f"{ALLOCATED_BASIS_HINT[el]}=<amount>")
    if lines or notes:
        _write_atomic(diag, "\n".join(lines + [f"note: {n}" for n in notes])
                            + "\n", encoding="utf-8")
        for ln in lines:
            _echo_captured(ln, file=sys.stderr)
    else:
        diag.unlink(missing_ok=True)
    for n in notes:
        _say_once(("declared-zero", name, n), "note", n, indent="  ",
                  file=sys.stdout)


def _warn_partial_carryovers(name: str, corp_files: List[Path],
                             base_json: Path, cache: Path) -> None:
    """A share exchange's cost carry-over moves the whole position (a
    SPLIT with no limit), so when the account held more than the broker
    exchanged the shares kept are booked as the new security too (TJ-0950,
    docs/limits.md). Said on every run and, through a `.diag` sidecar, in
    the account's .sum. No such event: nothing is read."""
    import json as _json
    from taxjson.lib.corp_views import _held_before_split
    diag = cache / f"{name}_partial_carryover.diag"
    exchanged: Dict[str, float] = {}
    for f in corp_files:
        try:
            meta = _json.loads(f.read_text(encoding="utf-8")).get(
                "metadata") or {}
        except (OSError, ValueError):
            continue
        for eid, qty in (meta.get("carryover_exchanged") or {}).items():
            exchanged[eid] = exchanged.get(eid, 0.0) + float(qty)
    lines: List[str] = []
    if exchanged:
        try:
            rows = _json.loads(base_json.read_text(encoding="utf-8")).get(
                "transactions", [])
        except (OSError, ValueError):
            rows = []
        # Two brokers of one account book one merger each: their shares
        # together are what was exchanged, out of one position.
        by_pair: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for r in rows:
            if r.get("action") == "SPLIT" and r.get("corp_event_id") in exchanged:
                by_pair.setdefault((str(r.get("symbol") or ""),
                                    str(r.get("symbol_new") or "")),
                                   []).append(r)
        for (old, new), splits in sorted(by_pair.items()):
            first = min(splits, key=lambda r: str(r.get("date") or ""))
            held = _held_before_split(rows, first)
            took = sum(exchanged[e] for e in
                       {str(r["corp_event_id"]) for r in splits})
            if held <= took + 1e-3:
                continue
            lines.append(
                f"warning: {name}: {old} to {new} on {first.get('date')} "
                f"moves all {held:g} shares held, but only {took:g} were "
                f"exchanged. A cost carry-over moves the whole position, so "
                f"the {held - took:g} kept are booked as {new}, and the "
                f"holdings and later gains of both are wrong. taxjson cannot "
                f"yet limit it to the shares exchanged (docs/limits.md). "
                f"Elect the event `ignore` (tjs elections {name} --set "
                f"{first['corp_event_id']}=ignore) and book the exchange by "
                f"hand in a .tt file.")
    if lines:
        _write_atomic(diag, "\n".join(lines) + "\n", encoding="utf-8")
        for ln in lines:
            _echo_captured(ln, file=sys.stderr)
    else:
        diag.unlink(missing_ok=True)


def _warn_expired_open_options(name: str, gains_json: Path, cache: Path,
                               year: Any,
                               tax_date: Optional[str] = None,
                               country: Optional[str] = None) -> None:
    """An option still open in the books after its expiry date: the
    export dropped the expiry / assignment / exercise row. For a LONG
    contract the premium paid is a capital loss of the expiry year that
    the books never realize (R1-37); option-boundary covers written
    contracts only. Loud on every run and, through a `.diag` sidecar,
    in the account's .sum. Cutoff: lib/option_boundary.expired_test,
    the one its twin `expired_open` (checklist, option-boundary) uses —
    expired by the later of the year end and the last date the books
    cover, and before today (audit A2-1210, A2-1112)."""
    import json as _json
    from datetime import date as _date
    from taxjson.lib.option_symbols import is_option_symbol, parse_option_expiry
    diag = cache / f"{name}_expired_options.diag"
    # An expiry ON Dec 31 is that year's (re-audit A2-0716): the year
    # end is inclusive, today exclusive (a contract expiring today may
    # still trade).
    _today = _date.today().isoformat()

    def _expired(exp: str) -> bool:
        return exp <= f"{year}-12-31" and exp < _today
    lines: List[str] = []
    try:
        inv = _json.loads(gains_json.read_text(encoding="utf-8")).get(
            "inventory") or []
    except (OSError, ValueError, AttributeError):
        inv = []
    _txs = None
    if any(is_option_symbol(str(h.get("symbol") or "")) for h in inv):
        try:
            from taxjson.lib.tx_load import load_transactions
            from taxjson.lib.option_boundary import expired_test
            _base = cache / f"{name}_base.json"
            if _base.exists():
                _txs = load_transactions(_base)
                _expired = expired_test(_txs, int(year), tax_date=tax_date)
        except (OSError, ValueError, TypeError):
            _txs = None
    # Positions the broker says were opened BEFORE the data (IB code C on
    # the trade that opened them in the books): the missing row is the
    # purchase, not the expiry (audit S013-00).
    _closing: set = set()
    # One contract under two roots (a CLOSING row booked on RCX.B while
    # the books hold RCX, audit A2-0266): both legs stay open past the
    # expiry, and the fix is the ticker.map line, not a missing row.
    _redescribed: Dict[str, str] = {}
    if any(is_option_symbol(str(h.get("symbol") or "")) for h in inv):
        try:
            from taxjson.lib.option_boundary import expired_open
            from taxjson.lib.option_close_check import (
                unbacked_option_closes)
            if _txs is not None:
                _closing = {x["symbol"] for x in expired_open(
                    _txs, int(year), tax_date=tax_date, country=country)
                    if x.get("broker_closing")}
                for _f in unbacked_option_closes(_txs):
                    if len(_f["partners"]) == 1:
                        _p = _f["partners"][0][0]
                        _fix = f"GLOBAL {_p} {_f['symbol']}"
                        _redescribed.setdefault(_f["symbol"], _fix)
                        _redescribed.setdefault(_p, _fix)
        except (OSError, ValueError, TypeError):
            _closing = set()
    from taxjson.model.result import Holding
    for h in inv:
        sym = str(h.get("symbol") or "")
        try:
            qty = float(Holding(h).quantity or 0.0)
        except ValueError:      # ResultShapeError: not a number
            continue
        # A 400-digit quantity reads as inf: left out, as a text one is
        # (backlog TJ-3046).
        if not math.isfinite(qty):
            continue
        if abs(qty) < 1e-9 or not is_option_symbol(sym):
            continue
        exp = parse_option_expiry(sym)
        if not exp or not _expired(exp):
            continue
        side = "long" if qty > 0 else "written"
        if sym in _redescribed:
            lines.append(
                f"warning: {name}: {sym} expired {exp} and the books still "
                f"hold {qty:g} ({side}): a CLOSING row was booked under "
                f"another root of the same contract (see the ATTENTION "
                f"line) — add to ticker.map:  {_redescribed[sym]}  — not "
                f"a missing row.")
            continue
        if sym in _closing:
            lines.append(
                f"warning: {name}: {sym} expired {exp} and the books still "
                f"hold {qty:g} ({side}), but the broker coded the trade "
                f"that opened it CLOSING (IB code C / RBC CLOSE CONTRACT): "
                f"it closed a position "
                f"opened before the data — add the missing "
                f"{'write' if qty > 0 else 'purchase'} (`tjs "
                f"find-missing-history`), not an expiry row.")
            continue
        cost = _held_cost(h) if qty > 0 else None
        lines.append(
            f"warning: {name}: {sym} expired {exp} but the books still "
            f"hold {qty:g} ({side}) — the export is missing its expiry, "
            f"assignment or exercise row"
            + (f"; the {M.shown(abs(cost), 2):,.2f} paid "
               f"is a loss of {exp[:4]} that is not booked"
               if cost is not None else "")
            + ". Add the missing row (an expiry is a BUYSELL closing "
              "the position at 0 on the expiry date) and re-run.")
    if lines:
        _write_atomic(diag, "\n".join(lines) + "\n", encoding="utf-8")
        for ln in lines:
            _echo_captured(ln, file=sys.stderr)
    else:
        diag.unlink(missing_ok=True)


def _held_cost(h: Dict[str, Any]) -> Optional[float]:
    """A holding's cost as the expired-option warning shows it, or None
    when it is not a finite number (the warning then leaves it out:
    backlog TJ-3045, TJ-3052)."""
    from taxjson.model.result import Holding
    try:
        cost = float(Holding(h).total_cost or 0.0)
    except ValueError:      # ResultShapeError: not a number
        return None
    return cost if math.isfinite(cost) else None


def _refuse_crypto_openings(name: str, tt_jsons, parsed_by) -> None:
    """An opening position in a crypto account (a .tt OPENING line, or a
    generic mapping's `opening` row in the parsed files of `parsed_by`'s
    generic groups: the two sources of one; the exchange exports never
    carry one, so their large files are not read): the crypto books have
    no merge2 stage, so the snapshot cut-off (lib/opening) would not run
    and the coins before it would count twice. Refused, naming the
    file."""
    import json as _json
    from taxjson.lib.row_types import is_opening_row
    generic = [p for b, p in parsed_by.items() if _is_generic_group(b)]
    for p in list(tt_jsons) + generic:
        try:
            rows = _json.loads(Path(p).read_text(
                encoding="utf-8")).get("transactions") or []
        except (OSError, ValueError, AttributeError):
            continue
        if any(isinstance(t, dict) and is_opening_row(t) for t in rows):
            _die(f"account {name} is a crypto account: an opening position "
                 f"({Path(p).name}: a .tt OPENING line or a mapping's "
                 f"opening row) is for share accounts only — enter the "
                 f"coins' purchases as BUYSELL lines instead.")


def detect_broker(csv_path: Path) -> Optional[str]:
    """Which parser reads this CSV (lib/brokerages/detect.detect): an
    explicit generic mapping, else the file's CONTENT (each supported
    export's header signature, from the parser's own required columns),
    else — only when no content signature matched — the cb_/kr_/generic_
    prefix or the coinbase/kraken word in the name. None when nothing
    routes it. A file whose content matches two exports raises
    AmbiguousBroker."""
    from taxjson.lib.brokerages.detect import detect
    return detect(csv_path).broker


def _shown_account_dir(account_dir: Path, member: Path) -> str:
    """An account's inputs folder as the console names it, relative to
    the project `member` (a file in it) belongs to: inputs/<account>,
    or ../inputs/<account> for exports shared by every year (backlog
    TJ-0253; the detection lines' form, TJ-0133)."""
    root = _PL.project_of_input(member)
    if root is None:
        return f"inputs/{account_dir.name}"
    try:
        return _PL.shown(Path(account_dir).resolve(), Path(root).resolve())
    except OSError:
        return f"inputs/{account_dir.name}"


def _redacted_copies_warning(account_dir: Path) -> None:
    """One warning per NAME.redacted.EXT copy beside the exports it was
    made from, which input_files leaves out: the run booked its rows a
    second time, under placeholder ids, without a word (backlog
    TJ-0476)."""
    from taxjson.lib.out import one_line as _one_line
    try:
        kids = sorted(account_dir.iterdir())
    except OSError:
        return
    for p in kids:
        if (p.suffix.lower() not in (".csv", ".tt")
                or not _PL.redacted_copy_beside_exports(p)):
            continue
        _say_once(("redacted-copy", str(p)), "warning",
                  f"{_shown_account_dir(account_dir, p)}/"
                  f"{_one_line(p.name)} is not read: a copy `tjs "
                  f"redact` made of the export beside it",
                  "Read too, its rows would be booked a second time "
                  "under placeholder ids. Move it out of the inputs "
                  "folder (`tjs redact --out DIR FILE` writes the "
                  "copy elsewhere).", prog=_PROG)


def group_inputs(account_dir: Path) -> Dict[str, List[Path]]:
    """{parser id: [csv]} for an account's inputs folder."""
    return group_inputs_detailed(account_dir)[0]


def group_inputs_detailed(account_dir: Path):
    """({parser id: [csv]}, [Detection]) — every CSV of the folder and
    how it was routed (lib/brokerages/detect). Stops the run on a file
    that cannot be read, is empty, is not UTF-8/UTF-16, matches two
    exports, or matches nothing."""
    from taxjson.lib.brokerages.detect import AmbiguousBroker, detect
    from taxjson.bin.taxjson_detect_brokerage import cannot_detect_message
    out: Dict[str, List[Path]] = {}
    found = []
    for csv in input_files(account_dir, ".csv"):
        # The file as the run names inputs (inputs/<account>/<file>), the
        # fix a detail line: the absolute path pushed the headline under
        # a bare "Error:" (backlog TJ-0123). The folder as the console
        # names it: ../inputs/<account>/ for exports shared by every
        # year (backlog TJ-0253).
        _in_acct = _shown_account_dir(account_dir, csv)
        _shown_csv = f"{_in_acct}/{csv.name}"
        # An unreadable or empty file got the "rename it to cb_/kr_/
        # generic_" advice; renaming never helps (re-audit A2-0713,
        # A2-1228).
        try:
            with open(csv, "rb") as _fh:
                _head = _fh.read(4096)
        except PermissionError as e:
            # The chmod names the file from where the command was run
            # (backlog TJ-0218).
            _die(f"cannot read {_shown_csv}: "
                 f"{e.strerror or 'Permission denied'}",
                 f"`chmod u+r {_config_shown(csv)}` lets taxjson read it "
                 f"(or remove the file).")
        except OSError as e:
            from taxjson.lib.cli_diag import describe_input_error
            _die(f"{describe_input_error(e)} — taxjson cannot read this "
                 f"input; fix its permissions (or remove it).")
        if not _head.strip(b" \t\r\n\xef\xbb\xbf\x00"):
            _die(f"{_shown_csv} is empty — a failed or interrupted "
                 f"download?",
                 "Download the export again (or remove the file).")
        try:
            det = detect(csv)
        except AmbiguousBroker as e:
            _die(str(e))
        if det.positions:
            # A positions report (what is held on a date, not activity —
            # lib/positions_reports) dropped into inputs/: listed with
            # the detection lines and skipped, never parsed as trades
            # and never a failed run. `tjs sanity` / `tjs
            # opening` read it.
            found.append(det)
            continue
        if not det.broker:
            # Content detection cannot read a cp1252 re-save: the
            # rename advice below pointed at the wrong fix (S024-03).
            try:
                _raw = csv.read_bytes()
                if _raw[:2] not in (b"\xff\xfe", b"\xfe\xff"):
                    _raw.decode("utf-8-sig")
            except UnicodeDecodeError as e:
                from taxjson.lib.cli_diag import not_utf8_headline, utf8_fix
                _fix = utf8_fix(csv)
                _die(not_utf8_headline(_shown_csv, e),
                     _fix[:1].upper() + _fix[1:] + ".")
            except OSError:
                pass
            # IB's dividends report (a slip source, not activity) in an
            # account's folder: said so, its name masked (IB names it
            # after the account number).
            from taxjson.lib.ib_dividends import is_dividends_report
            if is_dividends_report(csv):
                from taxjson.lib.brokerages.base import shown_name
                _die(f"{_in_acct}/{shown_name(csv)} is IB's "
                     f"dividends report (the T5 / T3 income per payment), "
                     f"not an activity export — move it to inputs/slips/, "
                     f"where `tjs slip-audit` reads it.")
            # On the console: the file as it is on disk (an export's
            # default name is its account number; masking it here hid
            # which of two such files is meant).
            from taxjson.lib.out import one_line as _one_line
            _die(cannot_detect_message(
                det, shown=f"{_in_acct}/{_one_line(csv.name)}"))
        out.setdefault(det.broker, []).append(csv)
        found.append(det)
    _redacted_copies_warning(account_dir)
    # A [[holding]] TOML in the folder (a positions file, not a generic
    # mapping sidecar `<file>.csv.toml` nor the folder's generic.toml)
    # is never read by the run either: listed the same way, so its
    # silence does not read as "taken into account".
    from taxjson.lib.brokerages.detect import Detection
    from taxjson.lib.positions_reports import (_is_holdings_toml,
                                               read_positions)
    for tml in input_files(account_dir, ".toml"):
        low = tml.name.lower()
        if low.endswith(".csv.toml") or low == "generic.toml":
            continue
        if not _is_holdings_toml(tml):
            continue
        try:
            _asof = read_positions(tml).as_of
        except Exception:
            _asof = ""
        found.append(Detection(
            tml, None, "positions",
            "holdings TOML" + (f", as of {_asof}" if _asof else ""),
            positions="holdings_toml"))
    return out, found


def _read_dated_events(root: Path, accounts: Dict[str, Any],
                       tmap: Any) -> Any:
    """The project's .tt JOURNAL and RENAME lines (lib/dated_events),
    checked against ticker.map; a line that cannot be booked stops the
    run with its message (it names the form). Said once: the notes (a
    rename declared twice) and warnings."""
    from taxjson.lib import dated_events as DE
    # The record of the last run's events goes until this run writes its
    # own (_write_dated_events_state, at the end): a run that stops —
    # here or in any later stage — never leaves the last one's record
    # describing books it did not build.
    try:
        (root / "work" / DE.STATE).unlink()
    except OSError:
        pass
    try:
        decl = DE.read_declarations(root, accounts)
        problems = DE.check_against_map(decl, tmap)
    except DE.DatedEventError as e:
        problems = str(e).splitlines()
    else:
        # Two identical JOURNAL lines are one journal (a Warning).
        from taxjson.lib.cross_listings import declared_twins
        decl.journals, _twins = declared_twins(decl.journals)
        decl.warnings += _twins
    if problems:
        _die(f"{len(problems)} .tt dated-event line(s) cannot be booked",
             *[f"- {p}" for p in problems],
             "A JOURNAL line is `JOURNAL <date> <FROM> <TO> <qty>`, a "
             "RENAME line `RENAME <date> <OLD> <NEW> "
             "[late=fold|late=separate]` (date first). Fix the line or "
             "delete it.")
    for n in decl.notes:
        _say("note", *_split_msg(n), prog=_PROG)
    for w in decl.warnings:
        _say("warning", *_split_msg(w), prog=_PROG)
    run_state.current.dated_events[(root / "work").resolve()] = decl
    return decl


def _read_loss_overrides(root: Path, accounts: Dict[str, Any]) -> None:
    """The project's .tt ALLOWLOSS lines (lib/loss_overrides), written to
    work/loss_overrides.json before any gains pass reads them; a line that
    cannot be used stops the run with its message (it names the form)."""
    from taxjson.lib import loss_overrides as LO
    from taxjson.lib import loss_override_note as LON
    try:
        items = LO.read_project(root, accounts)
    except LO.LossOverrideError as e:
        probs = str(e).splitlines()
        _die(f"{len(probs)} .tt {LON.KEYWORD} line(s) cannot be used",
             *[f"- {p}" for p in probs],
             f"The form is `{LON.FORM}`, date first, in the taxable "
             f"account that sold; the reason is required. Fix the line or "
             f"delete it.")
    LO.write_state(root / "work", items)


def _lock_files(root: Path, settings: Settings) -> List[Path]:
    """The filed-year locks a US gains run depends on (US-WASH-22): a
    new or removed lock rebuilds the cached gains."""
    if _country(settings) not in ("us", "usa"):
        return []
    from taxjson.lib.filed_locks import project_locks
    try:
        return [p for _y, p, _w in project_locks(root, settings)]
    except ValueError:
        return []


def _loss_override_flags(cache: Path) -> List[str]:
    """--loss-overrides for an engine run of the project's books ([] when
    the project has no ALLOWLOSS line)."""
    from taxjson.lib.loss_overrides import flags
    return flags(cache)


def _say_loss_overrides(root: Path, settings: Settings, cache: Path,
                        *, full: bool) -> None:
    """ONE warning per run listing every filing position taken against
    the loss rule (CA-SL-18 / US-WASH-25), read from the final gains
    files. On a full run, a line that names no denied loss, or several,
    stops the run (each named)."""
    from taxjson.lib import loss_overrides as LO
    from taxjson.lib import loss_override_note as LON
    from taxjson.lib.report_model import resolve_gains_files
    expected = LO.read_state(cache)
    if not expected:
        return
    accts = {o["account"] for o in expected}
    files = {a: p for a, p in resolve_gains_files(cache).items()
             if a in accts}
    items = LO.gather(files)
    country = _normalize_country(settings.country)
    rule = ("wash sale" if country in ("us", "usa")
            else "superficial loss")
    if full:
        # ticker.map's spellings: a line naming a sale the books spell
        # another way says how (LO.spelling_hint).
        probs = LO.problems(items, expected, spellings=LO.map_view(root))
        if probs:
            _die(f"{len(probs)} .tt {LON.KEYWORD} line(s) name no single "
                 f"denied {rule}",
                 *[f"- {p}" for p in probs],
                 f"Each line names ONE sale the rule denies: its date "
                 f"(trade or settlement), its symbol as `tjs "
                 f"wash-sales` spells it and, when two such sales share "
                 f"the day, the units sold (`{LON.FORM}`).")
    if not any(it.get("status") == LO.STATUS_APPLIED for it in items):
        return
    head, details = LO.warning_message(
        items, "usa" if country in ("us", "usa") else "canada")
    _say_once(("lossoverride",), "warning", head, *details, indent="  ",
              file=sys.stdout)


def _dated_effective_lines(cache: Path) -> List[str]:
    """The lines the run's effective map carries for the dated renames
    the project declares (lib/dated_events.effective_lines: the .tt
    events, and the merged facts of a legacy map line's event)."""
    from taxjson.lib import dated_events as DE
    decl = run_state.current.dated_events.get(cache.resolve())
    if decl is None:
        return []
    return DE.effective_lines(decl.events or decl.renames)


# The parsers that say a look-alike ticker change (a symbol stops, one of
# the same name starts short): RBC, Questrade and Webull.
_LOOKALIKE_RENAME_BROKERS = ("rbc_direct", "questrade", "webull")


def _parse_reads(cmd: List[str], acct_dir: Path, reads: List[Path]
                 ) -> Tuple[Any, ...]:
    """What a broker parse reads: its command, and the name, size and
    SHA-256 of every file it opens — `reads` (its inputs, maps, rates)
    and every CSV and mapping file of the account's folder (a parser
    reads a sibling export: a Kraken ledger beside its trades)."""
    files = list(reads)
    try:
        files += [p for p in acct_dir.iterdir()
                  if p.suffix.lower() in (".csv", ".toml")]
    except OSError:
        pass
    return (tuple(cmd), _inputs_fingerprint(
        [p for p in files if p.is_file()]))


def _parse_outputs(out: Path) -> Tuple[Any, ...]:
    """A broker parse's outputs as they are on disk: the books, its
    .diag, its transfers sidecar, its overrides log, its cash events —
    present or not, each by inode, size and mtime."""
    from taxjson.lib.cash_events import sidecar_path
    state = []
    for p in (out, out.with_name(out.name + ".diag"),
              out.with_name(out.stem + "_transfers.json"),
              out.with_name(out.stem + ".overrides"), sidecar_path(out)):
        try:
            st = p.stat()
            state.append((p.name, st.st_ino, st.st_size, st.st_mtime_ns))
        except OSError:
            state.append((p.name, None))
    return tuple(state)


def _reused_parse(out: Path, reads: Tuple[Any, ...]) -> bool:
    """True when this run already made the parse `out` from the same
    `reads` (_parse_reads) and its outputs are as it left them."""
    seen = run_state.current.parsed.get(out)
    return seen is not None and seen == (reads, _parse_outputs(out))


def _report_detection(name: str, found, cache: Path) -> None:
    """Print one line per input CSV — how its broker was detected — and
    persist them to work/<acct>_detect.diag, whose `note:` lines (the
    name disagreed with the content, a name-only routing) reach the
    account's .sum DIAGNOSTICS. The console names each file as it is on
    disk (two exports whose names mask alike, 55500001.csv and
    55500001_2.csv, must be told apart there); the saved .diag, like
    every saved diagnostic, masks account-number-like parts
    (shown_name)."""
    from taxjson.lib.brokerages.base import shown_name
    from taxjson.lib.out import one_line
    lines: List[str] = []
    notes: List[str] = []
    console: List[Tuple[str, str]] = []
    for det in found:
        masked = shown_name(det.path)
        # A control character in the name is escaped (one_line): a
        # newline in it must not start a line of its own.
        on_disk = one_line(det.path.name)
        lines.append(det.line(f"inputs/{name}/{masked}"))
        console.append(("  ", det.line(f"inputs/{name}/{on_disk}")))
        if det.note:
            notes.append(f"note: {det.note}")
            real = (det.note.replace(masked, on_disk, 1)
                    if det.note.startswith(masked) else det.note)
            console.append(("    ", f"note: {real}"))
    from taxjson.lib.out import show as _show, width as _width
    if (name not in run_state.current.detection_shown
            and _width(sys.stdout) > 0):
        run_state.current.detection_shown.add(name)
        # Shown to a person (The run's console): one `Info:` line per
        # file, what it was identified as — how (the content's header)
        # stays in work/<acct>_detect.diag; then the notes. The file is
        # named where it is (../inputs/... with exports shared by every
        # year: backlog TJ-0133); the .diag keeps its bytes.
        _in = _PL.shown(_PL.inputs_dir(cache.parent), cache.parent)
        for det in found:
            on_disk = one_line(det.path.name)
            where = f"{_in}/{name}/{on_disk}"
            if det.positions:
                text = (f"Info: File {where} is a positions report, not "
                        f"activity: skipped (`tjs sanity` and "
                        f"`tjs opening` read it)")
            elif det.broker:
                if _run_concise():
                    # The broker each file was read as: --details (and
                    # work/<acct>_detect.diag); the default console
                    # keeps the per-file counts (Essentials first).
                    run_state.current.details_hidden = True
                    continue
                text = f"Info: File {where} → identified as {det.display}"
            else:
                text = (f"Warning: File {where} → not identified"
                        + (f" ({det.reason})" if det.reason else ""))
            _show(_out_wrap(text, hang=""), sys.stdout)
        for ind, ln in console:
            if ind != "  ":
                _show(_out_wrap(_labelled_note(ln), hang=""), sys.stdout)
    elif name not in run_state.current.detection_shown:
        run_state.current.detection_shown.add(name)
        # Captured (width 0: the branch above is the shown one): lines
        # first, then the notes, as the .diag keeps them, in these bytes.
        from taxjson.lib.out import labelled as _labelled, show as _show
        for ind, ln in ([c for c in console if c[0] == "  "]
                        + [c for c in console if c[0] != "  "]):
            _show(_out_wrap(_labelled(ln), indent=ind, hang="      "),
                  sys.stdout)
    _txt = "".join(f"{x}\n" for x in lines + notes)
    _diag = cache / f"{name}_detect.diag"
    if _read_work_stamp(_diag) != _txt:
        _write_work_stamp(_diag, _txt)


def _labelled_note(line: str) -> str:
    """A `note: ...` line as a person is shown it (lib/out.labelled)."""
    from taxjson.lib.out import labelled
    return labelled(line)


def _is_generic_group(broker: str) -> bool:
    return broker == "generic" or broker.startswith("generic-")


def _mapping_choice(csv_path: Path):
    """The generic importer's choice of mapping for a file (its path, how
    and the files the choice depends on), the parse stage's view: a
    choice the importer refuses (a dangling sidecar, two fitting
    layouts) is reported by the parse, and counts here as the sidecar."""
    from taxjson.lib.brokerages.detect import AmbiguousBroker, mapping_choice
    from taxjson.lib.brokerages.generic import MappingChoice
    try:
        return mapping_choice(csv_path)
    except AmbiguousBroker:
        return MappingChoice.refused(csv_path)


def _layout_files(root: Path) -> List[Path]:
    """Every layout file a CSV of the project could be read with
    (generic.layout_files over layout_dirs_of): their presence and
    content are inputs of the parse."""
    from taxjson.lib.brokerages.generic import layout_dirs_of, layout_files
    return layout_files(layout_dirs_of(root))


def _generic_files(grouped: Dict[str, List[Path]]) -> List[Path]:
    return [p for b, ps in grouped.items() if _is_generic_group(b)
            for p in ps]


def _split_generic_groups(grouped: Dict[str, List[Path]]
                          ) -> Dict[str, List[Path]]:
    """generic_*.csv files whose mapping names the real broker
    (`[broker] name = "wealthsimple"`) are parsed per broker, in a
    `generic-<name>` group (work/<acct>_generic-<name>.json, recorded as
    source_brokerage generic:<name>), so the fees report attributes each
    broker instead of one 'generic' bucket (audit S027-05). Unnamed
    mappings stay in the plain `generic` group."""
    files = grouped.get("generic")
    if not files:
        return grouped
    from taxjson.lib.brokerages.generic import mapping_broker_name
    out: Dict[str, List[Path]] = {}
    for b, ps in grouped.items():
        if b != "generic":
            out[b] = ps
            continue
        for p in ps:
            try:
                nm = mapping_broker_name(p)
            except ValueError:
                nm = None       # the parse stage reports the mapping error
            out.setdefault(f"generic-{nm}" if nm else "generic",
                           []).append(p)
    return out


def _raw_mixed_currency_symbols(raw_json: Path) -> List[str]:
    """Symbols whose NATIVE-currency pool would mix currencies —
    following SPLIT renames. A cross-currency rollover (e.g. ABG.TO
    [CAD] --s.85.1(5)--> ABH.US [USD]) renames the CAD pool onto a
    symbol whose later trades are USD; the raw (unconverted) gains
    stage then dies with a currency-mismatch error and killed the
    whole run. Such positions are unrepresentable in the native view
    BY CONSTRUCTION — the caller skips the raw stage with a warning
    instead (the converted, tax-authoritative books are unaffected)."""
    import json as _json
    try:
        txs = _json.loads(raw_json.read_text(
            encoding="utf-8")).get("transactions", [])
    except (OSError, ValueError):
        return []
    renames: Dict[str, str] = {}
    for t in txs:
        if (t.get("action") == "SPLIT" and t.get("symbol_new")
                and t["symbol_new"] != t.get("symbol")):
            renames[t["symbol"]] = t["symbol_new"]

    def _final(sym: str) -> str:
        seen = set()
        while sym in renames and sym not in seen:
            seen.add(sym)
            sym = renames[sym]
        return sym

    curs: Dict[str, set] = {}
    for t in txs:
        # SPLIT rows are not counted: they carry no money, and a .tt
        # SPLIT is stamped CAD whatever the listing — a USD stock's
        # split then looked like a mixed-currency pool and the holdings
        # refresh was skipped with a misleading "rollover rename"
        # message (R1-126). A rename's currency mix still shows through
        # the trades on either side of it (followed via `renames`).
        # ADJUST / DISALLOW rows carry money onto the pool too: a
        # CAD-valued corporate-action ADJUST on a USD pool (an s.86.1
        # allocation with no rate to express it in USD — A2-0002), or a
        # USD return of capital on a CAD listing that
        # _raw_align_adjust_currency could not price (A2-0055/0191/0204),
        # would stop the native gains pass.
        # Every action the engines' currency guards check (an
        # OPENING_BALANCE too; a TRANSFER is never checked) — the list
        # here missed OPENING_BALANCE and flagged TRANSFER (audit
        # A2-0440). lib/row_types.POOL_FREE_ACTIONS is the one list.
        from taxjson.lib.row_types import POOL_FREE_ACTIONS
        if t.get("action") in POOL_FREE_ACTIONS:
            continue
        c, sym = t.get("currency"), t.get("symbol")
        if c and sym:
            curs.setdefault(_final(sym), set()).add(c)
    return sorted(sym for sym, cs in curs.items() if len(cs) > 1)


def _raw_align_adjust_currency(raw_json: Path, rates_path: Path,
                               base_currency: str) -> List[str]:
    """Restate, in the pool's own currency, every ADJUST / DISALLOW row of
    the NATIVE-currency raw merge that is in another currency: a USD
    return of capital on a TSX listing (RBC, Questrade, IB), a CAD T3
    box-42 ADJUST on a USD unit (docs/tax-rules.md recipe); and a purchase booked
    under a listing of another currency than its cash (its
    `listing_currency`: a Questrade REI paid on the USD side buying the
    account's TSX listing, GitHub issue #3), price and amounts alike. The native gains pass
    pools one currency per symbol and used to stop the whole run at
    "raw gains" with advice a project user cannot act on (audit
    A2-0055/0191/0204). The amount is converted through the base
    currency at the row's date (the same Bank of Canada / base rates the
    filing books use); the converted, tax-authoritative books are
    untouched — they convert every row anyway. A row with no rate is
    left as is (the raw view is then skipped with a note). Returns one
    line per restated row for the console."""
    import json as _json
    from decimal import Decimal as _D
    try:
        doc = _json.loads(raw_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    txs = doc.get("transactions", []) if isinstance(doc, dict) else []

    def _listed(t) -> str:
        # A row booked under a listing of another currency than its
        # cash (a Questrade REI paid in USD buying the TSX listing,
        # TaxTransaction.listing_currency): the pool's currency is the
        # listing's.
        lc = str(t.get("listing_currency") or "").upper()
        return lc if lc and lc != t.get("currency") else ""
    pool_cur: Dict[str, set] = {}
    from taxjson.lib.row_types import is_opening_row
    for t in txs:
        if t.get("action") in ("BUYSELL", "ASSIGN", "TRANSFER",
                               "OPENING_BALANCE") and t.get("currency") \
                and not is_opening_row(t):
            pool_cur.setdefault(t.get("symbol"), set()).add(
                _listed(t) or t["currency"])
    # An OPENING in the project's base currency (`tjs opening` from a
    # holdings file's base_total_cost) on a listing of another currency:
    # restated too, or it would drop the account's whole native view
    # (backlog TJ-1169). An opening in another currency is not restated: a
    # USD line on a TSX USD unit is already in the holding's currency
    # (TJ-1298). With no trade of the symbol the pool's currency is that
    # line's, so an ADJUST follows it (TJ-1329); with only a base-currency
    # line, the listing's when its suffix tells, else the line's.
    from taxjson.lib.markets import suffix_currency
    from taxjson.lib.markets_data import suffix_of
    for t in txs:
        if is_opening_row(t) and t.get("currency") \
                and t.get("symbol") not in pool_cur:
            pool_cur[t.get("symbol")] = None
    for sym in [s for s, v in pool_cur.items() if v is None]:
        cur = suffix_currency(suffix_of(sym or ""))
        lines = {t["currency"] for t in txs if is_opening_row(t)
                 and t.get("symbol") == sym and t.get("currency")}
        pool_cur[sym] = ({c for c in lines
                          if c.upper() != (base_currency or "").upper()}
                         or ({cur} if cur else lines))
    todo = [t for t in txs
            if (t.get("action") in ("ADJUST", "DISALLOW")
                or (t.get("action") == "BUYSELL" and _listed(t))
                or (is_opening_row(t) and str(t.get("currency")).upper()
                    == (base_currency or "").upper()))
            and t.get("currency")
            and len(pool_cur.get(t.get("symbol"), ())) == 1
            and t["currency"] not in pool_cur[t.get("symbol")]]
    if not todo:
        return []
    _rate = _raw_rate_lookup(rates_path, base_currency)
    if _rate is None:
        return []
    notes = []
    for t in todo:
        src, dst = t["currency"], next(iter(pool_cur[t["symbol"]]))
        day = t.get("date") or ""
        r_src, r_dst = _rate(src, day), _rate(dst, day)
        if not r_src or not r_dst:
            continue
        f = r_src / r_dst
        keys = ("net_amount", "gross_amount") + (
            ("price", "commission", "fee") if t["action"] == "BUYSELL"
            else ())
        for k in keys:
            if t.get(k) not in (None, ""):
                t[k] = float(_D(str(t[k])) * f)
        t["currency"] = dst
        _what = "OPENING" if is_opening_row(t) else t["action"]
        notes.append(f"{t['symbol']} {_what} {day}: {src} amount "
                     f"restated as {M.shown(t['net_amount'], 2):,.2f} {dst} for the "
                     f"native-currency holdings view")
    if notes:
        from taxjson.lib.safe_write import write_atomic
        write_atomic(raw_json, _json.dumps(doc, indent=2))
    return notes


def _raw_rate_lookup(rates_path: Path, base_currency: str):
    """rate(currency, day): one unit of `currency` in the base currency
    on `day` (YYYY-MM-DD) from the run's rate file, the prior business
    day's when the day has none (as taxjson-convert-currency reads it);
    None when the file has none near it. None (no lookup) when the file
    cannot be read."""
    from datetime import timedelta
    from decimal import Decimal as _D
    from taxjson.lib.exchange_rates import load_exchange_rates
    base = (base_currency or "").upper()
    try:
        hist = load_exchange_rates(rates_path, target_curr=base)
    except (OSError, ValueError):
        return None

    def _rate(cur: str, day: str):
        if cur == base:
            return _D(1)
        h = hist.get(cur) or {}
        try:
            d0 = datetime.strptime(day, "%Y-%m-%d")
        except (TypeError, ValueError):
            return None
        for i in range(6):           # prior business day, as convert does
            k = (d0 - timedelta(days=i)).strftime("%Y-%m-%d")
            if k in h:
                return h[k]
        return None
    return _rate


def _raw_net_withholding(raw_json: Path, rates_path: Path,
                         base_currency: str) -> List[str]:
    """Net, in the NATIVE-currency raw merge, a dividend's withholding
    paid in another currency (a .tt USD DIVIDEND with a same-day CAD
    TAX): the TAX converted at its own day's rate (the run's rates, as
    the base books convert it) before it comes off the dividend's
    net_amount (merge2's reconcile_dividend_tax). The native merge has
    no rates, so merge2 leaves such a TAX out of the net; it used to
    subtract 2.70 CAD from 10 USD (backlog TJ-0618). Idempotent: the
    file is written only when a net changes. Returns the console notes
    (a pair with no rate)."""
    import json as _json
    from taxjson.bin.taxjson_merge2 import reconcile_dividend_tax
    try:
        doc = _json.loads(raw_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    txs = doc.get("transactions") if isinstance(doc, dict) else None
    if not isinstance(txs, list):
        return []
    rows = [t for t in txs if isinstance(t, dict)]
    by_day: Dict[Tuple[Any, Any], set] = {}
    for t in rows:
        if str(t.get("action") or "").upper() in ("DIVIDEND", "TAX"):
            by_day.setdefault((t.get("symbol"), t.get("date")), set()).add(
                str(t.get("currency") or "").upper())
    if not any(len(c - {""}) > 1 for c in by_day.values()):
        return []
    _rate = _raw_rate_lookup(rates_path, base_currency)
    before = [t.get("net_amount") for t in rows]
    res = reconcile_dividend_tax(rows, rate=_rate)
    if [t.get("net_amount") for t in rows] != before:
        from taxjson.lib.safe_write import write_atomic
        write_atomic(raw_json, _json.dumps(doc, indent=2))
    return [f"{sym} DIVIDEND {day}: no {tcur}/{dcur} rate on file — its "
            f"{tcur} withholding is left out of the native-currency "
            f"dividend's net (the base books are unaffected)"
            for sym, day, tcur, dcur in res["unconverted"]]


def _account_sidecars(cache: Path, name: str,
                      sibling_prefixes: Iterable[str]) -> List[Path]:
    """The account's transfer evidence: work/<account>_<broker>_
    transfers.json (the TRANSFER rows an account without `transfers =
    true` keeps out of its books) and the legs of its .tt JOURNAL lines
    (work/<account>_tt-journal_transfers.json) — not a longer account
    name's files (`sibling_prefixes`), nor a converted .tt."""
    out = []
    for sc in sorted(cache.glob(f"{name}_*_transfers.json")):
        if any(sc.name.startswith(pre) for pre in sibling_prefixes):
            continue                 # another account's sidecar
        if sc.name.startswith(f"{name}_tt_"):
            continue                 # a converted .tt, not a sidecar
        out.append(sc)
    return out


def _raw_book_journals(raw_json: Path, sidecars: List[Path],
                       ticker_map: Optional[Path], rates_path: Path,
                       base_currency: str) -> List[str]:
    """Write the account's journals between two listings of one
    security into its NATIVE-currency merge (lib/listing_journals,
    backlog TJ-0361): the legs its own TRANSFER rows hold (`transfers =
    true`) and its transfer evidence holds (`sidecars`), paired within
    the map's identity classes, become two `listing_journal` rows that
    move the units with their cost on the journal's date. The holdings
    export used to move them after the gains (a later sale of the new
    listing read as a short) and an account keeping its transfers in
    the books booked the legs as a sale and a purchase at their market
    value. Idempotent (a merge already written is left alone). Returns
    the notes for the .diag (a far-apart pair of legs, a missing rate)."""
    import json as _json
    if ticker_map is None:
        return []
    from taxjson.bin.taxjson_export import _evidence_rows
    from taxjson.lib.ticker_map_rules import map_symbol, merge_renames
    from taxjson.lib.map_file import load_map_file
    from taxjson.lib.listing_journals import book_journals, identity_find
    try:
        doc = _json.loads(raw_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    txs = doc.get("transactions") if isinstance(doc, dict) else None
    if not isinstance(txs, list):
        return []
    try:
        tmap = load_map_file(Path(ticker_map))
    except (OSError, ValueError):
        return []
    find = identity_find(tmap)
    evidence = _evidence_rows([str(p) for p in sidecars]) if sidecars \
        else []
    # The evidence under the merge's own symbols: its DELETE lines, then
    # its GLOBAL renames (the parsers write the sidecars before the
    # merge maps anything).
    try:
        renames = merge_renames(tmap, to_base=False)
    except ValueError:
        renames = {}
    evidence = [dict(r, symbol=map_symbol(r["symbol"], renames))
                for r in evidence if r["symbol"] not in tmap.delete]
    rate = _raw_rate_lookup(rates_path, base_currency) or (
        lambda _c, _d: None)
    notes: List[str] = []
    booked = book_journals(txs, evidence, lambda a, b: find(a) == find(b),
                           rate, note=notes.append)
    if booked:
        from taxjson.lib.safe_write import write_atomic
        write_atomic(raw_json, _json.dumps(doc, indent=2))
    return notes


# ---------------------------------------------------------------- stages

def _rates_coverage_stale(rates_path: Path, today: Optional[date_cls] = None,
                          currencies: Optional[List[str]] = None) -> bool:
    """Whether to_base.csv's DATA has aged out. Freshness is a property of
    the data, not the file's mtime: coverage ends at the generation date,
    so on a stable install (no package/config mtime bumps to invalidate
    the cache) the file would otherwise be reused forever and every trade
    after its last row would have no rate (and stop the conversion).
    Stale when the newest rate row is more than 3 days old (tolerates
    weekends/short holidays without refetching daily) — judged PER
    CURRENCY when `currencies` is given: the file concatenates one block
    per source currency, and a USD block cut short by a failed download
    hid behind a fresh AUD last line (S046-06)."""
    today = today or date_cls.today()
    newest: Dict[str, date_cls] = {}
    try:
        with rates_path.open("r", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if not parts:
                    continue
                d = datetime.strptime(parts[0], "%Y-%m-%d").date()
                cur = parts[2].upper() if len(parts) > 2 else ""
                for key in (cur, "*"):
                    if key not in newest or d > newest[key]:
                        newest[key] = d
    except (OSError, ValueError, IndexError):
        return True                          # unreadable/malformed: refetch
    if "*" not in newest:
        return True                          # sources configured, no rows
    keys = [c.upper() for c in currencies] if currencies else ["*"]
    return any(k not in newest or (today - newest[k]).days > 3
               for k in keys)


def _inputs_fingerprint(paths: List[Path]) -> str:
    """One line per existing file: name, size and SHA-256 of the
    content (mtimes deliberately left out — see R1-253)."""
    lines = []
    for p in sorted(set(paths), key=str):
        try:
            data = p.read_bytes()
        except OSError:
            continue
        lines.append(f"{p.name} {len(data)} "
                     f"{hashlib.sha256(data).hexdigest()}")
    return "\n".join(lines) + "\n"
