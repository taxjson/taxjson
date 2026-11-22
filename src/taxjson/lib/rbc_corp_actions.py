"""RBC Direct Investing's corporate actions (`taxjson-corp-actions`,
bin/taxjson_corp_actions: the extractor for broker rbc_direct): the
mergers and spin-offs an RBC export's rows record (name changes, splits
and 1-for-1 exchanges are the parser's), read with the parser's own rows (lib/brokerages/rbc_direct read_rbc_rows)
and paired by lib/corp_actions' RBC reorganization rules into
CorporateAction events (parse_rbc_corporate_actions). Out of
lib/corp_actions, which called up into the RBC parser for it while the
parser imports corp_actions (step 8 of the architecture plan: lib's
import cycle)."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from taxjson.lib.corp_actions import (
    CorporateAction, _RBC_MERGER_TO_RE, _RBC_OLDCO_RE, _num_text,
    _rbc_account, _rbc_is_real_ticker, _rbc_leg_is_option, _rbc_name_tokens,
    _rbc_norm_company, _rbc_removal_names, _rbc_same_account,
    _rbc_short_merger_leg, _rbc_tok_eq, _renamed, pair_rbc_reorganizations,
    rbc_is_temp_symbol, rbc_ratio_parts, rbc_row_company,
    rbc_spinoff_cil_fraction)
from taxjson.lib.stage_msg import emit_line
from taxjson.model import money as M


_RBC_SUFFIXER = None


def _rbc_ca_symbol(symbol: str, currency: str) -> str:
    """The brokerage parser's own symbol for an RBC row: its
    `apply_currency_suffix`, with the canonical-listing rules (FTN.PRA ->
    FTN.PR.A.TO, a Venture / CSE suffix -> ROOT.TO, an unmapped currency
    as the parser spells it). A private copy of the old suffix rule booked
    a preferred share's merger on FTN.PRA.TO while its trades were on
    FTN.PR.A.TO (A2-0223, the S014-07 twin)."""
    global _RBC_SUFFIXER
    if _RBC_SUFFIXER is None:
        from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
        _RBC_SUFFIXER = RbcBrokerage()
    return _RBC_SUFFIXER.apply_currency_suffix(
        (symbol or '').strip(), (currency or '').upper() or 'USD')


_RBC_SPINOFF_RE = re.compile(
    r'\bSPIN\s?OFF\s+ON\s+([\d,]*\.?\d+)\s+SH(?:S|ARES?)?\s+FROM\s+SEC#\s*'
    r'(\S+)\s+(.+?)(?:\s+REC\s+\d\d/|\s*$)', re.I)


def _rbc_context_rows(rows, other_files) -> List[Any]:
    """The rows of the account's OTHER exports that this export (`rows`)
    does not already hold. A row overlapping exports repeat counts once
    — as many copies as the export holding the most of it, the way the
    brokerage parser's overlap plan counts them (two genuinely identical
    postings in one export stay two). Excluding only the rows of this
    export let two other exports' copies of one cash-in-lieu payment
    both reach the spin-off, doubling its cash and fraction (TJ-0755)."""
    from taxjson.lib.brokerages.rbc_direct import _row_content_key
    have: Dict[tuple, int] = defaultdict(int)
    for r in rows:
        have[(_rbc_account(r), _row_content_key(r))] += 1
    out: List[Any] = []
    for f_rows in other_files:
        groups: Dict[tuple, List[Any]] = {}
        for r in f_rows:
            groups.setdefault((_rbc_account(r), _row_content_key(r)),
                              []).append(r)
        for key, rs in groups.items():
            if len(rs) > have[key]:
                out.extend(rs[have[key]:])
                have[key] = len(rs)
    return out


def _rbc_row_homes(file_rows) -> Dict[int, set]:
    """{id(row): the exports (their places in `file_rows`) holding that
    broker row}: a row an overlapping download repeats is at home in
    both, so a payment in the re-download is still its event's."""
    from taxjson.lib.brokerages.rbc_direct import _row_content_key
    where: Dict[tuple, set] = defaultdict(set)
    keyed = []
    for i, f_rows in enumerate(file_rows):
        for r in f_rows:
            k = (_rbc_account(r), _row_content_key(r))
            where[k].add(i)
            keyed.append((id(r), k))
    return {rid: where[k] for rid, k in keyed}


