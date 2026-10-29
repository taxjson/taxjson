#!/usr/bin/env python3
"""
taxjson_wash_radar.py

Tax-Efficient Holding Advisor: the superficial-loss (Canada, ITA s.54)
or wash-sale (US, IRC §1091) status of every holding, as of a date.
Ported from tt_wash_radar.pl.

Analyzes potential wash sales (superficial losses) based on current holdings
and recent transaction history across taxable and sheltered accounts.

The two countries' rules never mix (tax-logic CA-PLAN-* / US-PLAN-*):
- canada: windows on SETTLE dates; a replacement backs a denial only
  while the holder still holds it at day 30, so a denied loss can be
  rescued (VIOLATION); a long call on the shares is a replacement.
- usa: windows on TRADE dates; an existing loss's verdict is the US
  ENGINE's own (lib/core USATaxRules run in-process on the same books,
  as of the date), so there is no still-held test and no rescue: a
  washed loss is WASHED (deferred into the replacement's basis, or lost
  for good through an IRA purchase); a long call is a note only.

Usage:
    python -m taxjson.bin.taxjson_wash_radar --taxable tax1.json --sheltered sh1.json [--date YYYY-MM-DD]
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

from taxjson.lib.cli_diag import guard_main
from taxjson.lib.core import (is_stock_dividend,
                              not_a_purchase)
from taxjson.lib.country import add_country_argument
from taxjson.lib.corporate_timeline import (SplitTimeline, radar_priority,
                                            account_tie, dedupe_split_events,
                                            split_seen)
from taxjson.lib.price_chain import is_crypto_symbol
from taxjson.lib.ticker_map import is_option_ticker
from taxjson.lib import out as _out
from taxjson.lib.wash_scope import (advisory_lines, advisory_parts,
                                    scope_lines, scope_more,
                                    scope_note)

# UTC-noon epoch helpers: shared home in lib/dates (the DST rationale
# lives there). Names re-exported for external callers.
from taxjson.lib.dates import (  # noqa: E402,F401
    date_time_to_epoch, date_to_epoch, epoch_to_date,
    last_trade_date_settling_by, noon_utc)

# Ordering ladder lives in lib/corporate_timeline with every other sort
# profile. The radar carried a private copy for years and engine ordering
# fixes never reached it — the repo's highest fix-ratio file for exactly
# that reason. Alias kept for the sort call below and external callers.
get_tx_priority = radar_priority

_PROG = "taxjson-wash-radar"

# Advisory sections, most-actionable first. Each row's category is the keyword
# before the first ':' in its advisory (e.g. "LOCKED: ..." → "LOCKED"). Rows
# are grouped into these sections, alphabetical by ticker within each.
_CATEGORY_ORDER = ["VIOLATION", "WASHED", "BLOCKED", "LOCKED", "EXITABLE",
                   "CAUTION", "COOLING", "RISK", "CLEAR"]
# The sections each country prints (VIOLATION = a rescuable s.54 denial
# and CAUTION = a registered buyer that sold out are Canadian states;
# WASHED = a §1091 disallowance nothing can undo is the US one).
_COUNTRY_ORDER = {
    "canada": ["VIOLATION", "BLOCKED", "LOCKED", "EXITABLE", "CAUTION",
               "COOLING", "RISK", "CLEAR"],
    "usa": ["WASHED", "BLOCKED", "LOCKED", "EXITABLE", "COOLING", "RISK",
            "CLEAR"],
}
_CATEGORY_TITLE = {
    "VIOLATION": "VIOLATION — superficial loss; act to rescue the loss",
    "WASHED": "WASHED — wash sale (§1091): loss disallowed; no sale "
              "undoes it",
    "BLOCKED": "BLOCKED — do not buy",
    "LOCKED": "LOCKED — recent buy; do not sell at a loss",
    "EXITABLE": "EXITABLE — loss OK only if you exit the FULL position",
    "CAUTION": "CAUTION — recent buy, but the buyer has since sold out",
    "COOLING": "COOLING — wait before re-entering",
    "RISK": "RISK — sellable now; sheltered still holds (pause DRIPs "
            "30 days after selling)",
    "CLEAR": "CLEAR — safe to sell at a loss",
}


class _EngineLosses:
    """The gains engine's dispositions, keyed by (account, tx id), from
    the --gains files. `rows_for(tx)` is None when the files do not
    cover the row's account and tax year (the radar then uses its own
    pool), else the engine's LOSS rows for it (possibly empty: the
    engine booked a gain, or no disposition at all)."""

    def __init__(self):
        self.by_key: Dict[tuple, list] = {}
        self.cover: Dict[str, set] = {}     # account -> {(year, basis)}
        # The project's option_buyback_loss_superficial as the gains
        # files state it (CA-SL-11 / CA-SL-12).
        self.buyback_wash = False

    @classmethod
    def load(cls, paths):
        self = cls()
        for p in paths or []:
            try:
                # A wrong-shape file (a list, a scalar summary) was an
                # AttributeError traceback (audit S042-18).
                from taxjson.lib.json_input import read_work_doc
                doc = read_work_doc(p)
            except (OSError, ValueError) as e:
                _out.fail(f"--gains {p}: {e}", prog=_PROG)
            summ = doc.get('summary') or {}
            year = str(summ.get('year') or '').strip()
            if not year:
                # A gains file with no year covers nothing we can
                # bound — refuse rather than guess.
                _out.fail(f"--gains {p}: no summary.year", prog=_PROG,
                         details=["Pass the pipeline's "
                                  "work/<acct>_gains*.json files."])
            basis = str(summ.get('tax_date_basis') or 'settle').lower()
            buyback_wash = bool(summ.get('option_buyback_loss_superficial'))
            self.buyback_wash = self.buyback_wash or buyback_wash
            accts = set()
            meta_acct = ((doc.get('metadata') or {}).get('account') or '')
            if meta_acct:
                accts.add(meta_acct)
            else:
                stem = Path(p).name
                for suf in ('_gains_wash.json', '_gains.json'):
                    if stem.endswith(suf):
                        accts.add(stem[:-len(suf)])
            for r in doc.get('transactions') or []:
                if r.get('action') or not r.get('qty') or 'id' not in r:
                    continue
                acct = str(r.get('account') or meta_acct or '')
                accts.add(acct)
                rows = self.by_key.setdefault((acct, str(r['id'])), [])
                g = r.get('raw_gain', r.get('gain'))
                if g is None or float(g) >= -0.01 or r.get('grant') \
                        or r.get('deemed'):
                    continue
                # A buy-back loss on a written option (any premium
                # timing) is not fed to the engine's superficial-loss
                # solver unless the project opts in (core.py
                # _wash_eligible).
                if (not buyback_wash
                        and r.get('is_option')
                        and (r.get('direction') or '') == 'SHORT'):
                    continue
                rows.append({**r, 'raw_gain': float(g)})
            for a in accts:
                self.cover.setdefault(a, set()).add((year, basis))
        return self

    def rows_for(self, tx):
        acct = (getattr(tx, 'account', '') or '').strip()
        cov = self.cover.get(acct)
        if not cov:
            return None
        for year, basis in cov:
            d = (tx.date if basis == 'trade'
                 else (tx.date_settle or tx.date)) or ''
            if d[:4] == year:
                return self.by_key.get((acct, str(tx.id)), [])
        return None


_US_TITLE = {
    "RISK": "RISK — sellable now; an IRA still holds (pause IRA buys and "
            "dividend reinvestment 30 days after selling)",
}


# The WASHED title when its rows' losses are .tt ALLOWLOSS filing
# positions (US-WASH-25): every row ("all"), or some ("some"). "loss
# disallowed" was false for a claimed one, which the brief line already
# said (backlog TJ-0023, TJ-0246).
_WASHED_CLAIMED_TITLE = {
    "all": "WASHED — wash sale (§1091) you claim as your filing "
           "position (ALLOWLOSS): the books allow the loss",
    "some": "WASHED — wash sale (§1091): loss disallowed, or claimed as "
            "your filing position (ALLOWLOSS); no sale undoes it",
}


def _claim_state(rows) -> str:
    """'all' / 'some' / '' of a section's text rows (the 6th cell: the
    row's losses are all ALLOWLOSS filing positions)."""
    flags = [len(r) > 5 and bool(r[5]) for r in rows]
    if flags and all(flags):
        return "all"
    return "some" if any(flags) else ""


def _category_title(cat: str, country: str, claimed: str = "") -> str:
    if cat == "WASHED" and claimed in _WASHED_CLAIMED_TITLE:
        return _WASHED_CLAIMED_TITLE[claimed]
    if country == "usa" and cat in _US_TITLE:
        return _US_TITLE[cat]
    return _CATEGORY_TITLE.get(cat, cat or "OTHER")


def _us_engine_losses(taxable_rows, sheltered_rows, loss_overrides=()):
    """The US engine's verdict on every loss in these books: {tx id:
    {qty, loss, disallowed, permanent, denied_units, direction,
    is_option, replacements}}. It is lib/core USATaxRules itself — the
    same §1091 window (trade dates, ±30 days), replacement matching
    (every account, IRAs included; no still-held test; re-shorts) and
    FIFO-per-account basis `taxjson run` uses — run on the radar's books
    as of its date, so the radar can never state a second version of
    the rule (partition COMMANDS-01/02/05). `loss_overrides`: the
    project's .tt ALLOWLOSS filing positions (US-WASH-25), applied as
    `taxjson run` applies them; a named sale's record carries
    `position` (its loss_override) and is not disallowed."""
    import contextlib
    import copy
    import io
    from taxjson.lib.core import get_tax_rules
    tax = [copy.deepcopy(t) for t in taxable_rows]
    shl = [copy.deepcopy(t) for t in sheltered_rows]
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            res = get_tax_rules("usa").compute_gains(
                tax, sheltered_transactions=shl, detect_wash_sales=True,
                per_account_basis=True,
                **({"loss_overrides": tuple(loss_overrides)}
                   if loss_overrides else {}))
    except Exception as e:                                # noqa: BLE001
        _out.fail(f"the US engine could not evaluate these books: {e}",
                 prog=_PROG,
                 details=["Fix what it names (`taxjson run` stops on the "
                          "same books) before trusting any wash-sale "
                          "advice."])
    out: Dict[str, dict] = {}
    for r in res.get("transactions") or []:
        if r.get("action") or not r.get("qty") or not r.get("id"):
            continue
        raw = float(r.get("raw_gain", r.get("gain")) or 0.0)
        if raw >= -0.01:
            continue
        o = out.setdefault(str(r["id"]), {
            "qty": 0.0, "loss": 0.0, "disallowed": 0.0, "permanent": 0.0,
            "denied_units": 0.0, "direction": r.get("direction") or "LONG",
            "is_option": bool(r.get("is_option")), "replacements": []})
        o["qty"] += abs(float(r.get("qty") or 0.0))
        o["loss"] += -raw
        o["disallowed"] += float(r.get("disallowed_amount") or 0.0)
        o["permanent"] += float(r.get("permanently_disallowed") or 0.0)
        for rep in r.get("wash_replacements") or []:
            o["denied_units"] += float(rep.get("match_qty") or 0.0)
            o["replacements"].append(rep)
        if isinstance(r.get("loss_override"), dict):
            o["position"] = r["loss_override"]
            o["would_disallow"] = (o.get("would_disallow", 0.0) + float(
                r["loss_override"].get("would_disallow") or 0.0))
    return out


def _is_futures(t) -> bool:
    """A futures contract row (F:/'/'-prefixed, or a futures settlement
    row): it settles on its trade date (CA-DATE-09)."""
    from taxjson.lib.core import _FUTURES_PREFIX_RE
    sym = str(getattr(t, 'symbol', '') or '')
    return bool(_FUTURES_PREFIX_RE.match(sym)
                or (getattr(t, 'type', '') or '') == 'futures_settlement')


def _outside_1091(sym: str) -> bool:
    """A §1256 contract (a futures contract, an option on one, a
    broad-based index option): the US engine never disallows a loss on
    it (tax-logic US-WASH-18; core.outside_1091, the engine's own
    test, TJ-0506). US only: Canada's s.54 covers any property."""
    from taxjson.lib.core import outside_1091
    return outside_1091(sym)


def _last_trading_day(deadline_iso: str, currency: str) -> str:
    """The last trading day on or before `deadline_iso` on `currency`'s
    market (a same-day-settling futures rescue sale)."""
    from datetime import timedelta
    from taxjson.lib.market_calendar import is_trading_day
    try:
        dt = datetime.strptime(deadline_iso, "%Y-%m-%d")
    except (TypeError, ValueError):
        return deadline_iso
    for _ in range(14):
        iso = dt.strftime("%Y-%m-%d")
        if is_trading_day(iso, currency):
            return iso
        dt -= timedelta(days=1)
    return deadline_iso


# The window a loss's re-entry date closes: a buy dated (Canada: settled)
# more than 30 days after the loss's own window date (its settlement
# date under s.54, its trade date under §1091) no longer replaces it.
_REENTRY_DAYS = 31


def _first_trading_day(iso: str, currency: str) -> str:
    """The first trading day on or after `iso` on `currency`'s market."""
    from datetime import timedelta
    from taxjson.lib.market_calendar import is_trading_day
    try:
        dt = datetime.strptime(iso, "%Y-%m-%d")
    except (TypeError, ValueError):
        return iso
    for _ in range(14):
        d = dt.strftime("%Y-%m-%d")
        if is_trading_day(d, currency):
            return d
        dt += timedelta(days=1)
    return iso


def reentry_date(window_epoch: float, loss=None, us: bool = False,
                 calendars=None) -> str:
    """The first TRADE day a buy no longer falls in a loss's ±30-day
    window, on the listing's calendar (tax-logic CA-PLAN-05 /
    US-PLAN-06). One expression for every re-entry date the radar states
    (COOLING, BLOCKED, WASHED's open part, a short cover's re-short
    date, each row's rebuy_at) and the tools built on it (buy-check,
    rebuy).

    USA (§1091, trade dates): the first trading day from the loss's
    trade date + 31. Canada (s.54, settle dates): the window closes 30
    days after the loss's settlement; the first trading day whose
    settlement falls after it — the window's last day itself when that
    is a trading day settling the next day, the Friday before when it
    closes on a weekend (backlog TJ-0186: it was settlement + 31 read
    as a trade date, a Sunday or a day late). `loss`: the loss record
    (symbol, currency, is_option, same_day_settle — crypto trades and
    settles every day — and futures, settling on its trade date).
    `calendars`: the market calendars a buy of the security can be on
    (a ticker.map / tobase.map pair books two listings as one: TSX and
    US days) — the date is the latest of theirs, so a buy of either
    listing on it settles after the window (backlog TJ-0447). Default:
    the loss's own listing."""
    from taxjson.lib.dates import listing_market_currency
    loss = loss or {}
    if loss.get('same_day_settle'):
        return epoch_to_date(window_epoch + _REENTRY_DAYS * 86400)
    cals = [c for c in (calendars or ()) if c] or [
        listing_market_currency(loss.get('symbol') or '',
                                loss.get('currency') or 'USD') or 'USD']
    return max(_reentry_on(window_epoch, loss, us, c) for c in cals)


def _reentry_on(window_epoch: float, loss, us: bool, cal: str) -> str:
    """reentry_date on one market calendar `cal`."""
    from datetime import timedelta
    if us:
        return _first_trading_day(
            epoch_to_date(window_epoch + _REENTRY_DAYS * 86400), cal)
    end = epoch_to_date(window_epoch + 30 * 86400)
    if loss.get('futures'):
        last_in = _last_trading_day(end, cal)
    else:
        last_in = last_trade_date_settling_by(end, cal,
                                              bool(loss.get('is_option')))
    try:
        nxt = (datetime.strptime(last_in, "%Y-%m-%d")
               + timedelta(days=1)).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return epoch_to_date(window_epoch + _REENTRY_DAYS * 86400)
    return _first_trading_day(nxt, cal)


def _listings_booked_under(map_path, booked) -> Dict[str, List[str]]:
    """{booked symbol: [other listings]} — the share listings the
    project's ticker.map (tobase.map read with it) books under each
    symbol of `booked` through a TOBASE (or legacy JOURNAL) line, the
    chain followed to its end (`TOBASE ZZX.US ZZX.TO`: ZZX.TO ->
    [ZZX.US]). A TSX Venture listing's .TO spelling is the same listing
    and is not named. {} without a map. A map that cannot be read stops
    the radar (exit 2), as `taxjson run` stops on it."""
    if not map_path:
        return {}
    try:
        from taxjson.bin.taxjson_ticker_map import (load_map_file,
                                                    merge_renames)
        tmap = load_map_file(Path(map_path))
        final = merge_renames(tmap, to_base=True)
    except (OSError, ValueError) as e:
        _out.error(f"--ticker-map {map_path}: {e}", prog=_PROG)
        sys.exit(2)
    from taxjson.lib.dates import listing_market_currency
    out: Dict[str, List[str]] = {}
    for frm in sorted(set(tmap.tobase) | set(tmap.journal)):
        f, to = str(frm).upper(), str(final.get(frm) or '').upper()
        if not to or f == to or to not in booked or is_option_ticker(f):
            continue
        if (f.rpartition('.')[0] == to.rpartition('.')[0]
                and listing_market_currency(f, None)
                == listing_market_currency(to, None)):
            continue                  # ZZV.TO for ZZV.V: one listing
        out.setdefault(to, []).append(f)
    return out


def _advisory_category(adv: str) -> str:
    return adv.split(":", 1)[0].strip() if adv else ""


def _category_rank(cat: str) -> int:
    return _CATEGORY_ORDER.index(cat) if cat in _CATEGORY_ORDER else len(_CATEGORY_ORDER)