def parse_rbc_corporate_actions(
    csv_path: Path, account: str = 'RBC',
    context_files: Optional[List[Path]] = None,
    renames: Optional[Dict[str, str]] = None,
) -> List[CorporateAction]:
    """Extract election events from an RBC Direct Investing activity CSV:
    mergers ("<OLDCO> MERGER TO <NEWCO> <ratio> NEW = <n> OLD" removal +
    "<NEWCO> SHRS RECEIVED THRU MERGER" receipt, paired by
    `pair_rbc_reorganizations`) and spin-offs ("DIS - <NEWCO> SPINOFF ON
    <n> SHS FROM SEC# <code> <PARENT>"). Name changes, splits and 1-for-1
    exchanges need no election; the brokerage parser books those.

    RBC reports no FMV and no ISIN; FMV comes from the election hint, and
    the symbols stand in for the ISINs in the event-id hash.

    `renames` — the project's ticker.map renames (`tjs run` passes
    them): a spin-off booked under a temporary code that the map already
    renames needs no "map it" warning."""
    from taxjson.lib.brokerages.base import income_facts_from_description
    from taxjson.lib.brokerages.rbc_direct import read_rbc_rows
    rows = read_rbc_rows(Path(csv_path)).rows

    # Company name → the real ticker it trades under (a merger removal is
    # booked under a temporary code; the same security's dividend / trade
    # rows carry the real ticker under the same 'Symbol Description').
    # Every export of the account is searched (this one first): the
    # position was often bought in an earlier year's export.
    lookup_rows = list(rows)
    other_files: List[list] = []
    for other in context_files or []:
        if Path(other).resolve() == Path(csv_path).resolve():
            continue
        try:
            other_files.append(read_rbc_rows(Path(other)).rows)
        except Exception:
            continue
        lookup_rows.extend(other_files[-1])
    # Every row of the account once, this export's own rows first (they
    # stay the representatives a copy elsewhere is folded into).
    _all_rows = list(rows) + _rbc_context_rows(rows, other_files)
    # name -> {symbol: {listing currencies}}. Only SHARE lines count (an
    # option's code and description name the issuer too, and an s.86.1
    # ADJUST pointed at the option's empty pool became a phantom gain —
    # audit S019-06). A symbol's listing is the currency its TRADES
    # settle in (the parser suffixes trades by row currency); rows that
    # never trade (dividends) fall back to their own currency. Keeping
    # every listing, not the first seen, lets an interlisted company be
    # told apart by the removal's currency — or refused as ambiguous —
    # instead of by file order (audits S019-05, S074-09).
    name_listings: Dict[str, Dict[str, set]] = {}
    trade_cur: Dict[str, set] = {}
    other_cur: Dict[str, set] = {}
    share_rows = []
    for r in lookup_rows:
        if not (r.symbol and _rbc_is_real_ticker(r.symbol)) \
                or _rbc_leg_is_option(r) \
                or getattr(r, 'cls', '') in ('reorg', 'spinoff', 'cil'):
            continue
        share_rows.append(r)
        cur = (r.currency or '').upper()
        (trade_cur if getattr(r, 'cls', '') in ('trade', 'transfer')
         else other_cur).setdefault(r.symbol, set()).add(cur)
        name = _rbc_norm_company(rbc_row_company(r))
        if name:
            name_listings.setdefault(name, {}).setdefault(r.symbol, set())

    def _listings(sym: str) -> set:
        return trade_cur.get(sym) or other_cur.get(sym) or set()

    from taxjson.lib.brokerages.rbc_direct import (_POSITION_CLASSES,
                                                   _signed_qty)
    _moves = set(_POSITION_CLASSES) | {'split'}

    def _row_of(x, sym: str) -> bool:
        """`x` moves `sym`'s shares: under the ticker, or a split under a
        temporary code whose company trades as that one ticker (TJ-1137,
        the build_rbc_account_context gap TJ-1089 closed)."""
        if x.symbol == sym:
            return True
        if getattr(x, 'cls', '') != 'split' or not rbc_is_temp_symbol(x.symbol):
            return False
        name = _rbc_norm_company(rbc_row_company(x))
        return set(name_listings.get(name) or ()) == {sym}

    def _position(sym: str, cur: str, date: str) -> float:
        """Shares of one listing held at the end of `date`: every row of
        the account's exports (an overlapping copy once) that moves that
        symbol's shares in that currency, signed as the broker parser
        signs them. Trades and transfers alone missed a stock split's
        added shares: after a 2-for-1 split and a sale of more than the
        pre-split count the parent listing still held read as sold out,
        and the spin-off's parent was left unresolved (TJ-0764)."""
        return sum(_signed_qty(x) for x in _all_rows
                   if _row_of(x, sym) and (x.currency or '').upper() == cur
                   and getattr(x, 'cls', '') in _moves
                   and x.date <= date)

    def _resolve(syms, prefer_cur: str = '', what: str = '',
                 on_date: str = '') -> Optional[str]:
        """One (symbol, listing) from candidate share symbols, as the
        parser's suffixed symbol; None (with a warning) when several
        listings remain after preferring `prefer_cur`, then the one
        listing held on `on_date` — a listing traded only before (sold
        out of) is not the parent (A2-0210, the Questrade twin's
        S073-05/S074-08 rule)."""
        opts = sorted({(sym, c) for sym in syms for c in _listings(sym)})
        if not opts:
            return None
        if len(opts) > 1 and prefer_cur:
            pick = [o for o in opts if o[1] == prefer_cur.upper()]
            if len(pick) == 1:
                opts = pick
        if len(opts) > 1 and on_date:
            held = [o for o in opts if _position(*o, on_date) > 1e-9]
            if len(held) == 1:
                opts = held
        if len(opts) > 1:
            shown = ', '.join(_rbc_ca_symbol(sy, c) for sy, c in opts)
            emit_line(f"warning: RBC {what}: the company trades under several "
                      f"listings in this account ({shown}) — not guessed. "
                      f"Map the one that holds the shares with a ticker.map "
                      f"GLOBAL line.")
            return None
        return _rbc_ca_symbol(*opts[0])

    pairing = pair_rbc_reorganizations(rows)
    # Every row of the account once, paired as the brokerage parser pairs
    # them: the cash in lieu that belongs to an event, in whichever
    # export of the account it posts.
    _homes = _rbc_row_homes([rows] + other_files) if other_files else None
    _all_pairing = (pair_rbc_reorganizations(_all_rows, _homes) if other_files
                    else pairing)
    merger_pairs = [ev for ev in pairing.events if ev.kind == 'merger']
    unmatched = list(pairing.unmatched)
    # Legs of one merger split across two statements (a December removal,
    # a January receipt): pair this file's leftover legs with the other
    # statements' leftovers, as IB and Questrade do. Only a removal held
    # in THIS file emits the event; a row an overlapping export repeats
    # is not a second leg (A2-0214).
    if other_files and unmatched:
        def _key(x):
            return (x.date, x.symbol, M.units(x.qty, 9), x.desc, x.currency,
                    x.account)
        own_keys = {_key(x) for x in rows}
        pool = [x for x in unmatched]
        pool += [x for x in rows if getattr(x, 'cls', '') == 'cil']
        for f_rows in other_files:
            other = pair_rbc_reorganizations(f_rows)
            pool += [x for x in other.unmatched if _key(x) not in own_keys]
            pool += [x for x in f_rows if getattr(x, 'cls', '') == 'cil'
                     and _key(x) not in own_keys]
        cross = pair_rbc_reorganizations(pool, _homes)
        own_left = {id(x) for x in unmatched}
        for ev in cross.events:
            if id(ev.removal) in own_left or id(ev.receipt) in own_left:
                own_left.discard(id(ev.removal))
                own_left.discard(id(ev.receipt))
                if ev.kind == 'merger' and any(
                        ev.removal is x for x in unmatched):
                    merger_pairs.append(ev)
        unmatched = [x for x in unmatched if id(x) in own_left]
    events: List[CorporateAction] = []
    for leg in unmatched:
        if _rbc_short_merger_leg(leg):
            emit_line(f"warning: RBC merger leg on {leg.date} ({leg.symbol}, qty "
                f"{leg.qty:g}) is a merger of a SHORT position — taxjson "
                f"cannot book it (the legs' signs are inverted). NOTHING "
                f"was booked: record the cover of the old short and the "
                f"new short by hand in a .tt file. {leg.desc[:90]!r}")
            continue
        if leg.qty < 0 and _RBC_MERGER_TO_RE.search(leg.desc):
            oldm = _RBC_OLDCO_RE.search(leg.desc)
            emit_line(f"warning: RBC merger removal on {leg.date} "
                f"({(oldm.group(1).strip() if oldm else leg.symbol)!r}, "
                f"qty {leg.qty:g}) has NO matching share receipt within "
                f"7 days in any statement of this account — taxjson "
                f"cannot book it. Add the statement holding the receipt "
                f"row, or record the exchange by hand in a .tt file and "
                f"mark the event `ignore`.")
            # Blocking, as on IB: the run stops until the user acts,
            # instead of continuing with the old shares neither removed
            # nor exchanged (A2-0214).
            src = _rbc_ca_symbol(leg.symbol, leg.currency)
            events.append(CorporateAction(
                date=leg.date, time='09:30:00', action_type='unsupported',
                source_symbol=src, source_isin=f"{src}@{leg.date}",
                target_symbol='?', target_isin='?',
                ratio_new=0.0, ratio_old=0.0,
                qty_disposed=abs(leg.qty), qty_received=0.0, fmv=0.0,
                currency=leg.currency or 'USD',
                target_currency=leg.currency or 'USD', account=account,
                raw_descriptions=[leg.desc],
                broker_account=_rbc_account(leg)))
    for ev in merger_pairs:
        rem, rc = ev.removal, ev.receipt
        rr = rbc_ratio_parts(rem.desc)
        if rr and rr[0] > 0 and rr[1] > 0:
            ratio_new, ratio_old = rr
        elif ev.ratio:
            # "XCH TO <other company>; 1 FOR 5" (no NEW = OLD phrase).
            ratio_new, ratio_old = ev.ratio, 1.0
        else:
            ratio_new, ratio_old = 1.0, 1.0
        oldm = _RBC_OLDCO_RE.search(rem.desc)
        src_raw = rem.symbol
        src = _rbc_ca_symbol(src_raw, rem.currency)
        if not _rbc_is_real_ticker(src_raw):
            oldco = _rbc_norm_company(oldm.group(1) if oldm
                                      else _rbc_removal_names(rem)[0])
            syms = (name_listings.get(oldco)
                    or name_listings.get(_rbc_norm_company(rem.symdesc)))
            resolved = _resolve(syms or {}, rem.currency,
                                f"merger removal {rem.symbol} on "
                                f"{rem.date}", rem.date) if syms else None
            if resolved:
                src = resolved
            elif not syms:
                emit_line(f"warning: RBC merger removal for "
                    f"{(oldm.group(1).strip() if oldm else rem.symbol)!r} is "
                    f"booked under temporary reorg symbol {rem.symbol!r}, and "
                    f"no other row in the statement trades under that company — "
                    f"the rollover / disposition has no source lot to act on. If "
                    f"this position isn't in the imported history, add a manual "
                    f"opening lot or a ticker.map GLOBAL line for "
                    f"{_rbc_ca_symbol(rem.symbol, rem.currency)}.")
        tgt = _rbc_ca_symbol(rc.symbol, rc.currency)
        # The cash in lieu from every export of the account: a payment
        # posted in a later export than both share legs was lost when
        # the legs paired in this one (TJ-0756).
        cils = next((x.cil for x in _all_pairing.events
                     if x.removal is rem
                     and x.kind in ('merger', 'reorg')), ev.cil)
        # SIGNED: a reversal CIL row cancels its posting (audit S063-19).
        cil_amount = max(0.0, sum(c.value for c in cils))
        events.append(CorporateAction(
            date=rem.date, time='09:30:00', action_type='merger',
            source_symbol=src, source_isin=src,
            target_symbol=tgt, target_isin=tgt,
            ratio_new=ratio_new, ratio_old=ratio_old,
            qty_disposed=abs(rem.qty), qty_received=rc.qty,
            fmv=0.0, currency=rem.currency or 'USD',
            target_currency=rc.currency or 'USD',
            account=account,
            cash_in_lieu=cil_amount,
            cash_in_lieu_currency=(cils[0].currency if cils else ''),
            # The day the cash was paid: the fraction is sold for it then
            # (as the parser books a reorganization's cash in lieu), not
            # on the exchange date (TJ-0866).
            cash_in_lieu_date=min((c.date for c in cils), default=''),
            raw_descriptions=[rem.desc, rc.desc] + [c.desc for c in cils],
            broker_account=_rbc_account(rem),
        ))

    # Spin-offs: a tax election (s.86.1 or an FMV dividend in kind).
    # A "REVERSE ENTRY" row cancels its posting (same symbol and
    # account, negated quantity, on or before it; the nearest), in
    # whichever statement of the account holds it — as the reorg legs'
    # step 1 does. A reversed-and-rebooked spin-off was offered twice and
    # the reversal read as a short parent (A2-0213).
    spin_all = [x for x in _all_rows if getattr(x, 'cls', '') == 'spinoff']
    # Cash in lieu of a spin-off's fraction, in whichever statement of
    # the account it posts — the pairing the parser makes over every
    # statement, so the rows it leaves to this stage are the ones booked.
    # Overlapping exports' copies of one payment count once (TJ-0755).
    spin_cil = {id(sp): cils for sp, cils in _all_pairing.spinoff_cil}
    reversed_ids: set = set()

    def _spin_parent(x) -> str:
        m = _RBC_SPINOFF_RE.search(x.desc or '')
        return m.group(2).strip().upper() if m else ''

    def _same_spinoff(x, rev) -> bool:
        """The same distribution: its listing currency and, when both
        rows name it, its parent (SEC# code). A bare ticker is not unique
        across exchanges: a USD reversal cancelled a same-ticker CAD
        spin-off of another parent, by row order (TJ-0762)."""
        if (x.currency or '').upper() != (rev.currency or '').upper():
            return False
        a, b = _spin_parent(x), _spin_parent(rev)
        return not a or not b or a == b
    for rev in sorted((x for x in spin_all if x.qty < 0
                       and 'REVERSE ENTRY' in (x.desc or '').upper()),
                      key=lambda x: x.date):
        cands = [x for x in spin_all
                 if x.qty > 0 and id(x) not in reversed_ids
                 and x.symbol == rev.symbol
                 and abs(x.qty + rev.qty) < 1e-9 and x.date <= rev.date
                 and _rbc_same_account(x, rev) and _same_spinoff(x, rev)]
        if cands:
            orig = max(cands, key=lambda x: x.date)
            reversed_ids.update((id(orig), id(rev)))
    for r in rows:
        if getattr(r, 'cls', '') != 'spinoff' or id(r) in reversed_ids:
            continue
        if r.qty < 0:
            # Spun-off shares DEBITED: the account was short the parent
            # and owes them. Snapped to a long buy it booked a negative
            # dividend and a phantom long (audit S072-04).
            emit_line(f"warning: RBC spin-off on {r.date} DEBITS {abs(r.qty):g} "
                      f"{r.symbol} — the parent was held SHORT. taxjson cannot "
                      f"book a spin-off on a short position: NOTHING was "
                      f"booked; record the owed shares by hand in a .tt file. "
                      f"{r.desc[:90]!r}")
            continue
        m = _RBC_SPINOFF_RE.search(r.desc)
        parent_qty = _num_text(m.group(1), where=r.label()) if m else 0.0
        parent_code = m.group(2).strip() if m else ''
        parent_name = m.group(3).strip().lstrip('*') if m else ''
        what = f"spin-off parent {parent_name or parent_code!r} on {r.date}"
        syms = (name_listings.get(_rbc_norm_company(parent_name))
                if parent_name else None)
        if not syms and parent_name:
            # Fuzzy fallback (RBC appends 'COMMON STOCK' and the like, so
            # exact names rarely hit). BOTH names must be mostly covered
            # by the other: scoring only the shorter one let 'BROOKFIELD
            # CORP' match 'BROOKFIELD RENEWABLE CORP' (audit S072-02).
            def _cov(a, b):
                ta, tb = _rbc_name_tokens(a), _rbc_name_tokens(b)
                if not ta or not tb:
                    return 0.0
                return sum(1 for x in ta
                           if any(_rbc_tok_eq(x, y) for y in tb)) / len(ta)
            scored = {}
            for x in share_rows:
                xname = rbc_row_company(x)
                if not xname or x.symbol == r.symbol:
                    continue
                sc = min(_cov(parent_name, xname),
                         _cov(xname, parent_name))
                if sc >= 0.6:
                    scored[x.symbol] = max(sc, scored.get(x.symbol, 0.0))
            if scored:
                top = max(scored.values())
                syms = {sy for sy, sc in scored.items() if sc == top}
        parent = _resolve(syms, '', what, r.date) if syms else None
        if not parent and not syms:
            emit_line(f"warning: RBC spin-off parent {parent_name or parent_code!r} "
                      f"(SEC# {parent_code}) is not traded in this statement, so "
                      f"its ticker is unknown — a tax-deferred spin-off's "
                      f"parent cost reduction would land on an empty pool. Include the "
                      f"statement that bought the parent.")
        # The parent's OWN listing, never the spun-off row's currency: a
        # TSX parent whose spin-off arrives in USD was PARENT.US, and the
        # s.86.1 ACB reduction became a phantom gain (audit S019-05).
        src = parent or (parent_code or '(unknown parent)')
        tgt = _rbc_ca_symbol(r.symbol, r.currency)
        if rbc_is_temp_symbol(r.symbol) and not _renamed(tgt, renames):
            # Named as the books carry it (X000006.TO): the bare code in
            # a GLOBAL line matched nothing, and the warning used to stay
            # after the line was added (audit S072-03).
            emit_line(f"warning: RBC spin-off on {r.date} is booked under the "
                      f"temporary code {tgt} ({r.symdesc or r.desc[:60]!r}) "
                      f"— once the listed ticker is known, add to ticker.map:  "
                      f"GLOBAL {tgt} <TICKER>.{tgt.rsplit('.', 1)[-1]}")
        # SIGNED: a reversal CIL row cancels its posting (S063-19).
        cils = spin_cil.get(id(r), [])
        cil_amount = max(0.0, sum(c.value for c in cils))
        events.append(CorporateAction(
            date=r.date, time='09:30:00', action_type='spinoff',
            source_symbol=src, source_isin=parent_code or src,
            target_symbol=tgt, target_isin=tgt,
            ratio_new=r.qty, ratio_old=parent_qty or 1.0,
            qty_disposed=0.0, qty_received=r.qty,
            fmv=0.0, currency=r.currency or 'USD',
            target_currency=r.currency or 'USD',
            account=account,
            cash_in_lieu=cil_amount,
            cash_in_lieu_currency=(cils[0].currency or '' if cils else ''),
            fraction_in_lieu=(rbc_spinoff_cil_fraction(cils)
                              if cil_amount > 0 else 0.0),
            cash_in_lieu_date=min((c.date for c in cils), default=''),
            # Who is entitled, as Questrade's (a US §355 allocation,
            # TJ-0951, TJ-1193).
            record_date=income_facts_from_description(r.desc).get(
                'record_date', ''),
            raw_descriptions=[r.desc] + [c.desc for c in cils],
            broker_account=_rbc_account(r),
        ))
    events.sort(key=lambda e: (e.date, e.source_symbol))
    return events


parse_rbc_corporate_actions.accepts_context = True
parse_rbc_corporate_actions.accepts_renames = True