def _bought_on(ev) -> str:
    """The date a purchase is named by: its trade date, as the broker
    shows it. The event's epoch is the rule's window date (Canada: the
    settlement), one or more days later (backlog TJ-0615)."""
    return ev.get('trade_date') or epoch_to_date(ev['epoch'])


def _qfmt(x: float) -> str:
    """Trim a share quantity for display: 400.0000 -> 400, 3.3333 -> 3.3333."""
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-", "-0") else s


@guard_main("taxjson-wash-radar")
def main():
    parser = argparse.ArgumentParser(
        description="The superficial-loss (Canada, ITA s.54) or wash-sale "
                    "(US, IRC §1091) status of every holding as of a "
                    "date, across the taxable and sheltered accounts' "
                    "books: the open windows and when each clears.")
    # nargs='+' + extend: both `--taxable a b` (historical) and repeated
    # `--taxable a --taxable b` (A2 composability) work.
    parser.add_argument("--taxable", nargs='+', action='extend', default=[],
                        required=True, metavar="FILE",
                        help="Taxable transaction JSON files (repeatable)")
    parser.add_argument("--sheltered", nargs='+', action='extend', default=[],
                        metavar="FILE",
                        help="Sheltered transaction JSON files (repeatable)")
    parser.add_argument("--date", help="Target date (YYYY-MM-DD), defaults to today")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Accepted for older callers; changes "
                             "nothing (the report without --brief is the "
                             "full one)")
    parser.add_argument("--all", "-a", action="store_true",
                        help="Also the OTHER rows: listings with no open "
                             "position and no loss in the window (nothing "
                             "to watch); the default view already has the "
                             "CLEAR rows")
    parser.add_argument("--json-out", metavar="PATH", default=None,
                        help="Also write the report as structured JSON (the "
                             ".rpt text is unchanged; readers such as "
                             "harvest --radar compute countdowns at their "
                             "own time from the absolute clears_at dates)")
    parser.add_argument("--account", default="",
                        help="Account label stamped into --json-out")
    parser.add_argument("--show", nargs='+', action='extend', default=[],
                        metavar="FILE",
                        help="Show only the rows of these taxable books "
                             "(each one also passed in --taxable): the "
                             "listings they traded or hold. Every book "
                             "still decides each status, as the engine "
                             "pools them (`taxjson wash-radar ACCOUNT`; "
                             "default: every row)")
    parser.add_argument("--json", action="store_true",
                        help="Print the structured radar document (the "
                             "--json-out payload) to stdout instead of "
                             "the text report")
    parser.add_argument("--gains", nargs='+', action='extend', default=[],
                        metavar="FILE",
                        help="The engine's gains files for the taxable "
                             "accounts (work/<acct>_gains_wash.json or "
                             "_gains.json; repeatable). A disposition the "
                             "engine computed is a loss or not by the "
                             "ENGINE's raw_gain (s.47 blend across taxable "
                             "accounts, denied-loss ACB bumps, option cost "
                             "folded on exercise), not by the radar's own "
                             "per-account pool. Dispositions outside the "
                             "files' tax year fall back to the radar's pool.")
    parser.add_argument("--incomplete-history", metavar="DIR",
                        default=None,
                        help="the project folder (its .tt OPENING "
                             "cost=unknown lines): synthesize the same "
                             "opening balances the gains engine applies, "
                             "so positions bought before the data are not "
                             "shown as shorts")
    parser.add_argument("--corporate-distribution", action="append",
                        default=[], metavar="SYMBOL",
                        help="Canada: a listing the project's [settings] "
                             "corporate_distributions names (its return "
                             "of capital keeps its pay date; a Canadian "
                             "trust's moves to its record date, as in "
                             "the engine — CA-INC-DATE-ROC-TRUST)")
    parser.add_argument("--transfers-as-acquisitions", action="store_true",
                        help="Count a sheltered account's unmatched TRANSFER rows as "
                             "acquisitions/disposals for the superficial-loss / wash-sale "
                             "window (strict; [settings] transfers_as_acquisitions = true). "
                             "Default: a custody move, held but never a purchase.")
    parser.add_argument("--brief", action="store_true",
                        help="The essentials (docs/output-style.md): one "
                             "STATUS table of the positions with an "
                             "advisory, a one-line action per status; no "
                             "definitions (`taxjson wash-radar` passes "
                             "it unless --details)")
    parser.add_argument("--option-buyback-wash", action="store_true",
                        help="Canada: [settings] "
                             "option_buyback_loss_superficial = true — a "
                             "loss on buying back a written option is "
                             "superficial when the same option is bought "
                             "in its window (CA-SL-12). Default: exempt "
                             "(CA-SL-11). The gains files' summary states "
                             "it too.")
    parser.add_argument("--cleared-days", type=int, default=0,
                        metavar="N",
                        help="Also list, in each row's `losses`, the "
                             "losses whose re-entry date passed in the "
                             "last N days (marked cleared; `taxjson "
                             "rebuy` shows them as one line). Default 0")
    parser.add_argument("--similar", action="append", default=[],
                        metavar="\"A B [C ...]\"",
                        help="A group of look-alike funds (a ticker.map "
                             "SIMILAR line; repeatable): listed with each "
                             "member's status, a caveat only — they stay "
                             "separate securities for the rule "
                             "(lib/similar)")
    parser.add_argument("--ticker-map", metavar="FILE", default=None,
                        help="The project's ticker.map (its tobase.map "
                             "read with it): a listing a TOBASE line books "
                             "under another (ZZX.US as ZZX.TO) trades and "
                             "settles on its own calendar, so a date the "
                             "radar gives the security holds on every "
                             "listing — a rescue sale's last trade date "
                             "is the earliest, a re-entry or clear date "
                             "the latest — and the FULL position names "
                             "the listings it spans")
    from taxjson.lib.pipeline import (add_loss_override_args,
                                      loss_overrides_from_args)
    add_loss_override_args(parser)
    add_country_argument(parser,
                         help="Project country (required). canada "
                             "(s.54, settle dates): only a LONG acquisition "
                             "(a long call included) still held by the SAME "
                             "holder at day 30 backs a denial (taxable "
                             "pool, or each registered account on its "
                             "own), so a denial can be rescued. usa "
                             "(§1091, trade dates): the US engine's own "
                             "verdict — any purchase in the window in any "
                             "account, IRAs included, with no still-held "
                             "test, so a washed loss cannot be rescued; a "
                             "re-short triggers a short-cover loss; a long "
                             "call is a note only")

    args = parser.parse_args()
    us_mode = args.country == "usa"

    def _money_txt(x) -> str:
        """An advisory's amount, as every table prints money
        (docs/output-style.md: fmt_money, no currency symbol) with the
        books' base currency after it — CAD in Canada, USD in the US
        (base_currency refuses any other). It was '$1000.00' (backlog
        TJ-0121)."""
        from taxjson.lib.report_model import fmt_money
        return f"{fmt_money(x)} {'USD' if us_mode else 'CAD'}"
    try:
        _overrides = loss_overrides_from_args(args)
    except ValueError as e:
        _out.fail(str(e), prog=_PROG)
    if us_mode and args.option_buyback_wash:
        # A Canada-only flag (lib/country FLAG_COUNTRY: ITA s.54).
        from taxjson.lib.country import flag_country_problems
        for _p in flag_country_problems(
                args.country, {"--option-buyback-wash": True},
                tool="taxjson-wash-radar"):
            print(_p, file=sys.stderr)
        sys.exit(2)

    def _tax_day(t):
        """The date a row's window is measured on: the TRADE date under
        §1091 (the US engine's basis), the SETTLE date under s.54 (the
        Canadian engine's)."""
        if us_mode:
            return t.date or t.date_settle
        return t.date_settle or t.date

    orig_stdout = sys.stdout
    if args.json:
        # One code path builds both outputs: the text report renders
        # into a discarded buffer and only the structured payload is
        # printed at the end. Restore to the SAVED stream, not
        # sys.__stdout__ — under in-process dispatch with output
        # capture, __stdout__ is the real terminal and the JSON would
        # escape the capture.
        import io
        sys.stdout = io.StringIO()

    if args.date:
        from taxjson.lib.dates import as_of_date_problem
        _bad = as_of_date_problem(args.date)
        if _bad:
            # 9999-12-31: the window past it is no date (issue #54).
            _out.fail(_bad, prog=_PROG)
        today_dt = datetime.strptime(args.date, "%Y-%m-%d")
    else:
        today_dt = datetime.now()

    # Noon UTC on today's (local) calendar date — same basis as
    # date_to_epoch so every days_since is an exact whole number.
    today_dt = noon_utc(today_dt)
    today_epoch = today_dt.timestamp()

    window_sec = 30 * 86400

    # Every book goes through the canonical row funnel (core.
    # coerce_transaction_row, as load_transactions does): a row the
    # engine would refuse — a non-numeric quantity, a missing date, a
    # trade with no quantity/net_amount — stops the radar with one line
    # naming the file and row. It used to crash with a TypeError
    # traceback, or skip the row silently (-v only) and give advice
    # computed without it (audit S049-22, S053-16); a bare-array book,
    # which the loader accepts, crashed with AttributeError (S079-11).
    from taxjson.lib.core import coerce_transaction_row, require_trade_fields
    from taxjson.lib.json_input import load_json_doc_or_exit, rows_or_exit
    _prog = "taxjson-wash-radar"
    transactions = []
    # A Canadian trust's return of capital lowers the ACB on its RECORD
    # date (s.53(2)(h); tax-logic CA-INC-DATE-ROC-TRUST): the engine
    # moves the row there before the walk, so the radar's own pool must
    # too, or a sale between the record and pay dates read as a loss the
    # engine never books (audit A2-1174 / A2-1178). Canada only.
    _income_rules = None
    if not us_mode:
        from taxjson.lib.income_dating import IncomeRules
        _income_rules = IncomeRules(
            country="canada",
            corporate_distributions=tuple(
                str(s).strip().upper()
                for s in (args.corporate_distribution or ())
                if str(s).strip()))

    def load_files(files, group_type):
        for f in files:
            path = Path(f)
            doc = load_json_doc_or_exit(_prog, path)
            rows = rows_or_exit(_prog, doc, path, "transactions")
            try:
                objs = [coerce_transaction_row(
                    {k: v for k, v in t.items() if not str(k).startswith('_')},
                    i, str(path)) for i, t in enumerate(rows)]
                require_trade_fields(objs)
            except ValueError as e:
                _out.error(str(e), prog=_prog)
                sys.exit(2)
            for tx_obj in objs:
                if _income_rules is not None:
                    _rec = _income_rules.roc_record_date(tx_obj)
                    if _rec:
                        tx_obj.date = _rec
                        tx_obj.date_settle = _rec
                tx_obj._group = group_type
                tx_obj._file = str(path)
                # The engine's own window basis: SETTLEMENT dates for
                # Canada (the CRA window, settle end-to-end), TRADE dates
                # for the US (§1091). The wrong basis disagreed with the
                # engine by 1-2 business days at the ±30d edges (a
                # 31-trade-day gap that is 29 settle-days).
                _d = _tax_day(tx_obj)
                tx_obj._epoch = date_to_epoch(_d)
                tx_obj._epoch_full = date_time_to_epoch(_d, tx_obj.time)
                transactions.append(tx_obj)

    load_files(args.taxable, 'TAXABLE')
    load_files(args.sheltered, 'SHELTERED')
    # --show: the listings of the scoped books (`wash-radar ACCOUNT`).
    # Only the rows shown are scoped; every book above still decides
    # each status, as the engine pools the taxable accounts and checks
    # every registered one (audit 2026-10-23 #6: a scoped radar read
    # only its own book and called a full exit safe while a sibling
    # taxable account still held the rebuy).
    _show_paths = {str(Path(f).resolve()) for f in args.show}
    _taxable_paths = {str(Path(f).resolve()) for f in args.taxable}
    if _show_paths - _taxable_paths:
        _out.fail("--show names a book that is not a --taxable file: "
                  + ", ".join(sorted(_show_paths - _taxable_paths)),
                  prog=_PROG)
    _show_symbols = None
    if _show_paths:
        _show_symbols = set()
        for _t in transactions:
            if (_t._group == 'TAXABLE'
                    and str(Path(_t._file).resolve()) in _show_paths
                    and _t.action not in ('DIVIDEND', 'DIVIDEND_IN_LIEU',
                                          'TAX', 'INTEREST', 'FEE',
                                          'DISALLOW')):
                for _sym in (_t.symbol, getattr(_t, 'symbol_new', None)):
                    if _sym:
                        _show_symbols.add(str(_sym))
    # The engine's input order (core._by_account_name): taxable rows,
    # then sheltered, each in account-name order, so a tie at one moment
    # is broken as the engine breaks it — never by the order the books
    # were passed in (taxjson.toml's; issue #31).
    transactions.sort(key=lambda t: (t._group != 'TAXABLE',
                                     account_tie(t)))

    # Custody-move TRANSFER noise (broker moves, cancel/rebook
    # restatements, registered-to-registered moves) must be invisible
    # here exactly as it is to the gains engine: raw out/in legs booked
    # book-value ACB swings that inflated loss estimates (or erased
    # them, for zero-net in-legs), and the in-leg registered as an
    # "acquisition" that turned a pure custody move into a VIOLATION.
    from taxjson.lib.pipeline import (_drop_self_cancelling_transfers,
                                      _net_cross_account_transfers)
    # A trade executed BEFORE a split that settles AFTER it: the walk
    # orders by settle date, so the pool splits first and the executed
    # (pre-split) quantity must be re-denominated into post-split units
    # (qty x ratio; money untouched) — the engine's rule (core.py, the
    # settle-lag straddle). Without it a pre-split sale of 80 in an
    # 11-for-10 split left 8 nonexistent post-split shares held.
    # (US: the walk orders by TRADE date, so a pre-split trade is booked
    # before the split scales it — nothing to re-denominate.)
    # One split reported twice (two sources, nearby dates) is ONE event
    # (split_copies, CA-CORP-12 — keyed per security like the engine).
    _lag_splits = [t for t in dedupe_split_events(transactions)
                   if t.action == 'SPLIT' and t.date and not us_mode]
    if _lag_splits:
        for t in transactions:
            if not (t.action in ('BUYSELL', 'ASSIGN') and t.date
                    and t.date_settle and t.date < t.date_settle):
                continue
            _f = 1.0
            for sp in _lag_splits:
                _spd = sp.date_settle or sp.date
                if (sp.symbol == t.symbol
                        and (t.date, t.time or '00:00:00')
                        < (_spd, sp.time or '00:00:00')
                        and _spd < t.date_settle
                        # a RENAME-split straddle is refused by the
                        # engine; the radar leaves the row as booked
                        and (getattr(sp, 'symbol_new', '') or '')
                        in ('', sp.symbol)
                        and float(sp.quantity or 0)):
                    _f *= float(sp.quantity)
            if abs(_f - 1.0) > 1e-12:
                t.quantity = float(t.quantity) * _f

    _tax_rows = [t for t in transactions if t._group == 'TAXABLE']
    _shl_rows = [t for t in transactions if t._group != 'TAXABLE']
    _tax_rows, _ = _drop_self_cancelling_transfers(_tax_rows)
    # The sheltered book gets the same treatment the engine's wash
    # context does (lib/pipeline._handle_transfers), under the project's
    # transfer policy (--transfers-as-acquisitions, pre-release review
    # L5): by default (CA-SL-16 / US-WASH-23) zero-net clusters and own-
    # account moves net whatever trades sit near them and an unmatched
    # TRANSFER is a custody move (held, never a purchase); strict
    # (CA-SL-17 / US-WASH-24) moves near a taxable trade are not netted
    # and an unmatched sheltered transfer-in counts as an acquisition.
    # A netted own-account move (rrsp -> rrsp2) stays as balance-only
    # TRANSFER rows — dropping both legs left the receiver short and the
    # sender long, so per-account holdings came out wrong.
    _strict_xfer = bool(getattr(args, 'transfers_as_acquisitions', False))
    _shl_rows, _ = _drop_self_cancelling_transfers(
        _shl_rows, main_transactions=_tax_rows,
        near_trade_guard=_strict_xfer)
    _own_moves: list = []
    _shl_rows = _net_cross_account_transfers(
        _shl_rows, main_transactions=_tax_rows, netted_out=_own_moves,
        near_trade_guard=_strict_xfer)
    for _t in _own_moves:
        _t._own_move = True
    _shl_rows = _shl_rows + _own_moves

    # Missing-history openings (.tt OPENING cost=unknown lines): the SAME
    # OPENING_BALANCE rows the gains pass synthesizes
    # (lib/pipeline.prepare_books), or a position bought before the data
    # walks negative here — shown as a short,
    # its rebuys booked as short covers with invented losses, and real
    # superficial-loss violations missed (2026-09 audit). Pairs are
    # (symbol, account), so each group's rows are walked separately.
    if args.incomplete_history:
        from taxjson.lib.missing_history import (load_missing_history,
                                                  synthesize_openings)
        try:
            _mh_pairs = load_missing_history(Path(args.incomplete_history))
        except (OSError, ValueError) as e:
            # Exit 2 like gains/t1135/audit --incomplete-history; a
            # sys.exit(str) exited 1, the 'finding' code (A2-1435).
            _out.error(f"--incomplete-history {args.incomplete_history}: "
                      f"{e}", prog=_PROG)
            sys.exit(2)

        def _with_openings(rows, group):
            new_rows, _log = synthesize_openings(rows, _mh_pairs)
            for t in new_rows:
                if not hasattr(t, '_group'):
                    t._group = group
                    t._file = str(args.incomplete_history)
                    _d = _tax_day(t)
                    t._epoch = date_to_epoch(_d)
                    t._epoch_full = date_time_to_epoch(_d, t.time)
            return new_rows
        _tax_rows = _with_openings(_tax_rows, 'TAXABLE')
        _shl_rows = _with_openings(_shl_rows, 'SHELTERED')
    transactions = _tax_rows + _shl_rows

    # As-of filter: a trade is in the books once it is MADE (trade date
    # on or before the as-of date), even though it settles later. The
    # old settle-date filter hid every trade made today (T+1) — and
    # Friday's trades all weekend — so buy-check said SAFE right after
    # a loss sale and sell-check said SAFE right after a buy (2026-09
    # audit). Windows stay on the country's basis (the rows keep their
    # epochs); only the "is it booked yet" test uses the trade date.
    engine = _EngineLosses.load(args.gains)
    _buyback_wash = bool(args.option_buyback_wash or engine.buyback_wash)
    # A .tt line has ONE date: in a settle-basis project it is the
    # settlement date, so a line dated tomorrow is a trade made today
    # (T+1). Its trade date is the last trading day that settles by it
    # (audit A2-0130; the R1-225 fix covered rows that carry both).
    _bases = {b for cov in engine.cover.values() for _y, b in cov}
    _tt_settle_dated = (('settle' in _bases) if _bases else not us_mode)
    _today_iso = epoch_to_date(today_epoch)

    # A listing's currency (its trading calendar) for a sale of it today.
    _sym_cur: Dict[str, str] = {}
    for _t in transactions:
        _c = (getattr(_t, 'currency', '') or '').strip().upper()
        if _t.symbol and _c:
            _sym_cur.setdefault(_t.symbol, _c)

    # The other listings the ticker map books under each booked symbol
    # (TOBASE ZZX.US ZZX.TO: ZZX.US's trades are in the books as
    # ZZX.TO). Its calendar is the US one: a rescue sale or a buy of the
    # US shares settles on US days, and the dates were the booked
    # listing's alone — a sell-by on US Thanksgiving, a rebuy date on
    # Canada Day whose US buy settled inside the window (backlog
    # TJ-0446, TJ-0447).
    _other_listings = _listings_booked_under(
        args.ticker_map, {str(t.symbol).strip().upper()
                          for t in transactions if t.symbol})

    def _listing_key(sym: str) -> str:
        s = str(sym or '').strip().upper()
        if is_option_ticker(s):
            from taxjson.lib.core import parse_option_underlying
            return (parse_option_underlying(s) or s).upper()
        return s

    def _calendars(sym: str, currency=None) -> List[str]:
        """The market calendars a trade of `sym` can settle on: its own
        listing's, then those of the listings booked under it (an
        option: its underlying's)."""
        from taxjson.lib.dates import listing_market_currency
        s = str(sym or '').strip().upper()
        cals = [listing_market_currency(
            s, currency or _sym_cur.get(sym) or 'USD') or 'USD']
        for o in _other_listings.get(_listing_key(s), ()):
            c = listing_market_currency(o, None)
            if c and c not in cals:
                cals.append(c)
        return cals

    def _either_listing(sym: str) -> str:
        """' on either listing, A or B' when the listings booked as
        `sym` trade on more than one calendar (a date then holds on
        each), else ''."""
        if len(_calendars(sym)) < 2:
            return ""
        names = [_listing_key(sym)] + list(
            _other_listings.get(_listing_key(sym), ()))
        if len(names) == 2:
            return f" on either listing, {names[0]} or {names[1]}"
        return f" on any listing ({', '.join(names)})"

    def _reentry(l) -> str:
        """A loss record's re-entry date (reentry_date): the first
        trading day a buy no longer replaces it, on every listing."""
        return reentry_date(l['epoch'], l, us_mode,
                            _calendars(l.get('symbol') or '',
                                       l.get('currency')))

    def _days_to(iso: str) -> int:
        return int(round((date_to_epoch(iso) - today_epoch) / 86400))

    def _buy_replaces(l) -> bool:
        """Whether a BUY can replace loss record `l`: in Canada any loss
        (s.54: an acquisition held at day 30); in the US not a loss on
        covering a short (only a new short sale replaces it, §1091(e)),
        a claimed filing position (.tt ALLOWLOSS) or a loss the engine
        already disallowed in full."""
        if not us_mode:
            return True
        _left = float(l['loss']) - float(l.get('disallowed') or 0.0)
        return (not l.get('position')
                and l.get('direction', 'LONG') == 'LONG' and _left > 0.01)

    def _per_unit_denial(ls):
        """(amount, units): the share of the open losses `ls` that ONE
        unit bought back now denies, and how many units it holds for
        (None: as many as were sold). Canada (CA-SL-08, per sale): each
        sale is judged on its own, so one unit held at day 30 replaces a
        unit of every in-window sale — each loss's amount per unit sold,
        summed, for as many units as the smallest of the sales (two
        sales of 100 at 0.20 a unit: 0.40 for each of 100 units bought
        back; the total over the 200 units sold read 0.20 — backlog
        TJ-0560). US (US-WASH-02, share for share): a share bought now
        replaces a share of the earliest open loss first — its amount
        per unit, for that sale's units. (None, None) when no loss
        names its units."""
        rates = []
        for l in ls:
            q = float(l.get('qty') or 0.0) * float(l.get('f') or 1.0)
            if q > 1e-9:
                rates.append((l['epoch'], l.get('seq', 0),
                              float(l['loss']) / q, q))
        if not rates:
            return None, None
        if us_mode:
            first = min(rates)
            return first[2], (first[3] if len(rates) > 1 else None)
        sizes = {round(q, 6) for _e, _s, _r, q in rates}
        return (sum(r for _e, _s, r, _q in rates),
                min(sizes) if len(sizes) > 1 else None)

    def _sale_rec(ticker: str) -> Dict[str, Any]:
        """A loss sale of `ticker` today, as reentry_date reads a loss
        record: its listing, currency and settlement kind (a coin
        settles on its trade date, every day; a futures contract on its
        trade date, on the exchange's days)."""
        from taxjson.lib.core import _FUTURES_PREFIX_RE
        return {'symbol': ticker,
                'currency': _sym_cur.get(ticker) or 'USD',
                'is_option': is_option_ticker(ticker),
                'same_day_settle': is_crypto_symbol(ticker),
                'futures': bool(_FUTURES_PREFIX_RE.match(ticker or ''))}

    def _sale_epoch(ticker: str) -> float:
        """The window date of a loss sale of `ticker` placed today: under
        s.54 its SETTLEMENT date — the first trading day from today, on
        the listing's calendar, settled T+1 (a Friday trade, the
        Monday) — the basis the acquisitions' dates are on; under
        §1091 today's trade date. The forward view measured a sale
        today from today against settled buys, and a Friday's verdict
        held a buy the Monday settlement had left behind (backlog
        TJ-0243, audit TJ-0416). A security on two listings (a
        ticker.map pair): the earliest of their settlements, so every
        acquisition a sale on either listing would meet is counted."""
        if us_mode:
            return today_epoch
        rec = _sale_rec(ticker)
        if rec['same_day_settle']:
            return today_epoch
        from taxjson.lib.dates import settlement_date
        out = []
        for cal in _calendars(ticker, rec['currency']):
            trade = _first_trading_day(_today_iso, cal)
            out.append(trade if rec['futures'] else
                       settlement_date(trade, cal, rec['is_option']))
        return date_to_epoch(min(out))

    def _safe_sale_date(acq_epoch: float, ticker: str) -> str:
        """The first trading day a loss sale of `ticker` no longer meets
        an acquisition dated `acq_epoch` (its window date): the date a
        LOCKED, EXITABLE or CAUTION row clears. The same arithmetic as
        a buy after a loss (reentry_date): under s.54 the first trade
        whose sale settles after the acquisition's settlement + 30; under
        §1091 the first trading day from its trade date + 31. It was
        that date + 31 on the calendar: a weekend, or a day late
        (backlog TJ-0243). On every listing of the security: the
        latest."""
        return reentry_date(acq_epoch, _sale_rec(ticker), us_mode,
                            _calendars(ticker))

    def _rescue_deadline(l, ticker):
        """(settle bound, last TRADE date) of a Canadian loss's rescue
        sale: it must settle by the loss's settlement + 30, on the
        LISTING's calendar (a TSX USD unit trades on TSX days —
        A2-1183). The replacement may be held on another listing the
        ticker map books under this one (ZZY.US as ZZY.TO), which
        settles on its own days: the earliest of the listings' dates,
        so a sale of either on it settles in time (backlog TJ-0446)."""
        settle_d = epoch_to_date(l['epoch'] + 30 * 86400)
        if l.get('same_day_settle'):
            return settle_d, settle_d
        cals = _calendars(l.get('symbol') or ticker, l.get('currency'))
        if l.get('futures'):
            # A futures contract settles on its trade date (CA-DATE-09):
            # the last trading day on or before the bound (A2-1181).
            return settle_d, min(_last_trading_day(settle_d, c)
                                 for c in cals)
        return settle_d, min(last_trade_date_settling_by(
            settle_d, c, bool(l.get('is_option'))) for c in cals)

    def _trade_day(t):
        d = t.date or t.date_settle
        if (_tt_settle_dated and d and d > _today_iso
                and str(getattr(t, 'source', '') or '').lower()
                .endswith('.tt')
                and (not t.date_settle or t.date_settle == t.date)
                and t.action in ('BUYSELL', 'ASSIGN')):
            from taxjson.lib.dates import listing_market_currency
            if is_crypto_symbol(t.symbol or '') or _is_futures(t):
                return d
            return last_trade_date_settling_by(
                d, listing_market_currency(t.symbol, t.currency or 'USD'),
                is_option_ticker(t.symbol or ''))
        return d

    def _booked(t):
        return date_to_epoch(_trade_day(t)) <= today_epoch

    if us_mode:
        transactions.sort(key=lambda x: (x._epoch_full, get_tx_priority(x)))
    else:
        # The Canada engine's order (lib/corporate_timeline ca_main):
        # settle date, then TRADE date, then clock time — two settle-
        # lagged rows that settle the same day (a Friday sale and the
        # next trading day's buy over a settlement holiday) are taken in
        # the order they were made, an opening balance or a split first.
        # By clock time alone a Monday 09:45 buy was applied before the
        # Friday 15:00 sale, which then read as a loss (audit A2-0443).
        transactions.sort(key=lambda x: (
            x._epoch,
            '' if x.action in ('OPENING_BALANCE', 'SPLIT') else (x.date or ''),
            x.time or '', get_tx_priority(x)))
    # The walk's own processing order: same-moment questions (does a
    # rebuy listed after a loss sale count as acquired after it; which
    # of two same-moment losses claims a shared replacement first) are
    # answered by it, as the engine answers them by its own (CA-SL-08).
    for _i, _t in enumerate(transactions):
        _t._seq = _i

    # SPLIT-rename equivalence classes — the SAME union the engine
    # matches on (core.py: alias_of = split_timeline.canonical; a loss
    # on OLD.TO considers NEW.TO buys as triggers and NEW.TO shares as
    # still-held). The radar keyed its loss/trigger/acquisition maps by
    # RAW ticker, so a loss under the old name followed by a rebuy
    # under the new one showed COOLING + EXITABLE while the engine
    # denied the loss (2026-09 audit). Pools stay keyed by raw symbol
    # (rows print per ticker); only the MATCHING is class-level.
    # DATED (A2-0197): an old ticker's row after its rename date
    # is its own security (SplitTimeline.class_at); `date` None = now.
    _split_tl = SplitTimeline.from_transactions(
        dedupe_split_events([t for t in transactions if _booked(t)]),
        date_of=_tax_day)

    def alias_of(sym, date=None, before=False):
        return _split_tl.class_at(sym, date, before=before)

    def _f_end(sym, d, inclusive=False):
        # Units of `sym` at date `d` -> units after every later split
        # booked by the as-of date: every quantity the per-holder test
        # compares (acquisitions, balances, the loss) is put in these
        # units, so a split inside the window never mixes pre- and
        # post-split counts (audit A2-0382; the engine's lineage rule).
        try:
            return _split_tl.end_factor(sym, d or '', inclusive=inclusive)
        except Exception:                                  # noqa: BLE001
            return 1.0

    account_pool_qty = {} # (symbol, group, account) -> qty
    account_pool_acb = {} # (symbol, group, account) -> total_cost
    recent_losses = {}    # alias class -> [loss dicts] (ALL in-window losses)
    # Every OPENING, all history, per alias class: who acquired how many
    # units when. The superficial-loss test is PER HOLDER (the engine,
    # core.py): the taxable book is one holder (one s.47 pool across
    # the taxable accounts), each registered account is its own, and a
    # holder backs a denial only with units it ACQUIRED inside the
    # window and still holds at its end — min(acquired, held). Units a
    # registered account held before the window neither create nor
    # back a denial. Canada counts LONG acquisitions only (a new short
    # sale or written option acquires nothing, s.54); the US keeps the
    # direction-matched s.1091(e) re-short rule.
    acq_events = {}       # alias class -> [acquisition dicts]
    # s.54 para (i): a LONG CALL is a right to acquire the underlying
    # shares, so opening one is a trigger for a LONG SHARE loss (never
    # for an option loss: options wash only against the identical
    # contract). Keyed by the UNDERLYING's alias class.
    call_acq = {}         # underlying class -> [acquisition dicts]
    from taxjson.lib.core import (_FUTURES_PREFIX_RE, class_root_aliases,
                                  option_contract_size, parse_option_expiry,
                                  parse_option_right, parse_option_underlying,
                                  pool_qty_eps)
    # A class-share option root names its class line (SAMPLD for SAMPLD.B.TO,
    # SAMPLCB for SAMPLC.B — CA-SL-05 / US-WASH-12), as in both engines.
    _cls_root = class_root_aliases(t.symbol for t in transactions)

    def _call_und(sym):
        u = parse_option_underlying(sym)
        return _cls_root.get(u.upper(), u) if u else u

    def _call_underlying_cls(sym, date=None):
        if parse_option_right(sym) != 'C':
            return None
        und = _call_und(sym)
        # A futures option is never sized as a replacement of the
        # futures loss, however it is spelled: it is flagged for a
        # manual check (CA-SL-15 / US-WASH-15; audit A2-0378/0690).
        if not und or _FUTURES_PREFIX_RE.match(und):
            return None
        return alias_of(und, date)
    seen_splits = set()   # (symbol, account, date, ratio, symbol_new) dedup

    def _holder(group, pool_acct):
        return ('TAXABLE', '') if group == 'TAXABLE' else ('SHELTERED',
                                                           pool_acct)

    def _xfer_acq(tx, qty_raw):
        """Strict transfer policy: an unmatched sheltered transfer-in
        is an acquisition (the engine's transfer_rewrite row)."""
        return (_strict_xfer and tx.action == 'TRANSFER'
                and tx._group != 'TAXABLE' and qty_raw > 0
                and not getattr(tx, '_own_move', False))

    def _record_acq(cls, tx, pool_acct, qty, direction):
        _d = _tax_day(tx)
        ev = {'epoch': tx._epoch, 'qty': qty, 'dir': direction,
              'holder': _holder(tx._group, pool_acct),
              'account': (getattr(tx, 'account', '') or '').strip(),
              'tx_obj': id(tx),
              'tx_id': str(tx.id or ''),
              'seq': tx._seq,
              'trade_date': str(tx.date or '')[:10],
              # native units -> today's units (splits since)
              'f': _f_end(tx.symbol, _d),
              'symbol': tx.symbol}
        acq_events.setdefault(cls, []).append(ev)
        _u = (_call_underlying_cls(tx.symbol, tx.date)
              if direction == 'LONG' else None)
        if _u:
            # One contract is a right to its declared size of the
            # underlying (100 for a standard equity option, the size a
            # mini declares — A2-0373), in today's units.
            ev['per'] = (option_contract_size(tx)
                         * _f_end(_call_und(tx.symbol), _d))
            call_acq.setdefault(_u, []).append(ev)

    # Each holder's balance of each class over time, in today's units
    # (a split is a no-op in them): the day-30 balance of a loss whose
    # window has closed is read from it (CA-SL-02 / CA-SL-08).
    _bal_tl: Dict[tuple, list] = {}
    _bal_cum: Dict[tuple, float] = {}

    def _move(key, tx, qty_raw):
        account_pool_qty[key] += qty_raw
        _k = (alias_of(key[0], tx.date),      # dated class (A2-0197)
              _holder(key[1], key[2]))
        _bal_cum[_k] = _bal_cum.get(_k, 0.0) + qty_raw * _f_end(
            tx.symbol, _tax_day(tx),
            inclusive=(tx.action == 'OPENING_BALANCE'))
        _bal_tl.setdefault(_k, []).append((tx._epoch, _bal_cum[_k]))

    def _record_loss(cls, tx, loss):
        loss.setdefault('tx_obj', id(tx))
        loss.setdefault('seq', tx._seq)
        loss.setdefault('symbol', tx.symbol)
        # Where the loss was sold (the rebuy view names the accounts and
        # de-duplicates a class's losses across its rows).
        loss.setdefault('account',
                        (getattr(tx, 'account', '') or '').strip())
        loss.setdefault('tx_id', str(getattr(tx, 'id', '') or ''))
        loss.setdefault('f', _f_end(tx.symbol, _tax_day(tx)))
        # A crypto asset settles on its trade date (every crypto row has
        # date_settle == date), so a rescue sale may trade up to the
        # settle bound itself, weekends included — the equity T+1
        # walk-back printed a deadline up to 3 days early (R1-240). A
        # futures contract settles on its trade date too (CA-DATE-09),
        # on the exchange's trading days (A2-1181).
        _same = not tx.date_settle or tx.date_settle == tx.date
        loss.setdefault('same_day_settle', bool(
            is_crypto_symbol(tx.symbol or '') and _same))
        loss.setdefault('futures', bool(_is_futures(tx) and _same))
        recent_losses.setdefault(cls, []).append(loss)

    def _engine_record(cls, tx):
        """Record the engine's losses for this row. Returns False when
        the engine does not cover the row (the caller falls back to the
        radar's own pool), True when it does (losses, if any, recorded).
        US: every loss comes from the in-process US engine after the
        walk (_us_engine_losses), never from the radar's own pool."""
        if us_mode:
            return True
        if tx._group != 'TAXABLE' or tx.action in ('TRANSFER',
                                                   'OPENING_BALANCE'):
            return False
        rows = engine.rows_for(tx)
        if rows is None:
            return False
        for r in rows:
            _record_loss(cls, tx, {
                'date': tx.date,
                'epoch': tx._epoch,
                'qty': abs(float(r.get('qty') or 0.0)),
                'loss': -float(r['raw_gain']),
                'direction': r.get('direction') or 'LONG',
                'currency': (getattr(tx, 'currency', '')
                             or '').strip().upper(),
                'is_option': bool(r.get('is_option',
                                        is_option_ticker(tx.symbol))),
                'source': 'engine',
            })
        return True

    def _money(tx) -> float:
        """The trade's money in pool terms — the Canada engine's
        _trade_money: a BUY's cost as a magnitude (books spell it either
        sign), a SELL's proceeds SIGNED. A sale whose commission exceeds
        its gross really has negative proceeds; abs() turned that loss
        into a gain and the radar missed a superficial loss the engine
        denies (audit S053-24, S054-06)."""
        _n = float(tx.net_amount or 0.0)
        if (tx.type or '') == 'futures_settlement':
            return _n if float(tx.quantity or 0.0) < 0 else -_n
        return _n if float(tx.quantity or 0.0) < 0 else abs(_n)

    for tx in transactions:
        if not _booked(tx):
            continue

        # Cash-flow events don't change the share count. Their `quantity` is
        # the number of shares the dividend/tax/interest was computed ON (for
        # reconciliation), NOT shares acquired — counting it inflated the
        # running position (e.g. two dividend rows quoting 40 shares each made a
        # fully-sold sheltered position look like +80 held, triggering a
        # bogus "sheltered holdings exist" wash-sale warning). Only BUYSELL /
        # ASSIGN / SPLIT / TRANSFER / OPENING_BALANCE (+ ADJUST for ACB) move
        # the pool, matching the gains engine and missing-history walks.
        if tx.action in ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX', 'INTEREST',
                         'FEE', 'DISALLOW'):
            continue

        ticker = tx.symbol
        cls = alias_of(ticker, tx.date,      # rename-class key for the
                       tx.action == 'SPLIT')  # matching maps
        # Pool per (ticker, group, account) — NOT per file. All sheltered
        # accounts share one --sheltered file, so a per-file pool would apply a
        # split that happened in ONE registered account (e.g. a 10:1 in the
        # TFSA) to the COMBINED sheltered position. Keying by account scopes
        # each split to its own pool; quantities are still summed per group at
        # report time, so the taxable/sheltered totals are unchanged for
        # non-split tickers.
        group = tx._group
        acct = (getattr(tx, 'account', '') or '').strip() or tx._file
        key = (ticker, group, acct)

        if tx.action == 'ADJUST':
            _adj = float(tx.net_amount or 0.0)
            _q = account_pool_qty.get(key, 0.0)
            _wash = str(tx.id or '').startswith('WASH_')
            if _q < -1e-6 and not _wash:
                # The engine's rule (core.py, R1-157): a SHORT pool's
                # "cost" holds the short sale's proceeds, and a return of
                # capital while short is paid BY the short seller — it
                # lowers the short's gain. Added with the long sign it
                # turned a covering loss into a gain (audit S053-22).
                _adj = -_adj
            elif abs(_q) <= 1e-6 and not _wash and _adj < -0.005:
                # A return of capital on an EMPTY pool is a gain in its
                # year (s.40(3), ACB nil) and never touches the next
                # position's cost — as in the engine.
                _adj = 0.0
            _new = account_pool_acb.get(key, 0.0) + _adj
            if (not us_mode and _q > 1e-6 and not _wash and _adj < 0
                    and _new < 0):
                # s.40(3): a return of capital above the ACB is a gain
                # in its year and leaves the ACB at nil — never negative
                # (the engine's rule, tax-logic CA-ACB-07). A negative
                # cost turned a later real loss into a gain and the
                # radar missed the superficial loss (audit A2-0372).
                _new = 0.0
            account_pool_acb[key] = _new
            continue

        if tx.action == 'SPLIT':
            # Apply the split MULTIPLICATIVELY to the share count, mirroring the
            # gains engine (`running *= ratio`); the ratio is in `quantity`
            # (e.g. 1.1 for a 1.1-for-1). Total ACB is unchanged by a split —
            # only per-share cost changes. The old no-op left the running
            # quantity off by the split factor, so post-split sells underflowed
            # the pool into a false negative position (a closed holding showed a
            # negative quantity). A SPLIT carrying a non-empty
            # `symbol_new` also renames the pool to the new ticker.
            ratio = tx.quantity
            if not ratio or abs(ratio) <= 1e-12:
                continue
            # One corporate event = one application per account pool: a
            # single account fed by TWO brokers (margin dir with IB + RBC
            # CSVs) carries the same split once per broker, and their ids
            # differ so upstream dedup can't collapse them. Keyed by the
            # shared split-event identity (normalized against the mapped
            # ticker, matching this walk's pool keys).
            if split_seen(seen_splits, ticker, tx.date, ratio,
                          getattr(tx, 'symbol_new', ''),
                          account=acct) is not None:
                continue
            scaled_qty = account_pool_qty.get(key, 0.0) * ratio
            acb = account_pool_acb.get(key, 0.0)
            new_sym = (getattr(tx, 'symbol_new', '') or '').strip()
            if new_sym and new_sym != ticker:
                account_pool_qty.pop(key, None)
                account_pool_acb.pop(key, None)
                dst = (new_sym, group, acct)
                account_pool_qty[dst] = account_pool_qty.get(dst, 0.0) + scaled_qty
                account_pool_acb[dst] = account_pool_acb.get(dst, 0.0) + acb
            else:
                account_pool_qty[key] = scaled_qty
                account_pool_acb[key] = acb
            continue

        qty_raw = tx.quantity
        abs_qty = abs(qty_raw)
        
        if key not in account_pool_qty:
            account_pool_qty[key] = 0.0
            account_pool_acb[key] = 0.0
            
        current_inv = account_pool_qty[key]
        is_opening = (current_inv > 1e-6 and qty_raw > 0) or (current_inv < -1e-6 and qty_raw < 0) or (abs(current_inv) <= 1e-6)

        # The engine's verdict on this row, when its gains files cover
        # it: an OPENING here can still be an engine disposition when
        # the two pools disagree (the engine is the authority).
        if is_opening:
            _engine_record(cls, tx)

        if is_opening:
            # Net amount in TaxTransaction includes fees. A TRANSFER-in
            # with no stated book value (Questrade emits 0.0) must not
            # zero-dilute the pool's ACB — carry the average cost.
            if tx.action == 'TRANSFER' and abs(tx.net_amount) <= 0.005 \
                    and abs(current_inv) > 1e-6:
                account_pool_acb[key] += (
                    account_pool_acb[key] / abs(current_inv)) * abs_qty
            else:
                account_pool_acb[key] += _money(tx)

            if ((tx.action in ('TRANSFER', 'OPENING_BALANCE')
                    or not_a_purchase(tx))
                    and not _xfer_acq(tx, qty_raw)):
                # An opening balance or a transfer-in at its book value
                # (CA-OPEN-01, CA-ACB-TRANSFER-BV) is not a purchase either.
                # Moving/synthesizing your own shares acquires nothing:
                # neither is a superficial-loss trigger (the gains
                # engine excludes both — core.py's trigger filter), so
                # neither may feed global_lacq/open_events. Quantity and
                # value still moved above.
                _move(key, tx, qty_raw)
                continue
            if us_mode and is_stock_dividend(tx) and qty_raw > 0:
                # US-STKDIV-01: a stock dividend is not a purchase for
                # §1091 (the US engine never matches it), so it is not a
                # "Recent buy" either — the position just grows (audit
                # A2-0550). Canada keeps it: a $0 acquisition that counts
                # for s.54 (CA-STKDIV-01).
                _move(key, tx, qty_raw)
                continue

            # Record EVERY opening (not just the last 30 days from today):
            # the trigger test is against the ±30-day window around the
            # LOSS SALE, anchored to the sale date, not to today. Holder
            # and direction are kept: a short OPENING is never an
            # acquisition in Canada, and a registered account's buy
            # backs a denial only while THAT account still holds.
            _record_acq(cls, tx, acct, abs_qty,
                        'LONG' if qty_raw > 0 else 'SHORT')
        else:
            # Closing
            avg_cost_unit = account_pool_acb[key] / abs(current_inv) if abs(current_inv) > 1e-6 else 0.0
            closing_qty = min(abs_qty, abs(current_inv))
            cost_of_shares_closed = closing_qty * avg_cost_unit
            
            # Calculate gain only if taxable — from the ENGINE's gains
            # when they cover this row (its s.47 blended pool, the
            # s.53(1)(f) bump of an earlier denied loss, option cost
            # folded on exercise); the radar's own per-account pool
            # missed real losses that way and buy-check said SAFE
            # (2026-09 audit). The own-pool figure is the fallback for
            # rows outside the engine's tax year.
            if _engine_record(cls, tx):
                pass
            elif (tx._group == 'TAXABLE' and tx.action != 'TRANSFER'
                  # An exercise/assignment leg of an OPTION is not a
                  # disposition at a loss: the option's cost or premium
                  # folds into the share leg (s.49(3)/(3.1)). Booked
                  # here as "sold for 0", it invented a loss equal to
                  # the option's cost (2026-09 audit).
                  and not (tx.action == 'ASSIGN'
                           and is_option_ticker(ticker))
                  # A buy-back loss on a WRITTEN option is exempt unless
                  # the project opts in (CA-SL-11 / CA-SL-12), as in
                  # the engine (core.py _wash_eligible) and the gains-
                  # file path above: outside the files' year it showed
                  # COOLING (audit A2-0442).
                  and not (current_inv < 0 and is_option_ticker(ticker)
                           and not _buyback_wash)):
                # Prorate proceeds to the CLOSED portion: a sale that
                # crosses zero (sell 150 holding 100) otherwise nets the
                # FULL proceeds against only the closed shares' cost —
                # a real loss computed as a gain, silently downgrading
                # BLOCKED to CLEAR (2026-09 audit).
                proceeds = _money(tx) * (closing_qty / abs_qty
                                                 if abs_qty > 1e-9
                                                 else 1.0)
                gain = (proceeds - cost_of_shares_closed) if current_inv > 0 else (cost_of_shares_closed - proceeds)
                
                if gain < -0.01:
                    # Keep EVERY loss (a list, not last-wins): a newer small
                    # loss used to overwrite an older loss whose VIOLATION
                    # was still active, silently dropping its rescue
                    # deadline from the report.
                    _record_loss(cls, tx, {
                        'date': tx.date,
                        'epoch': tx._epoch,
                        'qty': closing_qty,
                        'loss': abs(gain),
                        'direction': 'LONG' if current_inv > 0 else 'SHORT',
                        # Settlement market for the rescue-deadline
                        # walk-back (T+1 CAD/USD equities; options T+1).
                        'currency': (getattr(tx, 'currency', '')
                                     or '').strip().upper(),
                        'is_option': is_option_ticker(ticker),
                    })
            
            account_pool_acb[key] -= cost_of_shares_closed
            leftover_qty = abs_qty - closing_qty
            if leftover_qty > 1e-6:
                # Reset ACB for the cross-over or remaining
                account_pool_acb[key] = (leftover_qty / abs_qty) * _money(tx)
                # The leftover OPENS a fresh position on the flip side —
                # record it like any opening, or the new short/long is
                # invisible to the trigger walk (2026-09 audit).
                if ((tx.action not in ('TRANSFER', 'OPENING_BALANCE')
                        and not not_a_purchase(tx))
                        or _xfer_acq(tx, qty_raw)):
                    _record_acq(cls, tx, acct, leftover_qty,
                                'LONG' if qty_raw > 0 else 'SHORT')
        
        _move(key, tx, qty_raw)
        # The pool's own zero: 1e-6 for shares, float noise for a coin —
        # a 0.0000009 BTC rebuy is a holding (core.pool_qty_eps; audit
        # A2-1168).
        if abs(account_pool_qty[key]) < pool_qty_eps(ticker, abs_qty):
            account_pool_acb[key] = 0.0
            account_pool_qty[key] = 0.0

    if us_mode:
        # §1091: the US engine's own verdict on every loss, as of the
        # radar's date (rows booked by then), keyed like the walk's.
        _booked_rows = [t for t in transactions if _booked(t)]
        _us = _us_engine_losses(
            [t for t in _booked_rows if t._group == 'TAXABLE'],
            [t for t in _booked_rows if t._group != 'TAXABLE'],
            _overrides)
        _seen_ids = set()
        for t in _booked_rows:
            v = _us.get(str(t.id)) if t._group == 'TAXABLE' else None
            if not v or str(t.id) in _seen_ids:
                continue
            _seen_ids.add(str(t.id))
            _record_loss(alias_of(t.symbol, t.date), t, {
                'date': t.date,
                'epoch': t._epoch,
                'qty': v['qty'],
                'loss': v['loss'],
                'disallowed': v['disallowed'],
                'permanent': v['permanent'],
                'denied_units': v['denied_units'],
                'replacements': v['replacements'],
                'direction': v['direction'],
                'currency': (getattr(t, 'currency', '') or '').strip().upper(),
                'is_option': v['is_option'] or is_option_ticker(t.symbol),
                'source': 'engine-usa',
                **({'position': v['position'],
                    'would_disallow': v.get('would_disallow', 0.0)}
                   if v.get('position') else {}),
            })

    # ---- who backs which denial (CA-SL-08 / US-WASH-02) ----
    # Canada: CRA's formula PER SALE (CA-SL-08) — each sale is judged on
    # its own, so a held unit may back the denials of two sales (and a
    # sale today whatever backed an earlier loss); only the fills of ONE
    # sale (core.disposition_groups) share a replacement unit, in the
    # engine's order (audit A2-0689). USA: each replacement share is
    # matched once, as the US engine matched it (A2-0377/0691).
    import bisect
    from taxjson.lib.core import disposition_groups
    _sale_of = disposition_groups([t for t in transactions if _booked(t)])
    _sale_ledgers: Dict[int, Dict[int, float]] = {}
    # The current sale's ledger: id(acq event) -> native units claimed.
    # Empty outside _claim, so a sale today starts a ledger of its own.
    _used: Dict[int, float] = {}
    _us_used: Dict[str, float] = {}    # US: replacement tx id -> units matched
    if us_mode:
        for _v in _us.values():
            for _rep in _v.get('replacements') or []:
                _rid = str(_rep.get('tx_id') or '')
                if _rid:
                    _us_used[_rid] = (_us_used.get(_rid, 0.0)
                                      + float(_rep.get('match_qty') or 0.0))
    _tl_epochs: Dict[tuple, list] = {}

    def _bal_at(cls_, holder, epoch):
        """The holder's balance of the class (today's units) after every
        row dated on or before `epoch`."""
        tl = _bal_tl.get((cls_, holder))
        if not tl:
            return 0.0
        eps_ = _tl_epochs.get((cls_, holder))
        if eps_ is None:
            eps_ = _tl_epochs[(cls_, holder)] = [e for e, _c in tl]
        i = bisect.bisect_right(eps_, epoch)
        return tl[i - 1][1] if i else 0.0

    def _ev_avail(e):
        """Native units of an acquisition not yet backing a denial (US),
        or not yet backing another fill of the same sale (Canada)."""
        if us_mode:
            return max(0.0, e['qty'] - _us_used.get(e.get('tx_id') or '', 0.0))
        return max(0.0, e['qty'] - _used.get(id(e), 0.0))

    _acq_ix: Dict[tuple, tuple] = {}

    def _acq_within(src, cls_, lo, hi):
        """The acquisitions of class `cls_` in `src` (acq_events or
        call_acq) whose epoch is in [lo, hi], in the order recorded: a
        bisect over them sorted by epoch, not a walk of every one per
        loss (TJ-0239). The walk records them all before any loss is
        decided; a list that grew since is indexed again."""
        evs = src.get(cls_)
        if not evs:
            return []
        key = (id(src), cls_)
        ix = _acq_ix.get(key)
        if ix is None or ix[0] != len(evs):
            order = sorted(range(len(evs)), key=lambda i: evs[i]['epoch'])
            ix = _acq_ix[key] = (len(evs),
                                 [evs[i]['epoch'] for i in order], order)
        _n, epochs, order = ix
        a = bisect.bisect_left(epochs, lo)
        b = bisect.bisect_right(epochs, hi)
        return [evs[i] for i in sorted(order[a:b])]

    def _capacity(cls_, lo, hi, loss_seq, share_loss, exclude=None):
        """The engine's per-holder test for a loss of class `cls_` whose
        window is [lo, hi] (Canada): each holder backs it with
        min(units it acquired in the window and has not spent on an
        earlier fill of the same sale, its balance at day 30 less the
        units those fills claimed); a long call per (holder, series) at
        its contract size. Returns (candidates, caps, claimed-out)."""
        end_date = epoch_to_date(hi)
        # The day-30 balance as the books stand: every row in the
        # timeline is booked (traded by today), so reading it at the
        # window's end counts a trade made today that settles later. At
        # min(hi, today) a buy made today (in the TAXABLE/SHELTERED
        # columns already) was a candidate with no balance until it
        # settled — LOCKED read RISK and a rebuy's VIOLATION read
        # BLOCKED (backlog TJ-0076).
        asof = hi
        cands = []
        for e in _acq_within(acq_events, cls_, lo, hi):
            if e['dir'] != 'LONG':
                continue
            if exclude is not None and e.get('tx_obj') == exclude:
                continue       # the loss row's own leftover leg
            if (is_option_ticker(e['symbol'])
                    and (parse_option_expiry(e['symbol']) or '9999')
                    < end_date):
                continue       # not owned at day 30
            cands.append((e['holder'], e, e['f']))
        if share_loss:
            for e in _acq_within(call_acq, cls_, lo, hi):
                if exclude is not None and e.get('tx_obj') == exclude:
                    continue
                if (parse_option_expiry(e['symbol']) or '9999') < end_date:
                    continue
                cands.append((('call', e['holder'], e['symbol']), e,
                              e['per']))
        acq: Dict[Any, float] = {}
        bal: Dict[Any, float] = {}
        claimed: Dict[Any, float] = {}
        for k, e, f in cands:
            acq[k] = acq.get(k, 0.0) + _ev_avail(e) * f
            if k not in bal:
                if k[0] == 'call':
                    bal[k] = _bal_at(alias_of(e['symbol']), k[1], asof) * f
                else:
                    bal[k] = _bal_at(cls_, k, asof)
            u = _used.get(id(e), 0.0)
            # Units an earlier fill of this sale claimed are still in
            # the day-30 balance but back nothing more for it — except a
            # TAXABLE purchase made before this sale, which the sale
            # itself disposes of (the engine's _claimed_out).
            if u > 0 and (k[0] != 'TAXABLE' or e['seq'] > loss_seq):
                claimed[k] = claimed.get(k, 0.0) + min(u, e['qty']) * f
        caps = {k: max(0.0, min(a, bal.get(k, 0.0) - claimed.get(k, 0.0)))
                for k, a in acq.items()}
        return cands, caps, claimed

    def _claim(cls_, l):
        """Decide loss `l` the engine's way and record which units it
        claims (post-loss purchases first, earliest first; then earlier
        ones, latest first; the taxable holder first at one moment)."""
        share_loss = (not l.get('is_option')
                      and l.get('direction', 'LONG') == 'LONG')
        cands, caps, claimed = _capacity(
            cls_, l['epoch'] - window_sec, l['epoch'] + window_sec,
            l['seq'], share_loss, exclude=l.get('tx_obj'))
        l['claimed'] = claimed
        l['alloc'] = {}
        l['denied_end'] = 0.0
        held_sub = sum(caps.values())
        q = float(l.get('qty') or 0.0) * float(l.get('f') or 1.0)
        if held_sub <= pool_qty_eps(l.get('symbol') or '', q):
            return
        denied = min(q, held_sub)

        def _rank(k):
            h = k[1] if k[0] == 'call' else k
            return 0 if h[0] == 'TAXABLE' else 1
        post = [c for c in cands if c[1]['seq'] > l['seq']]
        pre = [c for c in cands if c[1]['seq'] <= l['seq']]
        ordered = (sorted(post, key=lambda c: (c[1]['epoch'], _rank(c[0]),
                                               c[1]['seq']))
                   + sorted(pre, key=lambda c: (c[1]['epoch'],
                                                -_rank(c[0]), c[1]['seq']),
                            reverse=True))
        rem, left = denied, dict(caps)
        for k, e, f in ordered:
            take = min(rem, _ev_avail(e) * f, left.get(k, 0.0))
            if take > 1e-12:
                _used[id(e)] = _used.get(id(e), 0.0) + take / f
                l['alloc'][k] = l['alloc'].get(k, 0.0) + take
                l.setdefault('alloc_last', {})[k] = max(
                    (e['epoch'], e['account']),
                    l.get('alloc_last', {}).get(k, (-1e18, '')))
                left[k] -= take
                rem -= take
            if rem <= 1e-12:
                break
        l['denied_end'] = denied - max(0.0, rem)

    if not us_mode:
        for _cls, _l in sorted(((c, l) for c, ls in recent_losses.items()
                                for l in ls),
                               key=lambda cl: (cl[1]['epoch'], cl[1]['seq'])):
            _used = _sale_ledgers.setdefault(
                _sale_of.get(_l.get('tx_obj'), _l.get('tx_obj')), {})
            _claim(_cls, _l)
        _used = {}

    # ---- rights the engines flag but never size (warn-only) ----
    # A warrant/right (CA-SL-14 / US-WASH-14), a call on an adjusted
    # option series or a futures option on the loss's contract (CA-SL-15
    # / US-WASH-15) bought in a loss's window: the engines name it for a
    # manual check; the radar and the tools built on it say so too, for
    # an existing loss and for a loss sale today (audit A2-0129/0378/
    # 0687/0690). US: a plain long call is a note too (US-WASH-12).
    from taxjson.lib.core import (detect_right_replacement_matches,
                                  detect_unresolved_option_replacement_matches)
    _flag_events = [t for t in transactions if _booked(t)
                    and t.action in ('BUYSELL', 'ASSIGN', 'TRANSFER',
                                     'OPENING_BALANCE')]
    _sl_word = "a wash sale" if us_mode else "superficial"
    _statute = ("§1091 (\"contract or option to acquire\")" if us_mode
                else "s.54 para (i) (a right to acquire)")
    _flag_kind = {
        'right_vs_share_loss': "a warrant/right on these shares",
        'adjusted_option_vs_loss': "a call on an adjusted series of "
                                   "these shares",
        'futures_option_vs_loss': "a call on this futures contract",
    }

    def _flag_notes(entries, today_view=False):
        """[note] for the engines' warn-only flags on `entries` (loss
        dicts: symbol, date, amount, direction)."""
        if not entries:
            return []
        try:
            found = (detect_unresolved_option_replacement_matches(
                entries, _flag_events, date_of=_tax_day,
                canonical=_split_tl.canonical)   # as the engines pass
                + detect_right_replacement_matches(
                    entries, _flag_events, date_of=_tax_day,
                    canonical=_split_tl.canonical))
        except Exception:                                  # noqa: BLE001
            return []
        out = []
        for w in found:
            what = _flag_kind.get(w['rule'], w['rule'])
            if us_mode and w['rule'] == 'futures_option_vs_loss':
                # US-WASH-18: a futures contract is a §1256 contract, not
                # stock or securities — the loss is never disallowed; the
                # engine flags the call for a manual check only (audit
                # A2-1343: it read as a wash-sale risk on "shares").
                _which = ("a loss sale today" if today_view
                          else f"the {w['loss_date']} loss")
                out.append(
                    f"NOTE: {w['option_symbol']} ({what}) was bought "
                    f"{w['option_acquired']}, inside the window of "
                    f"{_which} — a futures contract is a §1256 contract, "
                    f"not stock or securities, so §1091 does not disallow "
                    f"that loss (US-WASH-18); the engine only flags it "
                    f"[{w['rule']}], so check it by hand.")
                continue
            if today_view:
                out.append(
                    f"NOTE: {w['option_symbol']} ({what}) was bought "
                    f"{w['option_acquired']}, inside the window of a loss "
                    f"sale today — under {_statute} it may make that loss "
                    f"{_sl_word}; the engine only flags it "
                    f"[{w['rule']}], so check it by hand.")
            else:
                out.append(
                    f"NOTE: {w['option_symbol']} ({what}) was bought "
                    f"{w['option_acquired']}, inside the window of the "
                    f"{w['loss_date']} loss — under {_statute} it may make "
                    f"that loss {_sl_word}; the engine only flags it "
                    f"[{w['rule']}], so check it by hand.")
        return list(dict.fromkeys(out))

    # Reporting
    table_data = []
    row_records = []   # structured twins of table_data rows (for --json-out)
    all_tickers = set(t for t, g, a in account_pool_qty.keys())
    all_tickers.update(recent_losses.keys())

    # --show: the scoped books' listings, by rename class (a listing
    # renamed since is shown under its name today). Every row is still
    # computed; only the ones shown are filtered.
    _show_cls = (None if _show_symbols is None
                 else {alias_of(s_) for s_ in _show_symbols})

    def _shown(t):
        return (_show_cls is None or t in _show_symbols
                or alias_of(t) in _show_cls)

    sorted_tickers = sorted(all_tickers, key=lambda t: (is_option_ticker(t), t))

    for ticker in sorted_tickers:
        tax_q = 0.0
        shl_q = 0.0
        cls = alias_of(ticker)
        # "Held" for the advisories: 0.01 units hides equity dust (a
        # DRIP residue), but a crypto lot of 0.0009 BTC is ~$120 and the
        # engine's still-held test counts it (1e-6) — it was shown with
        # no advisory at all (audit S049-20, S030-00's class).
        _eps = 1e-6 if is_crypto_symbol(ticker) else 0.01

        # ALL losses still inside their own 30-day windows — not just the
        # latest one. A newer small loss must not hide an older loss whose
        # VIOLATION (and sell-by rescue deadline) is still active.
        in_window_losses = [
            l for l in recent_losses.get(cls, [])
            if (today_epoch - l['epoch']) / 86400 <= 30
        ]

        # Class-level totals drive the still-held decisions (the engine
        # sums balances over the whole rename class); the per-ticker
        # tax_q / shl_q are what the row displays. They coincide unless
        # a rename was booked in one account but not another.
        cls_tax_q = 0.0
        cls_shl_q = 0.0
        for (t, grp, acct), qty in account_pool_qty.items():
            if alias_of(t) != cls:
                continue
            if grp == 'TAXABLE':
                cls_tax_q += qty
                if t == ticker:
                    tax_q += qty
            else:
                cls_shl_q += qty
                if t == ticker:
                    shl_q += qty

        def _held_by_holder(direction='LONG'):
            """{holder: units} of the class each holder holds NOW on
            `direction`'s side, at the pool's own zero (1e-6 for shares,
            float noise for a coin: a 0.0000009 BTC rebuy still backs a
            denial — A2-1168)."""
            h = {}
            for (t, grp, pacct), qty in account_pool_qty.items():
                if alias_of(t) != cls:
                    continue
                k = _holder(grp, pacct)
                h[k] = h.get(k, 0.0) + qty
            sgn = 1.0 if direction == 'LONG' else -1.0
            return {k: sgn * v for k, v in h.items()
                    if sgn * v > pool_qty_eps(ticker, v)}

        long_held = _held_by_holder('LONG')
        calls_held = {}          # (holder, call symbol) -> contracts
        # s.54 para (i) only: a US long call is not replacement
        # property for the engine (a warning only, US-WASH-12).
        if not is_option_ticker(ticker) and not us_mode:
            for (t, grp, pacct), qty in account_pool_qty.items():
                if _call_underlying_cls(t) != cls:
                    continue
                k = (_holder(grp, pacct), t)
                calls_held[k] = calls_held.get(k, 0.0) + qty
            calls_held = {k: v for k, v in calls_held.items() if v > 1e-6}

        def _backing_us(lo, hi, crit):
            """US forward view: units each holder acquired in [lo, hi]
            on `crit`'s side that the US engine has not already matched
            to an earlier loss (share for share, US-WASH-02), capped at
            what the holder holds now — an IRA's purchase backs it
            whatever it holds now (no still-held test)."""
            held = long_held if crit == 'LONG' else _held_by_holder(crit)
            acq, last = {}, {}
            for e in acq_events.get(cls, []):
                if e['dir'] != crit or not (lo <= e['epoch'] <= hi):
                    continue
                h = e['holder']
                acq[h] = acq.get(h, 0.0) + _ev_avail(e) * e['f']
                if e['epoch'] >= last.get(h, (-1e18, ''))[0]:
                    last[h] = (e['epoch'], e['account'], _bought_on(e))
            back = {}
            for h, a in acq.items():
                cap = (a if h[0] == 'SHELTERED'
                       else min(a, held.get(h, 0.0)))
                if cap > 1e-6:
                    back[h] = {'units': cap, 'held': held.get(h, 0.0),
                               'last': last[h][0], 'account': last[h][1],
                               'last_date': last[h][2]}
            return back

        def _who(h, acct_label=''):
            if h[0] == 'TAXABLE':
                return 'taxable'
            return f"sheltered '{acct_label or h[1] or 'unknown'}'"

        def _full_position_span(t) -> str:
            """What "the FULL position" an EXITABLE row asks to sell is,
            when it is more than one listing in one account: every
            taxable unit of the security — the listings the ticker map
            books as one (ZZX.TO and ZZX.US) and each taxable account's
            units. Sold on one listing or from one account, the sale is
            a PARTIAL one and superficial (backlog TJ-0448). '' for one
            listing in one account."""
            per_acct: Dict[str, float] = {}
            for (s_, grp, pacct), qty in account_pool_qty.items():
                if (grp == 'TAXABLE' and alias_of(s_) == cls
                        and abs(qty) > _eps):
                    per_acct[pacct] = per_acct.get(pacct, 0.0) + qty
            names = [_listing_key(t)] + list(
                _other_listings.get(_listing_key(t), ()))
            if len(per_acct) < 2 and len(names) < 2:
                return ""
            txt = f"all {_qfmt(abs(cls_tax_q))} taxable units"
            if len(names) > 1:
                txt += (f" of {', '.join(names[:-1])} and {names[-1]}, "
                        f"one security")
            if len(per_acct) > 1:
                txt += ": " + ", ".join(f"{a} {_qfmt(q)}" for a, q
                                        in sorted(per_acct.items()))
            return txt

        # The engine's verdict, loss by loss (the claim ledger above):
        # the units a holder acquired inside the loss's ±30-day window,
        # not spent on an earlier denial, AND still holds (Canada; LONG
        # acquisitions only — a re-short or a new written option never
        # triggers). (US: no radar-side test at all — the verdict is the
        # engine's disallowed amount on each loss; see WASHED.)
        violations = [l for l in ([] if us_mode else in_window_losses)
                      if float(l.get('denied_end') or 0.0)
                      > pool_qty_eps(ticker, float(l.get('qty') or 0.0))]

        # Anything held on either side (display threshold): a recent
        # loss with a position left is BLOCKED (don't add), else COOLING.
        held_any = (sum(abs(qty) for (t, grp, acct), qty
                        in account_pool_qty.items()
                        if alias_of(t) == cls and abs(qty) > _eps)
                    + sum(calls_held.values()))

        adv = ""
        is_relevant = False
        clear_in = "-"
        clears_at = None   # ABSOLUTE clear date for --json-out consumers
        settle_deadline = None   # VIOLATION only: the engine's settle bound
        rescue_j = None          # VIOLATION only: who must sell what
        denied_j = None          # VIOLATION only: units denied as things stand
        at_risk_j = None         # LOCKED only: taxable units a loss sale today
        loss_j = None            # BLOCKED/COOLING: (in-window loss, units sold)
        deadline_passed = False  # VIOLATION only: no rescue sale settles in time
        #                          would lose to a registered holder
        # US WASHED only: every washed loss is a .tt ALLOWLOSS filing
        # position (US-WASH-25), so the books allow it — the brief
        # view's action line says so (backlog TJ-0023).
        claimed_only = False
        # US: a §1256 contract (a futures contract, an option on one, a
        # broad-based index option) is outside §1091 (US-WASH-18) — its
        # loss is never disallowed, so there is no COOLING/BLOCKED
        # re-entry date (audit A2-0435 / A2-1343, TJ-0506).
        _us_exempt = us_mode and _outside_1091(ticker)
        # US: a short-cover loss is replaced only by a new SHORT sale
        # (§1091(e), US-WASH-05) — a long buy never disallows it (audit
        # A2-0436 / A2-1369). Stated as a note on the position's own
        # forward view, or as COOLING when nothing is held.
        short_cover_j = None
        _short_cover_note = None
        # Held only in registered accounts / IRAs, bought there in the
        # past 30 days: {account, date} of the latest such buy (the
        # category stays CLEAR; sell-check's SIMILAR caveat reads it).
        sheltered_buy_j = None

        if _us_exempt:
            if in_window_losses or abs(tax_q) > _eps or abs(shl_q) > _eps:
                is_relevant = True
                if in_window_losses:
                    _amt = sum(float(l['loss']) for l in in_window_losses)
                    _dates = ", ".join(sorted({l['date']
                                               for l in in_window_losses}))
                    adv = (f"CLEAR: the loss of {_money_txt(_amt)} on {_dates} is "
                           f"on a §1256 contract (a futures contract, an "
                           f"option on one, a broad-based index option), "
                           f"marked to market, so §1091 never disallows "
                           f"it (US-WASH-18); a re-purchase within 30 days "
                           f"is only flagged for a manual check.")
                else:
                    adv = ("CLEAR: a §1256 contract (a futures contract, "
                           "an option on one, a broad-based index option) "
                           "is marked to market — outside §1091 "
                           "(US-WASH-18): a loss sale is never disallowed; "
                           "a re-purchase within 30 days of it is only "
                           "flagged for a manual check.")
        elif in_window_losses:
            is_relevant = True
            # US: the engine already disallowed these (§1091). Nothing
            # the user does now changes it — no still-held test, so no
            # "rescue" (partition COMMANDS-01).
            _washed = ([l for l in in_window_losses
                        if float(l.get('disallowed') or 0.0) > 0.005]
                       if us_mode else [])
            # A loss a .tt ALLOWLOSS line claims (US-WASH-25): the books
            # allow it, so it is neither washed nor open — the radar
            # called it WASHED, "added to the replacement's basis"
            # (pre-release review).
            _claimed = ([l for l in in_window_losses if l.get('position')]
                        if us_mode else [])
            _unclaimed_open = [
                l for l in in_window_losses if not l.get('position')
                and float(l['loss'])
                - float(l.get('disallowed') or 0.0) > 0.01]
            if _claimed and not _washed and not _unclaimed_open:
                claimed_only = True
                cl_raw = sum(float(l['loss']) for l in _claimed)
                cl_dis = sum(float(l.get('would_disallow') or 0.0)
                             for l in _claimed)
                adv = (f"WASHED: the loss of {_money_txt(cl_raw)} on "
                       + ", ".join(sorted({l['date'] for l in _claimed}))
                       + " is claimed as your filing position against "
                         "§1091 ("
                       + ", ".join(sorted({f"ALLOWLOSS "
                                           f"{l['position'].get('where')}"
                                           for l in _claimed}))
                       + f"): the rule would disallow {_money_txt(cl_dis)}; the "
                         f"books allow it and add nothing to the "
                         f"replacement's basis. Delete the line to apply "
                         f"the rule.")
            elif _washed:
                dis = sum(float(l['disallowed']) for l in _washed)
                perm = sum(float(l.get('permanent') or 0.0)
                           for l in _washed)
                raw = sum(float(l['loss']) for l in _washed)
                dates = ", ".join(sorted({l['date'] for l in _washed}))
                reps: Dict[Any, float] = {}
                for l in _washed:
                    for r in l.get('replacements') or []:
                        k = (r.get('date') or '?', r.get('account') or '?',
                             bool(r.get('is_sheltered')))
                        reps[k] = reps.get(k, 0.0) + float(
                            r.get('match_qty') or 0.0)
                _short = all(l.get('direction') == 'SHORT'
                             for l in _washed)
                _verb = "shorted" if _short else "bought"
                rep_txt = "; ".join(
                    f"{'IRA ' if sh else ''}'{a}' {_verb} {_qfmt(q)} on {d}"
                    for (d, a, sh), q in sorted(reps.items()))
                denied_j = round(sum(float(l.get('denied_units') or 0.0)
                                     for l in _washed), 6)
                adv = (f"WASHED: the loss of {_money_txt(raw)} on {dates} is a "
                       f"wash sale (§1091): {_money_txt(dis)} disallowed — a "
                       f"replacement {'short was opened' if _short else 'was bought'}"
                       f" within 30 days"
                       + (f" ({rep_txt})" if rep_txt else "")
                       + ". There is no still-held test: selling the "
                         "replacement does not undo it.")
                if dis - perm > 0.005:
                    adv += (f" {_money_txt(dis - perm)} is added to the "
                            f"replacement's basis and comes back when "
                            f"that lot is sold.")
                if perm > 0.005:
                    adv += (f" {_money_txt(perm)} matched an IRA purchase and "
                            f"is lost for good.")
                if _claimed:
                    adv += (f" The loss of {_money_txt(sum(float(l['loss']) for l in _claimed))} "
                            f"on " + ", ".join(sorted(
                                {l['date'] for l in _claimed}))
                            + " is claimed as your filing position "
                              "(.tt ALLOWLOSS): the books allow it.")
                _open = [l for l in in_window_losses
                         if not l.get('position')
                         and float(l['loss'])
                         - float(l.get('disallowed') or 0.0) > 0.01]
                if _open:
                    safe_d = max(_reentry(l) for l in _open)
                    days_left = _days_to(safe_d)
                    clear_in = f"{safe_d} ({days_left}d)"
                    clears_at = safe_d
                    rem = sum(float(l['loss'])
                              - float(l.get('disallowed') or 0.0)
                              for l in _open)
                    adv += (f" The remaining {_money_txt(rem)} of loss is "
                            f"disallowed too if you buy again before "
                            f"{safe_d}.")
            elif violations:
                # Already superficial: a replacement acquired in the
                # window is still held. Rescue = every backing holder
                # exits what it holds beyond the units earlier denials
                # claimed (min(acquired, held) reaches 0 only there),
                # settling on or before loss_settle+30 — the engine's
                # held-at-end boundary. The date the user acts on is the
                # last TRADE date that settles in time, on the LISTING's
                # calendar (a TSX USD unit trades on TSX days — A2-1183);
                # the settle bound is shown too. Once that date has
                # passed, nothing rescues the loss (A2-0688).
                _dl = {id(l): _rescue_deadline(l, ticker)
                       for l in violations}
                open_v = [l for l in violations
                          if _dl[id(l)][1] >= _today_iso]
                passed_v = [l for l in violations
                            if _dl[id(l)][1] < _today_iso]
                denied_j = round(sum(float(l['denied_end'])
                                     for l in violations), 6)
                unit_word = ("contract(s)" if (is_option_ticker(ticker)
                                               or any(l.get('futures')
                                                      for l in violations))
                             else "units" if is_crypto_symbol(ticker)
                             else "shares")

                def _is_shl(k):
                    h = k[1] if k[0] == 'call' else k
                    return h[0] == 'SHELTERED'
                _per_of = {e['symbol']: e['per']
                           for e in call_acq.get(cls, []) if 'per' in e}
                _passed_txt = ""
                if passed_v:
                    _pw = min(passed_v, key=lambda l: l['epoch'])
                    _p_units = sum(float(l['denied_end']) for l in passed_v)
                    _p_shl = any(_is_shl(k) for l in passed_v
                                 for k in l.get('alloc') or {})
                    _passed_txt = (
                        f"the loss of {_money_txt(sum(float(l['loss']) for l in passed_v))}"
                        f" on {', '.join(sorted({l['date'] for l in passed_v}))}"
                        f" is superficial — the last trade date to rescue "
                        f"it ({_dl[id(_pw)][1]}, settling by "
                        f"{_dl[id(_pw)][0]}) has passed, so "
                        f"{_qfmt(_p_units)} units of it are denied ("
                        + ("the part a registered account backs is lost "
                           "PERMANENTLY; " if _p_shl else "")
                        + "a taxable replacement's part is added to its "
                          "ACB). No sale can undo it now.")
                if open_v:
                    worst = min(open_v, key=lambda l: l['epoch'])
                    settle_d, safe_d = _dl[id(worst)]
                    _same_day = bool(worst.get('same_day_settle')
                                     or worst.get('futures'))
                    days_left = int((date_to_epoch(safe_d) - today_epoch)
                                    / 86400)
                    clear_in = f"{safe_d} ({days_left}d)"
                    clears_at = safe_d
                    settle_deadline = settle_d
                    rescue: Dict[Any, dict] = {}
                    rescue_calls: Dict[Any, dict] = {}
                    for l in open_v:
                        for k in l.get('alloc') or {}:
                            _acct = (l.get('alloc_last') or {}).get(
                                k, (0, ''))[1]
                            _cl = float((l.get('claimed') or {}).get(k, 0.0))
                            if k[0] == 'call':
                                _ck = (k[1], k[2])
                                _held = calls_held.get(_ck, 0.0)
                                _per = _per_of.get(k[2]) or 1.0
                                _need = min(_held, max(
                                    0.0, _held - _cl / _per))
                                _prev = rescue_calls.get(_ck)
                                if _need > 1e-9 and (
                                        _prev is None
                                        or _need > _prev['held']):
                                    rescue_calls[_ck] = {'held': _need,
                                                         'account': _acct}
                            else:
                                _held = long_held.get(k, 0.0)
                                _need = min(_held, max(0.0, _held - _cl))
                                _prev = rescue.get(k)
                                if _need > 1e-9 and (
                                        _prev is None
                                        or _need > _prev['held']):
                                    rescue[k] = {'held': _need,
                                                 'account': _acct}
                    verb = ("Cover" if (us_mode and worst.get('direction',
                                                              'LONG')
                                        == 'SHORT')
                            else "Sell")
                    share_units = sum(b['held'] for b in rescue.values())
                    parts = [f"{_who(h, b['account'])} {_qfmt(b['held'])}"
                             for h, b in sorted(rescue.items())]
                    _what = (f"{share_units:.4f} {unit_word} "
                             f"({', '.join(parts)})" if rescue else "")
                    if rescue_calls:
                        _cparts = [f"{_who(k[0], c['account'])} {k[1]} "
                                   f"{_qfmt(c['held'])}"
                                   for k, c in sorted(rescue_calls.items())]
                        _cw = (f"{sum(c['held'] for c in rescue_calls.values()):g}"
                               f" long call contract(s) "
                               f"({', '.join(_cparts)})")
                        _what = f"{_what} and {_cw}" if _what else _cw
                    _shl = any(h[0] == 'SHELTERED' for h in rescue) or any(
                        k[0][0] == 'SHELTERED' for k in rescue_calls)
                    rescue_j = (
                        [{"holder": ('taxable' if h[0] == 'TAXABLE'
                                     else 'sheltered'),
                          "account": ('' if h[0] == 'TAXABLE'
                                      else (b['account'] or h[1])),
                          "symbol": ticker, "qty": b['held']}
                         for h, b in sorted(rescue.items())]
                        + [{"holder": ('taxable' if k[0][0] == 'TAXABLE'
                                       else 'sheltered'),
                            "account": ('' if k[0][0] == 'TAXABLE'
                                        else (c['account'] or k[0][1])),
                            "symbol": k[1], "qty": c['held'], "call": True}
                           for k, c in sorted(rescue_calls.items())])
                    _when = (f"by {safe_d} (it settles the same day)"
                             if _same_day else
                             f"by {safe_d} (last TRADE date"
                             f"{_either_listing(ticker)} — the sale "
                             f"must SETTLE by {settle_d})")
                    _open_units = sum(float(l['denied_end']) for l in open_v)
                    adv = (f"VIOLATION: {verb} {_what} "
                           f"{_when} to rescue the loss"
                           f" ({_qfmt(_open_units)} units denied as things "
                           f"stand)"
                           + ("; the part a registered account backs is "
                              "denied PERMANENTLY unless that account "
                              "sells too" if _shl else "")
                           + ".")
                    if _passed_txt:
                        adv += f" Earlier: {_passed_txt}"
                else:
                    worst = min(passed_v, key=lambda l: l['epoch'])
                    settle_d, safe_d = _dl[id(worst)]
                    clear_in = f"{safe_d} (passed)"
                    clears_at = safe_d
                    settle_deadline = settle_d
                    deadline_passed = True
                    adv = f"VIOLATION: {_passed_txt}"
            else:
                _sc_losses = [l for l in in_window_losses if us_mode
                              and l.get('direction', 'LONG') == 'SHORT']
                if _sc_losses:
                    # §1091(e): only a new SHORT sale replaces a
                    # short-cover loss — said as a note beside a long
                    # loss's re-entry date (a mixed row), or alone; the
                    # position's own view (below) still applies.
                    last = max(_sc_losses, key=lambda l: l['epoch'])
                    safe_d = max(_reentry(l) for l in _sc_losses)
                    _amt = sum(float(l['loss']) for l in _sc_losses)
                    short_cover_j = {"loss": round(_amt, 6),
                                     "date": last['date'],
                                     "reshort_ok_from": safe_d}
                    _short_cover_note = (
                        f"NOTE: the loss of {_money_txt(_amt)} on covering "
                        f"a short on {last['date']} is disallowed only by a "
                        f"new SHORT sale before {safe_d} (§1091(e)); buying "
                        f"the shares does not replace it.")
                # The losses a BUY today can still replace: their dates
                # and amounts are what BLOCKED / COOLING and buy-check
                # state. A US short-cover loss is not one (a re-short
                # replaces it: it set the wait and the amount — backlog
                # TJ-0559), nor a claimed filing position.
                _wait = [l for l in in_window_losses
                         if _buy_replaces(l) and _reentry(l) > _today_iso]
                if not _wait:
                    # Canada: every in-window loss's re-entry date has
                    # come — a buy traded today settles after its window,
                    # so the loss blocks nothing (backlog TJ-0186); the
                    # position's own view (below) applies. US: only
                    # short-cover losses are open (their note, above).
                    is_relevant = bool(_sc_losses)
                else:
                    # No held in-window replacement: the binding
                    # constraint is the LATEST re-entry date of the
                    # losses a buy can still replace (re-entry before it
                    # disallows).
                    last = max(_wait, key=lambda l: (_reentry(l),
                                                     l['epoch']))
                    safe_d = _reentry(last)
                    days_left = _days_to(safe_d)
                    clear_in = f"{safe_d} ({days_left}d)"
                    clears_at = safe_d
                    loss_amt = sum(l['loss'] for l in _wait)
                    loss_units = sum(float(l.get('qty') or 0.0)
                                     for l in _wait)
                    # A rebuy denies only the rebought units' share of
                    # the loss (min(bought, sold, still held) / sold),
                    # never the whole loss by itself — a 1-share DRIP
                    # after a 100-share loss sale costs 1% of it (audit
                    # S054-07). One unit's share: _per_unit_denial.
                    per_unit, per_upto = _per_unit_denial(_wait)
                    loss_j = (round(loss_amt, 6), round(loss_units, 6),
                              None if per_unit is None
                              else round(per_unit, 6),
                              None if per_upto is None
                              else round(per_upto, 6))
                    _per = ((f" — {_money_txt(per_unit)} of it for each "
                             f"unit bought back"
                             + (f" (the first {_qfmt(per_upto)})"
                                if per_upto is not None else ""))
                            if per_unit is not None else "")
                    if held_any > _eps:
                        # Still holding, but no replacement bought in the
                        # window is held: the loss is allowed so far —
                        # buying MORE before the window closes would
                        # disallow.
                        adv = (f"BLOCKED: Recent loss of "
                               f"{_money_txt(loss_amt)} on {last['date']}. "
                               f"Re-entry before {safe_d} disallows the "
                               f"loss on as many units as you buy{_per}.")
                    else:
                        # Fully exited at a loss: safe, just don't
                        # re-enter until the window closes.
                        adv = (f"COOLING: Loss of {_money_txt(loss_amt)} "
                               f"on {last['date']}. Safe to re-enter on "
                               f"{safe_d}.")
            if us_mode and not is_option_ticker(ticker) and any(
                    abs(e['epoch'] - l['epoch']) <= window_sec
                    for l in in_window_losses
                    if l.get('direction', 'LONG') == 'LONG'
                    and not l.get('is_option')
                    for e in call_acq.get(cls, [])):
                # US-WASH-12: flagged, never enforced.
                adv += (" NOTE: a long call on these shares was bought "
                        "inside the window — §1091 may treat it as an "
                        "option to acquire them; the US engine does not "
                        "disallow on it (a warning only), so check it by "
                        "hand.")

        # The rule's name in this project's law.
        _sl = "a wash sale" if us_mode else "a superficial loss"
        _sl_adj = "a wash sale" if us_mode else "superficial"
        _reg = "IRA(s)" if us_mode else "SHELTERED account(s)"
        # A SHORT position is closed by a cover, and in a US project the
        # trigger for a short-cover loss is a new SHORT sale (§1091(e),
        # US-WASH-05), never an IRA purchase: long wording ("Recent buy",
        # "Selling the FULL position", "an IRA buy ... disallows") was
        # reused for shorts (audit A2-0371 / A2-0686 / A2-1184).
        _short_pos = cls_tax_q < -1e-9

        if not adv and abs(tax_q) > _eps:
            # Forward view: a loss sale of the taxable position TODAY.
            # Its window reaches back 30 days from its own window date
            # (Canada: its settlement, _sale_epoch); each holder's
            # backing is what it bought in that span and still holds.
            crit = ('SHORT' if (us_mode and cls_tax_q < 0) else 'LONG')
            sale_ep = _sale_epoch(ticker)
            lo = sale_ep - window_sec
            tax_long = abs(cls_tax_q)
            # Units an earlier loss already claimed back nothing more
            # (CA-SL-08 / US-WASH-02 share for share): a registered buy
            # that backs a closed-window denial is not 'at risk' again
            # (audit A2-0377 / A2-0691).
            shl_back: Dict[Any, dict] = {}
            shl_calls: Dict[Any, dict] = {}
            tax_evs = []
            if us_mode:
                back = _backing_us(lo, sale_ep + window_sec, crit)
                shl_back = {h: b for h, b in back.items()
                            if h[0] == 'SHELTERED'}
                tax_evs = [e for e in acq_events.get(cls, [])
                           if e['holder'][0] == 'TAXABLE'
                           and e['dir'] == crit and e['epoch'] >= lo
                           and _ev_avail(e) > 1e-9]
            else:
                _cands, _caps, _cl = _capacity(
                    cls, lo, sale_ep + window_sec, float('inf'),
                    share_loss=not is_option_ticker(ticker))
                for k, e, f in _cands:
                    if _ev_avail(e) <= 1e-9:
                        continue
                    if k[0] == 'call':
                        if k[1][0] == 'TAXABLE':
                            if calls_held.get((k[1], k[2]), 0.0) > 1e-6:
                                tax_evs.append(e)
                            continue
                        if _caps.get(k, 0.0) <= 1e-6:
                            continue
                        d = shl_calls.setdefault((k[1], k[2]), {
                            'units': _caps[k],
                            'contracts': _caps[k] / (f or 1.0),
                            'last': e['epoch'], 'account': e['account'],
                            'last_date': _bought_on(e)})
                    elif k[0] == 'TAXABLE':
                        tax_evs.append(e)
                        continue
                    else:
                        if _caps.get(k, 0.0) <= 1e-6:
                            continue
                        d = shl_back.setdefault(k, {
                            'units': _caps[k],
                            'held': long_held.get(k, 0.0),
                            'last': e['epoch'], 'account': e['account'],
                            'last_date': _bought_on(e)})
                    if e['epoch'] >= d['last']:
                        d['last'], d['account'] = e['epoch'], e['account']
                        d['last_date'] = _bought_on(e)
            at_risk = min(tax_long,
                          sum(b['units'] for b in shl_back.values())
                          + sum(c['units'] for c in shl_calls.values()))
            # Taxable acquisitions in the window (a PARTIAL sale is
            # superficial; a full exit is not) and registered buyers
            # that have since sold out (no backing today; re-arms only
            # through a new buy).
            tax_acq = max(tax_evs, key=lambda e: e['epoch']) if tax_evs else None
            gone = [e for e in acq_events.get(cls, [])
                    if e['holder'][0] == 'SHELTERED' and e['dir'] == crit
                    and e['epoch'] >= lo and e['holder'] not in shl_back
                    and _ev_avail(e) > 1e-9
                    and long_held.get(e['holder'], 0.0) <= 1e-6]
            gone_last = max(gone, key=lambda e: e['epoch']) if gone else None
            shl_caveat = None
            if gone_last is not None:
                shl_caveat = (
                    f"NOTE: SHELTERED "
                    f"'{gone_last['account'] or 'unknown'}' bought "
                    f"{_bought_on(gone_last)} but holds none "
                    f"of it now — that leg re-arms only if an affiliated "
                    f"account re-buys within 30 days AFTER your sale.")
            pre_window_shl = (abs(cls_shl_q) > _eps and not shl_back)
            if at_risk > 1e-6:
                is_relevant = True
                latest = max([b['last'] for b in shl_back.values()]
                             + [c['last'] for c in shl_calls.values()]
                             + ([tax_acq['epoch']] if tax_acq else []))
                safe_d = _safe_sale_date(latest, ticker)
                days_left = _days_to(safe_d)
                clear_in = f"{safe_d} ({days_left}d)"
                clears_at = safe_d
                at_risk_j = round(at_risk, 6)
                # US: an IRA purchase in the window backs the denial
                # whatever the IRA holds now (no still-held test), so the
                # text states what it BOUGHT and what it holds (audit
                # A2-0751 / A2-0754: 'still holds 40' after selling all).
                who = "; ".join(
                    [(f"'{b['account'] or h[1] or 'unknown'}' bought "
                      f"{_qfmt(b['units'])} of those shares in the window "
                      f"(last {b['last_date']}; holds "
                      f"{_qfmt(max(0.0, b.get('held', 0.0)))} now)")
                     if us_mode else
                     (f"'{b['account'] or h[1] or 'unknown'}' bought "
                      f"{b['last_date']} and still holds "
                      f"{_qfmt(b['units'])} of those shares")
                     for h, b in sorted(shl_back.items())]
                    + [f"'{c['account'] or k[0][1] or 'unknown'}' bought "
                       f"{_qfmt(c['contracts'])} {k[1]} call(s) on "
                       f"{c['last_date']}"
                       for k, c in sorted(shl_calls.items())])
                if us_mode:
                    keep = ("that portion is permanently denied (an IRA "
                            "purchase in the window denies it even if the "
                            "IRA has sold)")
                else:
                    keep = ("that portion is permanently denied unless the "
                            "registered account sells them within 30 days "
                            "after your sale")
                adv = (f"LOCKED: Recent buy in {_reg}: {who}. "
                       + ("Covering the taxable short" if _short_pos
                          else "Selling the taxable position")
                       + f" at a loss before "
                       f"{safe_d} is {_sl} for up to "
                       f"{_qfmt(at_risk)} of your {_qfmt(tax_long)} "
                       f"shares — {keep}; the rest of the loss stands.")
                if tax_acq:
                    adv += (f" A PARTIAL loss sale is also {_sl_adj} for "
                            f"the taxable buy on "
                            f"{_bought_on(tax_acq)} (deferred "
                            f"into the remaining shares).")
            elif tax_acq:
                is_relevant = True
                safe_d = _safe_sale_date(tax_acq['epoch'], ticker)
                days_left = _days_to(safe_d)
                clear_in = f"{safe_d} ({days_left}d)"
                clears_at = safe_d
                _full = ("the FULL position (shares AND the long calls "
                         "bought in the window)" if any(
                             _call_underlying_cls(e['symbol']) == cls
                             and alias_of(e['symbol']) != cls
                             for e in tax_evs) else "the FULL position")
                _span = _full_position_span(ticker)
                if _span:
                    _full = (f"{_full[:-1]}; {_span})" if _full.endswith(")")
                             else f"{_full} ({_span})")
                # Taxable-only in-window buy: NOT a hard lock — the
                # still-held test fails if the ENTIRE position (incl. the
                # recent buy) is disposed, so a full exit realizes the
                # loss today. Only a PARTIAL loss sale is superficial.
                # US (A2-0553): there is no still-held test, but shares
                # sold in the SAME sale never replace each other
                # (US-WASH-17) — so the full exit must be one order.
                _one = " in one order" if us_mode else ""
                if crit == 'SHORT' or _short_pos:
                    adv = (f"EXITABLE: Recent "
                           f"{'short sale' if crit == 'SHORT' else 'buy'} in "
                           f"'{tax_acq['account'] or 'unknown'}' on "
                           f"{_bought_on(tax_acq)}. Covering "
                           f"the FULL short{_one} at a loss is fine now; a "
                           f"PARTIAL cover at a loss before {safe_d} is "
                           f"{_sl_adj} (the loss defers into the shares "
                           f"still short).")
                else:
                    adv = (f"EXITABLE: Recent buy in "
                           f"'{tax_acq['account'] or 'unknown'}' on "
                           f"{_bought_on(tax_acq)}. Selling "
                           f"{_full}{_one} at a loss is fine now; a "
                           f"PARTIAL loss sale before {safe_d} is "
                           f"{_sl_adj} (basis defers into the remaining "
                           f"shares).")
                if pre_window_shl and us_mode:
                    adv += (f" IRAs hold {_qfmt(abs(cls_shl_q))} sh "
                            f"bought before the window — they do not make "
                            f"this loss a wash sale, but an IRA buy "
                            f"(dividend reinvestment too) within 30 days "
                            f"AFTER the sale would, permanently.")
                elif pre_window_shl:
                    adv += (f" Sheltered accounts hold "
                            f"{_qfmt(abs(cls_shl_q))} sh bought before the "
                            f"window — they do not make this loss "
                            f"superficial, but a sheltered buy (a DRIP too) "
                            f"within 30 days AFTER the sale would, "
                            f"permanently.")
                if shl_caveat:
                    adv += f" {shl_caveat}"
            elif shl_caveat:
                is_relevant = True
                safe_d = _safe_sale_date(gone_last['epoch'], ticker)
                days_left = _days_to(safe_d)
                clear_in = f"{safe_d} ({days_left}d)"
                clears_at = safe_d
                # No holder owns property acquired in the window (the
                # taxable pool bought none; the registered buyer sold
                # out), so a loss sale of ANY size today stands — the
                # per-holder test, as the engine applies it. The old
                # "IF you exit your FULL taxable position" implied a
                # partial harvest would be superficial (audit S054-18).
                adv = (f"CAUTION: a loss sale today is NOT superficial, "
                       f"whole or partial — no account holds shares "
                       f"bought in the last 30 days. {shl_caveat}")

        if (not adv and abs(tax_q) > _eps and abs(cls_shl_q) > _eps
                and not (us_mode and _short_pos)):
            # No acquisition on EITHER side within the past 30 days:
            # s.40(2)(g) needs an acquisition INSIDE the ±30-day
            # window, not mere ownership — so a loss sale TODAY is
            # claimable. The sheltered holding matters for the FORWARD
            # half only: an affiliated add (a DRIP is the classic)
            # within 30 days after the sale denies the loss
            # PERMANENTLY (registered-account basis is unrecoverable).
            is_relevant = True
            if _short_pos:
                adv = ("RISK: Covering at a loss is fine NOW (no buys in "
                       "the last 30 days) — but sheltered accounts still "
                       "hold, so a buy of the shares (a DRIP included) "
                       "within 30 days AFTER the cover that is still held "
                       "30 days after it denies the loss on as many shares "
                       "as it buys. Pause DRIPs/sheltered adds for 30 "
                       "days.")
            elif us_mode:
                adv = ("RISK: Sellable at a loss NOW (no buys in the last "
                       "30 days) — but an IRA still holds, so an IRA "
                       "buy (dividend reinvestment included) within 30 "
                       "days AFTER the sale disallows the loss "
                       "PERMANENTLY on as many shares as it buys (Rev. "
                       "Rul. 2008-5). Pause IRA buys/reinvestment for 30 "
                       "days.")
            else:
                adv = ("RISK: Sellable at a loss NOW (no buys in the last "
                       "30 days) — but sheltered accounts still hold, so "
                       "an affiliated buy (including a DRIP) within 30 "
                       "days AFTER the sale denies the loss PERMANENTLY "
                       "on as many shares as it buys (a small DRIP denies "
                       "a small part). Pause DRIPs/sheltered adds for 30 "
                       "days, or sell the sheltered shares too.")

        if not adv and _short_cover_note:
            # Nothing else to say about the position: the short-cover
            # loss's own window (a re-short is what disallows it).
            adv = (f"COOLING: Loss of {_money_txt(short_cover_j['loss'])} on "
                   f"covering a short on {short_cover_j['date']}. Do not "
                   f"short it again before "
                   f"{short_cover_j['reshort_ok_from']} — a new short sale "
                   f"in the window disallows it (§1091(e)); buying the "
                   f"shares does not.")
            clears_at = short_cover_j['reshort_ok_from']
            clear_in = (f"{clears_at} ("
                        f"{int((date_to_epoch(clears_at) - today_epoch) / 86400)}d)")
        elif _short_cover_note:
            adv += " " + _short_cover_note

        if not adv:
            if abs(tax_q) > _eps or abs(shl_q) > _eps:
                is_relevant = True
                if abs(tax_q) <= _eps:
                    # Held only in registered accounts / IRAs: nothing to
                    # sell at a loss, and "No recent buys" was false for a
                    # sheltered purchase in the last 30 days (audit
                    # A2-0751 / A2-1370) — name it; the taxable row of
                    # the property it makes superficial says the rest.
                    # Every event is a booked trade (made by today); the
                    # window is a sale today's (its settlement in
                    # Canada), so a buy made today that settles tomorrow
                    # is named too, as _capacity counts it (backlog
                    # TJ-0245, audit TJ-0415).
                    _sale_ep = _sale_epoch(ticker)
                    _recent = [e for e in acq_events.get(cls, [])
                               if e['holder'][0] == 'SHELTERED'
                               and e['symbol'] == ticker
                               and _sale_ep - window_sec <= e['epoch']
                               <= _sale_ep]
                    _bought = ""
                    if _recent:
                        _e = max(_recent, key=lambda e: e['epoch'])
                        _bought = (f" ('{_e['account'] or _e['holder'][1] or 'unknown'}'"
                                   f" bought it {_bought_on(_e)})")
                        sheltered_buy_j = {
                            "account": (_e['account'] or _e['holder'][1]
                                        or 'unknown'),
                            "date": _bought_on(_e)}
                    adv = (f"CLEAR: Held only in {_reg}{_bought} — "
                           f"selling it there has no tax effect"
                           + ("; while it is held, that purchase can "
                              "make a taxable loss on "
                              + ("the shares it acquires " if
                                 is_option_ticker(ticker) else
                                 "the same property ")
                              + f"{_sl_adj} (see the taxable row)."
                              if _recent else "."))
                elif _short_pos and is_option_ticker(ticker):
                    # A written option: it is bought back, and only the
                    # identical contract ever replaces it (CA-SL-06 /
                    # US-WASH-03) — never "the shares".
                    adv = ("CLEAR: "
                           + ("no recent writes of this contract; "
                              if us_mode else "no recent buys; ")
                           + "safe to buy it back at a loss (do not "
                           + ("write it again" if us_mode
                              else "buy the same contract again")
                           + " for 30 days).")
                elif _short_pos and us_mode:
                    adv = ("CLEAR: no recent short sales; safe to cover at "
                           "a loss (do not short it again for 30 days: a "
                           "new short sale is the replacement, §1091(e)).")
                elif _short_pos:
                    adv = ("CLEAR: no recent buys; safe to cover at a loss "
                           "(do not buy the shares for 30 days).")
                else:
                    adv = ("CLEAR: no recent buys; safe to sell at a loss "
                           "(do not repurchase for 30 days).")
            # (Fully-exited recent losses are routed to COOLING in the loss
            # branch above.)

        notes = []
        if not is_option_ticker(ticker) and adv:
            notes += _flag_notes([
                {'symbol': l.get('symbol') or ticker,
                 'date': epoch_to_date(l['epoch']),
                 'amount': -float(l.get('loss') or 0.0),
                 'direction': l.get('direction', 'LONG'), 'id': ''}
                for l in in_window_losses
                if not l.get('is_option')
                and l.get('direction', 'LONG') == 'LONG'])
            _named = {n.split(' ', 2)[1] for n in notes}
            if abs(tax_q) > _eps and tax_q > 0:
                # A purchase already named against an in-window loss is
                # not named again for a sale today (one line per buy).
                notes += [n for n in _flag_notes([{'symbol': ticker,
                                       'date': _today_iso, 'amount': -1.0,
                                       'direction': 'LONG', 'id': ''}],
                                     today_view=True)
                          if n.split(' ', 2)[1] not in _named]
                if us_mode and 'NOTE: a long call' not in adv and any(
                        today_epoch - window_sec <= e['epoch']
                        <= today_epoch
                        for e in call_acq.get(cls, [])):
                    # US-WASH-12 for a sale TODAY: the engine will flag
                    # it (call_vs_share_loss); say so before the sale
                    # (audit A2-0687).
                    notes.append(
                        "NOTE: a long call on these shares was bought in "
                        "the last 30 days — §1091 may treat it as an "
                        "option to acquire them, so a loss sale now may "
                        "be a wash sale; the US engine only flags it (a "
                        "warning), so check it by hand.")
            notes = [n for n in dict.fromkeys(notes) if n not in adv]
            if notes:
                adv += " " + " ".join(notes)
        if _short_cover_note and _short_cover_note in adv \
                and _short_cover_note not in notes:
            notes.append(_short_cover_note)

        # The class's losses as records (`taxjson rebuy`): every loss
        # still in its window, plus (--cleared-days N) those whose
        # re-entry date passed in the last N days. `rebuy_at` is the
        # re-entry date of the latest loss a BUY can still deny: Canada
        # every in-window loss (s.54: any acquisition held at day 30);
        # USA not a loss the engine already disallowed in full, nor a
        # short-cover loss (only a re-short replaces it, §1091(e)), nor
        # a claimed filing position, nor a futures loss (US-WASH-18).
        losses_j = []
        rebuy_at = None
        _lookback = 30 + max(0, int(args.cleared_days or 0))
        for l in sorted(recent_losses.get(cls, []),
                        key=lambda x: (x['epoch'], x.get('seq', 0))):
            _age = (today_epoch - l['epoch']) / 86400
            if _age > _lookback + 7:
                continue
            _re = _reentry(l)
            # Open while a buy traded today would still replace it: up
            # to its re-entry date (a weekend before a US one included).
            _open = _re > _today_iso
            if _age > _lookback and not _open:
                continue
            _buy = not _us_exempt and _buy_replaces(l)
            if _open and _buy and (rebuy_at is None or _re > rebuy_at):
                rebuy_at = _re
            _rb = (_rescue_deadline(l, ticker)[1]
                   if not us_mode and float(l.get('denied_end') or 0.0)
                   > pool_qty_eps(ticker, float(l.get('qty') or 0.0))
                   else None)
            losses_j.append({
                "symbol": l.get('symbol') or ticker,
                "account": l.get('account') or '',
                "tx_id": l.get('tx_id') or '',
                "trade_date": l.get('date'),
                "window_date": epoch_to_date(l['epoch']),
                "loss": round(float(l['loss']), 6),
                "qty": round(float(l.get('qty') or 0.0), 6),
                "is_option": bool(l.get('is_option')),
                "direction": l.get('direction', 'LONG'),
                "rebuy_at": _re,
                "cleared": not _open,
                "buy_replaces": bool(_buy),
                # Canada: units denied as things stand (a replacement
                # bought in the window still held); USA: the engine's
                # disallowed amount.
                "denied_qty": (None if us_mode else round(
                    float(l.get('denied_end') or 0.0), 6)),
                "disallowed": (round(float(l.get('disallowed') or 0.0), 6)
                               if us_mode else None),
                # Canada, a loss denied as things stand: the last trade
                # date of its own rescue sale, and whether it has passed
                # (then the denial is final) — per loss, so `rebuy` never
                # offers a final denial as rescuable (backlog TJ-0079).
                "rescue_by": _rb,
                "rescue_passed": bool(_rb and _rb < _today_iso),
            })

        # The latest purchase of the class inside the window of a loss
        # sale today (Canada: still held by its buyer; US: any), in any
        # account — what a look-alike's SIMILAR caveat in sell-check
        # names. The category says it only for LOCKED / EXITABLE /
        # CAUTION: a VIOLATION's replacement bought 9 days ago went
        # unnamed, plain SAFE (backlog TJ-0449).
        recent_buy_j = None
        if not is_option_ticker(ticker) and acq_events.get(cls):
            _rb_lo = _sale_epoch(ticker) - window_sec
            _rb = [e for e in acq_events.get(cls, [])
                   if e['dir'] == 'LONG' and e['epoch'] >= _rb_lo
                   and (us_mode or long_held.get(e['holder'], 0.0) > 1e-6)]
            if _rb:
                _e = max(_rb, key=lambda e: (e['epoch'], e['seq']))
                recent_buy_j = {
                    "account": (_e['account'] or _e['holder'][1]
                                or 'unknown'),
                    # The trade date, as the broker shows it.
                    "date": _bought_on(_e)}

        if (args.all or is_relevant) and _shown(ticker):
            table_data.append([ticker, _qfmt(tax_q), _qfmt(shl_q), clear_in,
                               adv, claimed_only])
            row_records.append({
                "ticker": ticker,
                "taxable_qty": tax_q, "taxable_display": _qfmt(tax_q),
                "sheltered_qty": shl_q, "sheltered_display": _qfmt(shl_q),
                "clears_at": clears_at,
                # VIOLATION only: the engine's settle-date bound behind
                # the trade-date clears_at (None elsewhere).
                "settle_deadline": settle_deadline,
                # VIOLATION only: units denied as things stand, and who
                # must sell what to rescue the loss (every backing
                # holder exits its whole holding of the class).
                "denied_qty": denied_j,
                "rescue": rescue_j,
                # VIOLATION only: the last rescue trade date has passed —
                # the loss is denied and no sale undoes it (A2-0688).
                "deadline_passed": deadline_passed,
                # LOCKED only: taxable units whose loss a sale TODAY
                # would lose to a registered holder's in-window buy.
                "at_risk_qty": at_risk_j,
                # BLOCKED / COOLING only: the in-window loss and the
                # units sold at it — a rebuy denies loss/units per unit
                # bought back (buy-check states it).
                "recent_loss": (loss_j[0] if loss_j else None),
                "recent_loss_qty": (loss_j[1] if loss_j else None),
                # BLOCKED / COOLING only: the share of those losses ONE
                # unit bought back now denies, and the units it holds
                # for (None: as many as were sold) — _per_unit_denial
                # (backlog TJ-0560).
                "recent_loss_per_unit": (loss_j[2] if loss_j else None),
                "recent_loss_per_unit_qty": (loss_j[3] if loss_j
                                             else None),
                "clears_in_at_generation": clear_in,
                "advisory": adv,
                # Warn-only flags inside the advisory, one per line for
                # the tools that print their own verdict line (sell-check
                # CLEAR, buy-check).
                "notes": notes,
                # US only: a futures contract / futures option, outside
                # §1091 (US-WASH-18) — buy-check and sell-check say so
                # instead of a re-entry date.
                "outside_wash_rule": bool(_us_exempt),
                # US only: the in-window losses are short covers, which
                # only a new SHORT sale replaces (§1091(e)) — a buy is
                # not a replacement ({loss, date, reshort_ok_from}).
                "short_cover_loss": short_cover_j,
                # CLEAR, held only in registered accounts / IRAs: the
                # latest buy there in the past 30 days ({account, date};
                # None otherwise) — a look-alike's in-window purchase
                # in any account (sell-check's SIMILAR caveat, CA-SL-20
                # / US-WASH-26).
                "sheltered_buy": sheltered_buy_j,
                # The latest purchase of the class inside the window of
                # a loss sale today, still held (Canada; any in the US):
                # {account, date} or None (sell-check's SIMILAR caveat
                # for a look-alike in any category — backlog TJ-0449).
                "recent_buy": recent_buy_j,
                # The class's recent losses and the first day a buy no
                # longer replaces any of them (`taxjson rebuy`; None
                # when no in-window loss is one a buy can deny).
                "losses": losses_j,
                "rebuy_at": rebuy_at,
                "category": _advisory_category(adv),
            })

    # Group rows by advisory category (alphabetical by ticker within each).
    _order = _COUNTRY_ORDER[args.country]
    table_data.sort(key=lambda r: (_category_rank(_advisory_category(r[4])), r[0]))
    by_cat: Dict[str, list] = {}
    for r in table_data:
        by_cat.setdefault(_advisory_category(r[4]), []).append(r)
    # Every advisory section prints in the fixed order — even empty ones
    # — so a clean `VIOLATION (0)` / `BLOCKED (0)` is visible at a
    # glance rather than silently absent. Any extra category (e.g. OTHER
    # from --all) follows.
    extras = [c for c in by_cat if c not in _order]
    # The SIMILAR groups (lib/similar): each member's status, a caveat.
    from taxjson.lib.similar import radar_lines
    _groups = [[s.strip().upper() for s in g.split()] for g in args.similar
               if g.strip()]
    _status = {str(r[0]).strip().upper(): cat for cat, rows_ in by_cat.items()
               for r in rows_ if cat}
    similar_lines = radar_lines(_groups, _status)
    _shown_for = ""
    if _show_paths:
        _shown_for = args.account or ", ".join(sorted(
            Path(f).name[:-len("_base.json")]
            if Path(f).name.endswith("_base.json") else Path(f).stem
            for f in args.show))
    report = (_render_brief(_order + extras, by_cat, args.country,
                            _today_iso, bool(args.all), similar_lines,
                            _shown_for)
              if args.brief else
              _render_text(_order + extras, by_cat, args.country,
                           _today_iso, bool(args.all), similar_lines,
                           _shown_for))

    if args.json_out or args.json:
        # Structured sidecar: same rows/grouping as the printed .rpt, plus
        # ABSOLUTE clears_at dates so consumers (harvest --radar) can
        # compute "clears in Nd" at their own time instead of reading the
        # generation-day countdown.
        recs_by_cat = {}
        row_records.sort(key=lambda r: (_category_rank(r["category"]),
                                        r["ticker"]))
        for rec in row_records:
            recs_by_cat.setdefault(rec["category"], []).append(rec)
        extras_j = [c for c in recs_by_cat if c not in _order]
        sections_j = [{
            "category": cat,
            "title": _category_title(cat, args.country,
                                     _claim_state(by_cat.get(cat, []))),
            "rows": recs_by_cat.get(cat, []),
        } for cat in _order + extras_j]
        payload = {
            "schema_version": 1,
            "country": args.country,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "as_of_date": today_dt.strftime("%Y-%m-%d"),
            "account": args.account,
            "include_all": bool(args.all),
            # True when the rows are one account's listings (--show);
            # the statuses still pool every book.
            "rows_scoped": bool(_show_paths),
            "scope_note": scope_note(args.country),
            "sections": sections_j,
            # The SIMILAR groups with a member's status (lib/similar).
            "similar": [{"members": g,
                         "status": {s: _status.get(s) for s in g
                                    if _status.get(s)}}
                        for g in _groups if any(_status.get(s) for s in g)],
        }
        if args.json_out:
            out_path = Path(args.json_out)
            # 'cannot write <path>: ...', not the input wording
            # (re-audit A2-0707), for the folder as well (A2-1428: a
            # parent that is a file was a FileExistsError traceback).
            from taxjson.lib.cli_diag import (OutputWriteError,
                                              write_text_atomic)
            try:
                out_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                why = (f"{out_path.parent} is not a folder"
                       if isinstance(e, (FileExistsError,
                                         NotADirectoryError))
                       else (e.strerror or str(e)))
                raise OutputWriteError(
                    f"cannot write --json-out {out_path}: {why}") from None
            write_text_atomic(out_path, json.dumps(payload, indent=2,
                                                   sort_keys=True) + "\n")

    report.print()

    if args.json:
        # Discard the buffered text report and emit only the payload.
        sys.stdout = orig_stdout
        print(json.dumps(payload, indent=2, sort_keys=True))


_US_DEFINITIONS = (
    "WASHED: a loss sale with a replacement bought within 30 days either "
    "side (trade dates), IRAs included: disallowed (§1091), no still-held "
    "test; the loss moves into the replacement's basis (lost in an IRA).",
    "BLOCKED: a loss sale in the last 30 days, some still held: a buy "
    "before the printed date disallows the loss on as many shares as you "
    "buy.",
    "LOCKED: an IRA bought in the last 30 days: a taxable loss sale is a "
    "wash sale for that many shares, permanently (Rev. Rul. 2008-5).",
    "EXITABLE: bought in a taxable account in the last 30 days: a FULL "
    "exit at a loss is fine, a partial loss sale is a wash sale.",
    "COOLING: sold out at a loss recently: wait 31 days from the sale "
    "(trade date) before buying back.",
    "RISK: sellable at a loss NOW, but an IRA still holds: an IRA buy "
    "(a DRIP too) within 30 days after disallows the loss permanently.",
    "CLEAR: no recent buys; safe to sell at a loss (no buy-back for 30 "
    "days).",
    "A long call bought in a loss's window is noted, not enforced; after "
    "a loss on covering a short only a new short sale replaces it "
    "(§1091(e)); futures and their options are §1256, outside §1091.",
)


_CA_DEFINITIONS = (
    "VIOLATION: a loss sale, and a holder that bought the same security "
    "inside the 30-day window either side still holds it: that holder "
    "sells ALL of it by the printed TRADE date (its settlement within 30 "
    "days of the loss's; crypto settles on its trade date) to rescue the "
    "loss. Shares held before the window never make a loss superficial.",
    "BLOCKED: a loss sale in the last 30 days: buying back cancels the "
    "loss on as many shares as you buy and still hold 30 days after it "
    "(s.54; the per-unit amount is printed).",
    "LOCKED: a registered account bought in the last 30 days and still "
    "holds: a taxable loss sale is superficial for that many shares, "
    "permanently unless that account sells them within 30 days after.",
    "EXITABLE: bought in a taxable account in the last 30 days: a FULL "
    "exit at a loss is fine, a partial loss sale is superficial.",
    "CAUTION: a registered account bought recently and has since sold: "
    "a loss sale stands unless an affiliated account re-buys within 30 "
    "days after.",
    "COOLING: sold out at a loss recently: wait 30 days from the sale "
    "before buying back.",
    "RISK: sellable at a loss NOW, but a sheltered account still holds: "
    "an affiliated buy (a DRIP too) within 30 days after denies the loss "
    "permanently; pause sheltered adds for 30 days.",
    "CLEAR: no recent buys; safe to sell at a loss (no buy-back for 30 "
    "days).",
)


# The one-line action a status asks for (the brief view; --details
# defines every status). A status not here asks for nothing.
_ACTIONS = {
    "canada": {
        "VIOLATION": "sell all of {t} by its CLEARS date to keep the loss",
        "BLOCKED": "do not buy {t} before its CLEARS date",
        "LOCKED": "do not sell {t} at a loss before its CLEARS date",
        "EXITABLE": "sell all of {t} or none at a loss",
        "RISK": "{t}: no sheltered buy (DRIP) 30 days after a loss sale",
    },
    "usa": {
        "WASHED": "{t}: the loss is disallowed; no sale undoes it",
        "BLOCKED": "do not buy {t} before its CLEARS date",
        "LOCKED": "do not sell {t} at a loss (an IRA bought it)",
        "EXITABLE": "sell all of {t} or none at a loss",
        "RISK": "{t}: no IRA buy for 30 days after a loss sale",
    },
}
# A WASHED row whose every washed loss is a .tt ALLOWLOSS filing
# position (US-WASH-25): the books allow the loss, so "disallowed" was
# false (backlog TJ-0023).
_CLAIMED_ACTION = ("{t}: the loss is claimed as your filing position "
                   "(ALLOWLOSS): allowed, no basis added")


def _few(names, n: int = 3) -> str:
    names = list(names)
    return ", ".join(names[:n]) + (f" +{len(names) - n} more"
                                   if len(names) > n else "")


_SIMILAR_NOTE = ("Similar funds (ticker.map SIMILAR) are two securities "
                 "for the rule: one's status is a caveat for the others, "
                 "not a verdict.")


def _similar_block(doc: "_out.Doc", similar_lines) -> None:
    """The SIMILAR groups that have a member with an advisory: one line
    each, then the one-sentence note."""
    if not similar_lines:
        return
    doc.blank()
    doc.line("SIMILAR")
    for ln in similar_lines:
        doc.item(ln, indent="  ")
    doc.para(_SIMILAR_NOTE)


def _shown_for_line(shown_for: str) -> str:
    """The scoped view's legend line (`wash-radar ACCOUNT`)."""
    return (f"ROWS: the listings of {shown_for}; every account's books "
            f"decide each status (TAXABLE: all your taxable accounts).")


def _render_brief(sections, by_cat, country: str, as_of: str,
                  include_all: bool, similar_lines=(),
                  shown_for: str = "") -> "_out.Doc":
    """The radar's essentials (docs/output-style.md, Essentials first):
    a legend, ONE table of the positions with an advisory (its STATUS
    first, in the fixed order), then one `! ` line per status that asks
    for an action and a pointer to --details (the advisories, the
    definitions, the scope). The rows are _render_text's."""
    us = country == "usa"
    doc = _out.Doc(
        "WASH RADAR — "
        + ("wash sales (§1091, trade dates)" if us
           else "superficial losses (s.54, settlement dates)")
        + f", as of {as_of}")
    body = [[cat or "OTHER", *r[:4]] for cat in sections
            for r in by_cat.get(cat, [])]
    if body:
        # A VIOLATION's CLEARS is its sell-by date, not the day a buy
        # is safe (rebuy's REBUY ON; backlog TJ-0137). One line within
        # 100 columns either way.
        doc.para("STATUS: what a loss sale or a buy does today; CLEARS: "
                 + ("window closes (VIOLATION: last day to sell)."
                    if by_cat.get("VIOLATION")
                    else "the day its window closes."))
        if shown_for:
            doc.para(_shown_for_line(shown_for))
        doc.table(["STATUS", "TICKER", "TAXABLE", "SHELTERED", "CLEARS"],
                  body, aligns=["<", "<", ">", ">", "<"], per_record=False)
    else:
        doc.para("No position with an advisory"
                 + (f" in {shown_for}." if shown_for else "."))
    acts = []
    n_notes = 0
    for cat in sections:
        rows_c = by_cat.get(cat, [])
        # A note to check by hand is one the engines flag and never
        # size (a warrant, a call on an adjusted series, a futures
        # option, a US long call: "... check it by hand"); a CAUTION
        # row's sheltered caveat or a short cover's re-short date is
        # information, not a check (backlog TJ-0451).
        n_notes += sum(1 for r in rows_c
                       for n in advisory_parts(str(r[4] or ""))[1]
                       if "by hand" in n)
        what = _ACTIONS["usa" if us else "canada"].get(cat)
        groups = [(what, rows_c)]
        if cat == "WASHED":
            _cl = [len(r) > 5 and bool(r[5]) for r in rows_c]
            groups = [(what, [r for r, c in zip(rows_c, _cl) if not c]),
                      (_CLAIMED_ACTION,
                       [r for r, c in zip(rows_c, _cl) if c])]
        for what, rows_g in groups:
            if not (rows_g and what):
                continue
            for n in (3, 2, 1, 0):
                line = f"{cat}: " + what.format(
                    t=_few([r[0] for r in rows_g], n) if n else
                    f"{len(rows_g)} position(s)")
                if cat != "WASHED":
                    line = _out.act(line, "tjs wash-radar --details")
                if len(line) <= 100:
                    break
            acts.append(line)
    if n_notes:
        acts.append(_out.act(f"{n_notes} warn-only note(s) to check by "
                             f"hand", "tjs wash-radar --details"))
    if acts:
        doc.blank()
        for a in acts:
            # Wrapped only on a terminal narrower than the line.
            if a.startswith(_out.ACT):
                doc.item(a[len(_out.ACT):], bullet=_out.ACT)
            else:
                doc.para(a)
    _similar_block(doc, similar_lines)
    if not acts and not similar_lines:
        doc.blank()
    doc.para(scope_more("tjs wash-radar --details", country))
    return doc


def _render_text(sections, by_cat, country: str, as_of: str,
                 include_all: bool, similar_lines=(),
                 shown_for: str = "") -> "_out.Doc":
    """The radar report in the house layout (docs/output-style.md): a
    title, one section per advisory category (heading with its count;
    `(none)` when empty) holding a TICKER / TAXABLE / SHELTERED / CLEARS
    table, each advisory printed once as a wrapped paragraph under the
    rows it applies to, then the definitions as a list and the scope
    paragraph. The rows and advisories are the --json document's."""
    us = country == "usa"
    doc = _out.Doc(
        "WASH RADAR — "
        + ("wash sales (§1091, trade dates)" if us
           else "superficial losses (s.54, settlement dates)")
        + f", as of {as_of}"
        + ("" if include_all else ", positions with an advisory"))
    if shown_for:
        doc.para(_shown_for_line(shown_for))
    headers = ["TICKER", "TAXABLE", "SHELTERED", "CLEARS"]
    for cat in sections:
        rows_c = by_cat.get(cat, [])
        doc.blank()
        doc.line("\n".join(_out.wrap(
            f"{_category_title(cat, country, _claim_state(rows_c))} "
            f"({len(rows_c)})", doc.w,
            "", "  ")))
        if not rows_c:
            doc.line("  (none)")
            continue
        # The advisory without its "<CATEGORY>:" lead (the heading says
        # it); rows that share one advisory share one paragraph, the
        # groups in the order of their first ticker.
        groups: Dict[str, list] = {}
        for r in rows_c:
            adv = str(r[4] or "")
            if cat and adv.startswith(f"{cat}:"):
                adv = adv[len(cat) + 1:].strip()
            groups.setdefault(adv, []).append(r)
        ordered = [(adv, grp) for adv, grp in groups.items()]
        body = [r[:4] for _adv, grp in ordered for r in grp]
        table = _out.fit_table(headers, body, aligns=["<", ">", ">", "<"],
                              indent="  ", per_record=False, width_=doc.w)
        doc.line("\n".join(table[:2]))
        i = 2
        for adv, grp in ordered:
            doc.line("\n".join(table[i:i + len(grp)]))
            i += len(grp)
            if adv:
                doc.line("\n".join(advisory_lines(adv, doc.w, "    ")))
    _similar_block(doc, similar_lines)
    doc.section("DEFINITIONS")
    for d in (_US_DEFINITIONS if us else _CA_DEFINITIONS):
        doc.item(d, indent="  ")
    # Every SAFE/CLEAR here is "as far as this project's accounts show"
    # (CA-PLAN-04 / US-PLAN-04, audit S054-22).
    doc.blank()
    doc.line("\n".join(scope_lines(country, doc.w)))
    return doc


if __name__ == "__main__":
    main()
