"""In-kind moves between a TAXABLE account and a REGISTERED account of the
same taxpayer (tax-logic CA-INKIND-01..06 / US-INKIND-01..03).

Shares that leave a taxable account into an RRSP, RRIF, TFSA, FHSA,
LIRA/LIF ... (a contribution in kind), or come back from one (a
withdrawal in kind), are not custody moves: the taxpayer's ownership
changes. `tjs run` finds them and books them:

* the PAIR: a taxable account's transfer-out row and a registered
  account's transfer-in row (or the reverse) of the same security
  (after ticker.map's undated renames) and quantity, within PAIR_DAYS
  days, across brokers. Legs of ONE account pair first (a journal),
  then legs of the same kind (taxable <-> taxable, registered <->
  registered: a move of your own, the closest date first) across the
  whole project, and only then a taxable leg with a registered one — an
  in-kind move. A cross-kind pair is booked only when neither leg has
  another plausible partner (any other leg of the same security and
  quantity in the window, of either kind, outside a journal): else it
  is AMBIGUOUS, listed NOT booked with the line that settles it;
* a `.tt` INKIND line in the taxable account's folder values a pair,
  declares it (it wins over the ambiguity and over a same-kind pair;
  `plan=` picks the plan's leg), declares one whose plan is outside
  the project, or (`plan=own`) declares the transfer row a move of your
  own, never in kind (bin/taxjson_convert_tt.parse_inkind_line). A
  declared transfer row with no partner of its quantity takes the
  plan's legs that add up to it (a delivery in parts) when one set
  does; an undeclared one in that shape is warned about (`in_parts`);
* the FAIR MARKET VALUE, in order: the INKIND line; the market value the
  broker states on the transfer row (IB's Transfers `Market Value`,
  `market_value` on the row); Yahoo's close on the date (the caller's
  `close` lookup, an ESTIMATE). The books convert it at the Bank of
  Canada rate of the date like any row;
* Canada: a contribution is a sale at fair market value on the transfer
  date (IN_KIND_CONTRIBUTION_TYPE: a loss is denied for good, s.40(2)
  (g)(iv)); the plan's acquisition is a purchase for s.54 (the run marks
  its row in the loss-rule context, mark_sheltered). A withdrawal is a
  purchase at fair market value (IN_KIND_WITHDRAWAL_TYPE);
* USA: an IRA, Roth, 401(k) or HSA takes contributions in cash only — a
  pair from a taxable account into one is a likely error: warned, not
  booked. A distribution in kind is a purchase at fair market value.

Pure functions over plain dicts; `tjs run` feeds the transfer rows,
the INKIND lines and the close lookup, and writes the rows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _date
from typing import (Any, Callable, Dict, Iterable, List, Optional, Sequence,
                    Tuple)

from taxjson.lib.core import (IN_KIND_CONTRIBUTION_TYPE,
                              IN_KIND_DISPOSITION_TYPE,
                              IN_KIND_WITHDRAWAL_TYPE)

# The most days between the two legs of one move (a transfer between two
# brokers can take a week).
PAIR_DAYS = 10
# The most days an in-kind move's in leg may post after its out leg when
# nothing else could be either leg's partner (the same window as a
# slower move of your own, transfer_in.SLOW_MOVE_DAYS): an ATON move to
# or from an RRSP often takes one to three weeks. Such a pair is named,
# NOT booked, until an INKIND line says it was one move (backlog
# TJ-0437: its taxable leg passed in silence, the deemed sale missing
# and the shares held in both accounts).
SLOW_DAYS = 45
_EPS = 1e-6

CONTRIBUTION = "contribution"
WITHDRAWAL = "withdrawal"
# `INKIND ... plan=own`: the transfer row is a move of your own.
OWN = "own"
# The most search steps a delivery in parts is looked for in (`_parts`):
# past them nothing is proven unique, and nothing is booked.
_PARTS_STEPS = 1 << 16

# Canada: the plans s.40(2)(g)(iv) names — a loss on a disposition to a
# trust governed by an RRSP (a LIRA is one), RRIF (a LIF / LRIF is one),
# TFSA, FHSA, RDSP (or a DPSP) is nil. "sheltered" (an account whose plan
# the project does not name) is taken as one of them, and the warning
# says so. An RESP or a PRPP is not named: the loss is an ordinary one.
CA_LOSS_DENIED_PLANS = frozenset({"rrsp", "rrif", "tfsa", "fhsa", "rdsp",
                                  "lira", "lif", "lrif", "sheltered"})

# What a withdrawal's value is on a slip (shown, never booked: the books
# hold capital property only).
CA_WITHDRAWAL_INCOME = {
    "rrsp": "that value is income on your T4RSP",
    "lira": "that value is income on your T4RSP",
    "rrif": "that value is income on your T4RIF",
    "lif": "that value is income on your T4RIF",
    "lrif": "that value is income on your T4RIF",
    "fhsa": ("that value is income on your T4FHSA unless it is a "
             "qualifying withdrawal"),
    # An RESP pays three ways and the transfer row does not say which:
    # a refund of contributions is tax-free with no slip; the plan's
    # income and grants are an educational assistance payment (the
    # student's T4A) or an accumulated income payment (the subscriber's
    # T4A). Every withdrawal used to be called the first kind of income
    # (backlog TJ-0926).
    "resp": ("a refund of contributions is not income (no slip); paid "
             "from the plan's income or grants it is an educational "
             "assistance payment on the student's T4A or an accumulated "
             "income payment on the subscriber's T4A — the plan's "
             "statement says which"),
    "rdsp": "that value is partly income on your T4A(RDSP)",
    "prpp": "that value is income on your T4A",
    "tfsa": "",
}
US_WITHDRAWAL_INCOME = {
    "roth": "the taxable part, if any, is on your Form 1099-R",
}
US_WITHDRAWAL_DEFAULT = "the taxable amount is on your Form 1099-R"


def _d(s: Any) -> Optional[_date]:
    try:
        return _date.fromisoformat(str(s or "")[:10])
    except ValueError:
        return None


def _gap(a: Any, b: Any) -> int:
    da, db = _d(a), _d(b)
    return abs((da - db).days) if da and db else 10 ** 6


@dataclass
class Leg:
    """One transfer row of one of your accounts."""
    account: str
    broker: str
    row: Dict[str, Any] = field(repr=False)
    key: str                    # the security after ticker.map renames
    plan: str = ""              # "" = a taxable account; else its plan

    @property
    def registered(self) -> bool:
        return bool(self.plan)

    @property
    def qty(self) -> float:
        try:
            return float(self.row.get("quantity") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @property
    def date(self) -> str:
        return str(self.row.get("date") or "")[:10]

    @property
    def symbol(self) -> str:
        return str(self.row.get("symbol") or "")

    @property
    def currency(self) -> str:
        return str(self.row.get("currency") or "").upper()

    def ident(self) -> Tuple[str, str, str, float]:
        """(account, symbol, date, signed quantity): the row's identity
        for the transfers view and the books' filters."""
        return (self.account, self.symbol, self.date, round(self.qty, 8))


@dataclass
class Move:
    """One in-kind move between a taxable and a registered account."""
    kind: str                   # CONTRIBUTION | WITHDRAWAL
    taxable: Leg
    registered: Optional[Leg]   # None: declared by an INKIND line, its
    #                             plan outside the project
    plan: str
    quantity: float             # > 0
    date: str                   # the booking date (the taxable leg's,
    #                             or the INKIND line's)
    fmv: Optional[float] = None  # the whole value, in `currency`
    currency: str = ""
    source: str = ""            # "tt" | "broker" | "yahoo"
    source_text: str = ""       # how the value was found, for a person
    estimated: bool = False
    problem: str = ""           # why it is not booked ("" = booked);
    #                             "ambiguous": see `alternatives`
    line: Optional[Dict[str, Any]] = None   # the INKIND line used
    # The other legs that could be either leg's partner (problem
    # "ambiguous"): the move is not booked until a line says which.
    alternatives: List[Leg] = field(default_factory=list)
    # The plan's legs when it received (sent) the shares in parts — a
    # declared move only; else [] and `registered` is the one leg.
    parts: List[Leg] = field(default_factory=list)
    # The days between its legs when they post more than PAIR_DAYS and
    # at most SLOW_DAYS apart (the in leg after the out leg); 0 within
    # the window. Undeclared, such a move's problem is "slow".
    slow: int = 0

    @property
    def registered_legs(self) -> List[Leg]:
        return list(self.parts) or ([self.registered] if self.registered
                                    else [])

    @property
    def symbol(self) -> str:
        return self.taxable.symbol

    @property
    def key(self) -> str:
        return self.taxable.key

    @property
    def booked(self) -> bool:
        return not self.problem and self.fmv is not None

    @property
    def other_account(self) -> str:
        return (self.registered.account if self.registered
                else f"a {self.plan.upper() if self.plan != 'sheltered' else 'registered'} "
                     f"plan outside the project")

    def pair_text(self) -> str:
        a, b = self.taxable.account, self.other_account
        return f"{a} → {b}" if self.kind == CONTRIBUTION else f"{b} → {a}"

    def as_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "taxable": self.taxable.account,
                "registered": (self.registered.account if self.registered
                               else ""),
                "plan": self.plan, "symbol": self.symbol, "key": self.key,
                "quantity": self.quantity, "date": self.date,
                "taxable_leg": list(self.taxable.ident()),
                "registered_leg": (list(self.registered.ident())
                                   if self.registered else None),
                "registered_legs": [list(g.ident())
                                    for g in self.registered_legs],
                "alternatives": [list(g.ident())
                                 for g in self.alternatives],
                "fmv": self.fmv, "currency": self.currency,
                "source": self.source, "source_text": self.source_text,
                "estimated": self.estimated, "problem": self.problem,
                "slow": self.slow, "booked": self.booked}


def legs(rows: Iterable[Tuple[str, str, Dict[str, Any]]],
         plans: Dict[str, str], *,
         key: Optional[Callable[[str], str]] = None) -> List[Leg]:
    """Leg per (account, broker, TRANSFER row) of a security; `plans`:
    account -> its plan ("" for a taxable account). Rows of an account
    `plans` does not name are left out."""
    keyf = key or (lambda s: str(s or "").upper())
    out = []
    for acct, broker, t in rows:
        if acct not in plans or not isinstance(t, dict):
            continue
        if t.get("action") != "TRANSFER" or not t.get("symbol"):
            continue
        lg = Leg(account=acct, broker=broker, row=t,
                 key=keyf(str(t.get("symbol") or "")), plan=plans[acct])
        if abs(lg.qty) > _EPS and lg.date:
            out.append(lg)
    return out


def _fits(o: Leg, n: Leg, days: int) -> bool:
    """`o` (an out leg) and `n` (an in leg) can be one move's two legs:
    the same security and quantity, within `days` days."""
    return (o.key == n.key and abs(-o.qty - n.qty) <= _EPS
            and _gap(o.date, n.date) <= days)


def _rank(o: Leg, n: Leg) -> Tuple[Any, ...]:
    return (_gap(o.date, n.date), o.date, n.date, o.account, n.account)


def _slow_gap(o: Leg, n: Leg, days: int, slow_days: int) -> int:
    """The days from out leg `o` to in leg `n` when they can be one
    slower move's legs — the same security and quantity, the in leg
    `days` + 1 to `slow_days` days AFTER the out leg (an arrival never
    comes from a later departure) — else 0."""
    if o.key != n.key or abs(-o.qty - n.qty) > _EPS:
        return 0
    do, dn = _d(o.date), _d(n.date)
    if do is None or dn is None:
        return 0
    gap = (dn - do).days
    return gap if days < gap <= slow_days else 0


def _make(o: Leg, n: Leg) -> Move:
    if o.registered:
        return Move(WITHDRAWAL, taxable=n, registered=o, plan=o.plan,
                    quantity=n.qty, date=n.date)
    return Move(CONTRIBUTION, taxable=o, registered=n, plan=n.plan,
                quantity=-o.qty, date=o.date)


@dataclass
class Pairing:
    """`pair_all`'s result: the in-kind moves (booked or not), and the
    legs left in no pair of any kind (a leg a line declares — in kind or
    `plan=own` — is never left: the line answers it)."""
    moves: List[Move]
    unpaired: List[Leg]


def pair_all(all_legs: Sequence[Leg], days: int = PAIR_DAYS, *,
             own: Iterable[int] = (),
             declared: Optional[Dict[int, str]] = None,
             slow_days: int = SLOW_DAYS) -> Pairing:
    """Pair every out leg with an in leg of the same security and
    quantity within `days` days, each leg once, the closest date first:

    1. legs of ONE account (a journal between two listings ticker.map
       joins, a broker's cancel and rebook), none a line declares in
       kind (backlog TJ-0829);
    2. legs of the same kind in two accounts — taxable <-> taxable or
       registered <-> registered: a move of your own, never in kind;
    3. a taxable leg with a registered leg: an in-kind move. Legs a
       line declares in kind (`declared`: id(row) -> the line's plan,
       "" = any) go first and skip step 2; `plan=` picks the plan's leg
       (an account whose plan is not named fits any), and with no
       partner of the quantity the plan's legs that add up to it, when
       exactly one set does, are its parts. Any other
       cross-kind pair is booked only when neither leg has another
       plausible partner (a leg of either kind outside a journal, not a
       declared move's): else its problem is "ambiguous".
    4. a taxable leg and a registered leg further apart: the in leg
       `days` + 1 to `slow_days` days after the out leg, neither with
       another candidate so far apart (a leg of either kind, any
       account, left by the steps above): a slower move, its `slow` the
       days between, its problem "slow" — named, NOT booked, until an
       INKIND line declares it (a declared leg with no partner in the
       window, nor parts of it, takes the closest such leg in step 3,
       booked; backlog TJ-0437).

    `own`: id(row) of the transfer rows a line declares moves of your
    own (`plan=own`) — never in kind, and never another leg's
    alternative."""
    own = set(own)
    declared = dict(declared or {})
    outs = [g for g in all_legs if g.qty < 0]
    ins = [g for g in all_legs if g.qty > 0]
    used_o: set = set()
    used_i: set = set()

    def greedy(ok) -> List[Tuple[int, int]]:
        cands = sorted((_rank(o, n), i, j)
                       for i, o in enumerate(outs) if i not in used_o
                       for j, n in enumerate(ins) if j not in used_i
                       and _fits(o, n, days) and ok(o, n))
        got = []
        for _r, i, j in cands:
            if i in used_o or j in used_i:
                continue
            used_o.add(i)
            used_i.add(j)
            got.append((i, j))
        return got

    # 1. One account's own legs — never a leg a line declares in kind:
    # the line says where it went (backlog TJ-0829: an unrelated
    # transfer-in of the account a day later took the declared
    # contribution's leg as a journal, and the line then booked a move
    # to a plan outside the project, the real plan's receipt left over).
    journal = greedy(lambda o, n: (o.account == n.account
                                   and id(o.row) not in declared
                                   and id(n.row) not in declared))
    j_o = {i for i, _ in journal}
    j_i = {j for _, j in journal}
    # 2. The same kind in two accounts (a leg a line declares in kind
    # waits for step 3).
    greedy(lambda o, n: (o.account != n.account
                         and o.registered == n.registered
                         and id(o.row) not in declared
                         and id(n.row) not in declared))

    def cross(o: Leg, n: Leg) -> bool:
        return o.account != n.account and o.registered != n.registered

    moves: List[Move] = []
    settled_o: set = set()          # legs of declared moves
    settled_i: set = set()
    # 3a. Declared legs.
    for side, legs_, other, used, oused in (
            ("out", outs, ins, used_o, used_i),
            ("in", ins, outs, used_i, used_o)):
        for i in sorted((i for i, g in enumerate(legs_)
                         if id(g.row) in declared and i not in used),
                        key=lambda i: (legs_[i].date, legs_[i].account)):
            d = legs_[i]
            plan = declared[id(d.row)]
            mates = [j for j, g in enumerate(other) if j not in oused
                     and id(g.row) not in own and cross(d, g)
                     and (not plan or g.plan in (plan, "sheltered"))]
            fit = sorted(
                (j for j in mates
                 if (_fits(d, other[j], days) if side == "out"
                     else _fits(other[j], d, days))),
                key=lambda j: (_rank(d, other[j]) if side == "out"
                               else _rank(other[j], d)))
            if fit:
                j = fit[0]
                used.add(i)
                oused.add(j)
                o, n = (d, other[j]) if side == "out" else (other[j], d)
                moves.append(_make(o, n))
            else:
                js = _parts(d, [(j, other[j]) for j in mates], days)
                if not js:
                    # A slower move (step 4's window): the closest leg
                    # of the plan posted after the out leg, declared by
                    # the line (backlog TJ-0437: the line booked the
                    # sale "to a plan outside the project").
                    far = sorted(
                        ((_slow_gap(d, other[j], days, slow_days)
                          if side == "out"
                          else _slow_gap(other[j], d, days, slow_days)), j)
                        for j in mates)
                    far = [(g, j) for g, j in far if g]
                    if not far:
                        continue        # apply_lines: a plan outside
                    gap, j = far[0]
                    used.add(i)
                    oused.add(j)
                    o, n = (d, other[j]) if side == "out" else (other[j], d)
                    m = _make(o, n)
                    m.slow = gap
                    moves.append(m)
                    continue
                used.add(i)
                oused.update(js)
                parts = sorted((other[j] for j in js),
                               key=lambda g: (g.date, g.account))
                m = _make(d, parts[0]) if side == "out" else \
                    _make(parts[0], d)
                m.quantity = abs(d.qty)
                m.parts = parts
                moves.append(m)
    for m in moves:
        for g in m.registered_legs + [m.taxable]:
            if g.qty < 0:
                settled_o.update(i for i, x in enumerate(outs) if x is g)
            else:
                settled_i.update(j for j, x in enumerate(ins) if x is g)
    # 3b. The rest: booked when unambiguous. A line's leg is answered
    # (own, or declared: paired above or a plan outside the project).
    answered = own | set(declared)
    blocked_o = j_o | settled_o | {i for i, g in enumerate(outs)
                                   if id(g.row) in answered}
    blocked_i = j_i | settled_i | {j for j, g in enumerate(ins)
                                   if id(g.row) in answered}
    for i, j in greedy(lambda o, n: (cross(o, n)
                                     and id(o.row) not in answered
                                     and id(n.row) not in answered)):
        o, n = outs[i], ins[j]
        m = _make(o, n)
        # A leg identical to the partner (same account, date and
        # quantity: two equal moves on one day) changes nothing.
        m.alternatives = sorted(
            [g for jj, g in enumerate(ins)
             if jj != j and jj not in blocked_i and g.ident() != n.ident()
             and g.account != o.account and _fits(o, g, days)]
            + [g for ii, g in enumerate(outs)
               if ii != i and ii not in blocked_o and g.ident() != o.ident()
               and g.account != n.account and _fits(g, n, days)],
            key=lambda g: (g.date, g.account, g.qty))
        if m.alternatives:
            m.problem = "ambiguous"
        moves.append(m)
    # 4. A slower move between a taxable and a registered account (in
    # leg after the out leg, more than `days` and at most `slow_days`
    # apart), neither leg with another candidate so far apart among the
    # legs left (either kind, any account; a journal's and a line's
    # legs are no candidates): named, never booked unasked.
    lo = [i for i, g in enumerate(outs) if i not in used_o
          and i not in j_o and id(g.row) not in answered]
    li = [j for j, g in enumerate(ins) if j not in used_i
          and j not in j_i and id(g.row) not in answered]
    by_o: Dict[int, List[Tuple[int, int]]] = {}
    by_i: Dict[int, List[int]] = {}
    for i in lo:
        for j in li:
            gap = _slow_gap(outs[i], ins[j], days, slow_days)
            if gap:
                by_o.setdefault(i, []).append((j, gap))
                by_i.setdefault(j, []).append(i)
    for i, cands in sorted(by_o.items()):
        if len(cands) != 1:
            continue
        j, gap = cands[0]
        o, n = outs[i], ins[j]
        if len(by_i.get(j, ())) != 1 or not cross(o, n):
            continue
        used_o.add(i)
        used_i.add(j)
        m = _make(o, n)
        m.slow, m.problem = gap, "slow"
        moves.append(m)
    moves.sort(key=lambda m: (m.date, m.taxable.account, m.key))
    unpaired = ([g for i, g in enumerate(outs) if i not in used_o]
                + [g for j, g in enumerate(ins) if j not in used_i])
    unpaired = [g for g in unpaired
                if id(g.row) not in declared and id(g.row) not in own]
    return Pairing(moves, unpaired)


def _parts(d: Leg, cands: Sequence[Tuple[int, Leg]], days: int
           ) -> List[int]:
    """The indexes of the plan's legs (one plan) that add up to the
    declared leg `d`'s quantity — opposite direction, each smaller,
    within `days` — when exactly one set does; else [].

    EVERY candidate leg counts towards uniqueness: the search used to
    look at the eight nearest legs only, so a ninth leg that made a
    second set was never seen and the first set was booked as the only
    one (audit 2026-10-24 B43). The search is bounded by `_PARTS_STEPS`
    steps instead; when they run out before a second set is ruled out,
    nothing is proven unique and [] is returned (the line then books
    the taxable side alone and the plan's legs stay listed)."""
    want = abs(d.qty)
    pool = sorted(((j, g) for j, g in cands
                   if g.key == d.key and (g.qty > 0) != (d.qty > 0)
                   and abs(g.qty) < want - _EPS
                   and _gap(g.date, d.date) <= days),
                  key=lambda e: (_gap(e[1].date, d.date), e[1].date))
    found: List[List[int]] = []
    steps = _PARTS_STEPS
    for plan in sorted({g.plan for _j, g in pool}):
        # Largest legs first: a sum that overshoots is pruned sooner.
        legs_ = sorted(((j, g) for j, g in pool if g.plan == plan),
                       key=lambda e: -abs(e[1].qty))
        qs = [abs(g.qty) for _j, g in legs_]
        rest = [0.0] * (len(qs) + 1)        # what the legs from i on add
        for i in range(len(qs) - 1, -1, -1):
            rest[i] = rest[i + 1] + qs[i]
        # Depth-first over "take leg i / skip leg i", iteratively.
        stack: List[Tuple[int, float, Tuple[int, ...]]] = [(0, 0.0, ())]
        while stack and len(found) < 2:
            if steps <= 0:
                return []           # unproven: the budget ran out
            steps -= 1
            i, total, chosen = stack.pop()
            if abs(total - want) <= _EPS:
                if len(chosen) >= 2:
                    found.append(sorted(legs_[k][0] for k in chosen))
                continue
            if (i >= len(qs) or total > want + _EPS
                    or total + rest[i] < want - _EPS):
                continue
            stack.append((i + 1, total, chosen))
            stack.append((i + 1, total + qs[i], chosen + (i,)))
        if len(found) > 1:
            break
    return found[0] if len(found) == 1 else []


def pair(all_legs: Sequence[Leg], days: int = PAIR_DAYS, *,
         own: Iterable[int] = (),
         declared: Optional[Dict[int, str]] = None) -> List[Move]:
    """The in-kind moves among `all_legs` (`pair_all`): a pair within
    one account or between two taxable or two registered accounts is a
    move of your own (not returned)."""
    return pair_all(all_legs, days, own=own, declared=declared).moves


def in_parts(pairing: Pairing, days: int = PAIR_DAYS
             ) -> List[Tuple[Leg, List[Leg]]]:
    """(taxable leg, the plan legs) per unpaired taxable transfer row
    with unpaired legs of a registered account of the same security, the
    other direction and another quantity within `days` days: possibly a
    move in kind delivered in parts (warned about, never booked
    unasked)."""
    out = []
    un = pairing.unpaired
    for g in sorted((g for g in un if not g.registered),
                    key=lambda g: (g.date, g.account, g.key)):
        hs = [h for h in un if h.registered and h.key == g.key
              and h.account != g.account and (h.qty > 0) != (g.qty > 0)
              and _gap(g.date, h.date) <= days]
        if hs:
            out.append((g, sorted(hs, key=lambda h: (h.account, h.date))))
    return out


def _line_leg(acct: str, ln: Dict[str, Any], all_legs: Sequence[Leg],
              keyf: Callable[[str], str], days: int, taken: set
              ) -> Optional[Leg]:
    """The transfer row of taxable account `acct` an INKIND line names:
    the same security and signed quantity, the nearest date within
    `days`, not taken by an earlier line."""
    q = float(ln["quantity"])
    k = keyf(ln["symbol"])
    c = sorted((g for g in all_legs
                if g.account == acct and not g.registered and g.key == k
                and id(g.row) not in taken and abs(g.qty - q) <= _EPS
                and _gap(g.date, ln["date"]) <= days),
               key=lambda g: (_gap(g.date, ln["date"]), g.date))
    return c[0] if c else None


def line_legs(lines: Iterable[Tuple[str, Dict[str, Any]]],
              all_legs: Sequence[Leg], *,
              key: Optional[Callable[[str], str]] = None,
              days: int = PAIR_DAYS) -> Tuple[set, Dict[int, str]]:
    """(own, declared) for `pair_all` from (taxable account, INKIND
    line) pairs: the rows `plan=own` lines name, and id(row) -> plan of
    the rows the other lines declare in kind."""
    keyf = key or (lambda s: str(s or "").upper())
    own: set = set()
    declared: Dict[int, str] = {}
    taken: set = set()
    for acct, ln in lines:
        g = _line_leg(acct, ln, all_legs, keyf, days, taken)
        if g is None:
            continue
        taken.add(id(g.row))
        if ln.get("plan") == OWN:
            own.add(id(g.row))
        else:
            declared[id(g.row)] = str(ln.get("plan") or "")
    return own, declared


def apply_lines(moves: List[Move],
                lines: Iterable[Tuple[str, Dict[str, Any]]],
                all_legs: Sequence[Leg], *,
                key: Optional[Callable[[str], str]] = None,
                days: int = PAIR_DAYS,
                refused: Optional[List[str]] = None) -> List[str]:
    """Apply each (taxable account, INKIND line): it names the transfer
    row of that account, security and signed quantity nearest its date
    (within `days`; `line_legs`). It values and dates the move of that
    row; with none, it declares a move for the row whose plan is outside
    the project (added to `moves`). A `plan=own` line values nothing
    (`pair_all` kept its row out). Returns a problem text per line that
    matches no transfer row. `refused` receives the text of each option
    line whose price and total disagree at the contract's size: the run
    stops on those (backlog TJ-1163), as it does on a share's."""
    keyf = key or (lambda s: str(s or "").upper())
    problems: List[str] = []
    taken: set = set()
    for acct, ln in lines:
        q = float(ln["quantity"])
        g = _line_leg(acct, ln, all_legs, keyf, days, taken)
        if g is None:
            problems.append(
                f"the INKIND line {ln.get('source') or ''} matches no "
                f"transfer row of {acct} ({abs(q):g} {ln['symbol']} "
                f"{'out' if q < 0 else 'in'} within {days} days of "
                f"{ln['date']}): ignored")
            bad = line_total_problem(ln)
            if bad and refused is not None:
                refused.append(bad)
            continue
        taken.add(id(g.row))
        if ln.get("plan") == OWN:
            continue
        kind = CONTRIBUTION if q < 0 else WITHDRAWAL
        m = next((m for m in moves if m.taxable.row is g.row), None)
        fmv = _line_value(ln, g, m.registered if m is not None else None)
        if isinstance(fmv, str):
            problems.append(f"{fmv}: ignored")
            if refused is not None:
                refused.append(fmv)
            continue
        if m is None:
            m = Move(kind, taxable=g, registered=None,
                     plan=ln.get("plan") or "sheltered",
                     quantity=abs(q), date=g.date)
            moves.append(m)
        if ln.get("plan") and m.plan == "sheltered":
            m.plan = ln["plan"]         # the line names the unnamed plan
        m.line = ln
        m.date = ln["date"]
        m.fmv = fmv
        m.currency = ln["currency"]
        m.source = "tt"
        m.source_text = f"INKIND line {ln.get('source') or ''}".strip()
        m.estimated = False
        if m.problem in ("ambiguous", "slow"):
            m.problem, m.alternatives = "", []
    moves.sort(key=lambda m: (m.date, m.taxable.account, m.key))
    return problems


def _line_value(ln: Dict[str, Any], leg: Leg, other: Optional[Leg]):
    """The value an INKIND line gives the move of `leg` (float), or the
    problem text when its price and total disagree. An option's price is
    per share of the contract: valued at the rows' own contract size (a
    ticker.map MULT line's 10), not the parser's standard 100 (backlog
    TJ-0933)."""
    total = float(ln["total"])
    try:
        px = float(ln.get("price") or 0.0)
    except (TypeError, ValueError):
        px = 0.0
    from taxjson.lib.core import is_option_symbol
    if px <= 0 or not is_option_symbol(str(ln.get("symbol") or "")):
        return total
    size = contract_size(leg, other)
    by_price = round(abs(float(ln["quantity"])) * px * size, 2)
    if not ln.get("total_stated"):
        return by_price
    return line_total_problem(ln, size) or total


def line_total_problem(ln: Dict[str, Any], *sizes: float) -> str:
    """Why an option's INKIND line is refused: it states a price and a
    total that agree at none of `sizes` (default: the size the symbol's
    own rule gives, a ticker.map MULT line, else the standard 100); ""
    for any other line (backlog TJ-1163)."""
    from taxjson.lib.core import is_option_symbol
    sym = str(ln.get("symbol") or "")
    try:
        px = float(ln.get("price") or 0.0)
    except (TypeError, ValueError):
        px = 0.0
    if not ln.get("total_stated") or px <= 0 or not is_option_symbol(sym):
        return ""
    total = float(ln["total"])
    qty = abs(float(ln["quantity"]))
    size = 0.0
    for size in sizes or (contract_size(Leg("", "", {"symbol": sym}, "")),):
        by_price = round(qty * px * size, 2)
        if abs(total - by_price) <= max(0.05, 0.01 * max(by_price, 1.0)):
            return ""
    return (f"the INKIND line {ln.get('source') or ''}: total {total:.2f} "
            f"differs from qty x price x {size:g} = {by_price:.2f} — give "
            f"one of them (price 0 and the total, or the price alone)")


def contract_size(leg: Leg, *others: Optional[Leg]) -> float:
    """Units one contract of the leg's option covers — what a quoted
    price is multiplied by to value a contract, and what its booked
    price is per: the size a transfer row declares (`multiplier`: an
    adjusted series' 50, a mini's 10; IB states it — `leg`'s row, else
    an `others`' row, the move's other leg), else lib/core's rule for
    the symbol (a ticker.map `MULT` line, else the standard 100,
    assumed and said once per root); 1 for a share. Every option used
    to be valued and priced at 100 (audit 2026-10-24 B20)."""
    from types import SimpleNamespace

    from taxjson.lib.core import is_option_symbol, option_contract_size
    sym = leg.symbol
    if not is_option_symbol(sym):
        return 1.0
    m, basis = 0.0, ""
    for g in (leg,) + others:
        if g is None:
            continue
        try:
            gm = float(g.row.get("multiplier") or 0.0)
        except (TypeError, ValueError):
            gm = 0.0
        gb = str(g.row.get("contract_size_basis") or "")
        if gm > 0 and gb != "assumed":
            return gm
        if gm > 0 and not m:
            m, basis = gm, gb
    return float(option_contract_size(SimpleNamespace(
        symbol=sym, multiplier=m, contract_size_basis=basis)) or 1.0)


def _market_value(leg: Optional[Leg], qty: float) -> Optional[float]:
    if leg is None:
        return None
    try:
        mv = float(leg.row.get("market_value") or 0.0)
    except (TypeError, ValueError):
        return None
    if mv <= 0 or abs(leg.qty) < _EPS:
        return None
    return round(mv * qty / abs(leg.qty), 2)


def decide(moves: Iterable[Move], country: str) -> None:
    """Set the problem of each move the country does not book: a US
    contribution to a retirement account (cash only, US-INKIND-01)."""
    from taxjson.lib.country import in_kind_contribution_booked
    for m in moves:
        if (m.kind == CONTRIBUTION and not m.problem
                and not in_kind_contribution_booked(country)):
            m.problem = "cash_only"


class CloseUnavailable(Exception):
    """The close lookup could not run (TAXJSON_OFFLINE with no cached
    close): its message says why."""


def value(moves: Iterable[Move],
          close: Optional[Callable[[Move], Any]] = None, *,
          broker_names: Optional[Dict[str, str]] = None) -> None:
    """Give each move with no value yet its fair market value: the
    broker's stated market value on its transfer row (the taxable leg's,
    else the registered leg's), else `close(move)` — a DayClose-like
    object (price, currency, day) or None; it may raise CloseUnavailable
    (the move's problem is then "offline"). A move left with no value
    has the problem "no_value"."""
    names = broker_names or {}
    for m in moves:
        if m.problem or m.fmv is not None:
            continue
        for leg in (m.taxable, m.registered):
            mv = _market_value(leg, m.quantity)
            if mv:
                m.fmv, m.currency, m.source = mv, leg.currency, "broker"
                b = names.get(leg.broker, leg.broker) or "the broker"
                m.source_text = (f"{b} market value on {leg.account}'s "
                                 f"transfer row")
                break
        if m.fmv is not None:
            continue
        if close is None:
            m.problem = "no_value"
            continue
        try:
            c = close(m)
        except CloseUnavailable as e:
            m.problem, m.source_text = "offline", str(e)
            continue
        if c is None or not getattr(c, "currency", None):
            m.problem = "no_value"
            continue
        # The rows' own contract size (B20), not 100 for every option.
        size = contract_size(m.taxable, m.registered)
        m.fmv = round(float(c.price) * m.quantity * size, 2)
        m.currency = str(c.currency).upper()
        m.source, m.estimated = "yahoo", True
        m.source_text = (f"Yahoo close {c.day}, ESTIMATED: split-adjusted"
                         + (", from the close cache"
                            if getattr(c, "source", "") == "cache"
                            else ""))


def row_type(m: Move, country: str) -> str:
    """The `type` of the taxable account's booked row."""
    from taxjson.lib.country import is_canada
    if m.kind == WITHDRAWAL:
        return IN_KIND_WITHDRAWAL_TYPE
    if is_canada(country) and m.plan not in CA_LOSS_DENIED_PLANS:
        return IN_KIND_DISPOSITION_TYPE
    return IN_KIND_CONTRIBUTION_TYPE


def booked_rows(moves: Iterable[Move], country: str
                ) -> Dict[str, List[Dict[str, Any]]]:
    """{taxable account: [rows]}: each booked move as one BUYSELL of the
    taxable account on its date at its fair market value — a sale for a
    contribution, a purchase for a withdrawal (lib/core IN_KIND_*)."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    seen: Dict[str, int] = {}
    for m in moves:
        if not m.booked:
            continue
        t = m.taxable.row
        q = -m.quantity if m.kind == CONTRIBUTION else m.quantity
        # The price is per unit of the rows' own contract size (B20);
        # the row below carries that `multiplier`, so the engines'
        # qty x price x multiplier comes back to `net_amount`.
        size = contract_size(m.taxable, m.registered)
        what = ("IN-KIND CONTRIBUTION TO" if m.kind == CONTRIBUTION
                else "IN-KIND WITHDRAWAL FROM")
        row = {
            "action": "BUYSELL",
            "type": row_type(m, country),
            "date": m.date,
            "time": str(t.get("time") or "09:30:00"),
            "date_settle": m.date,
            "symbol": m.symbol,
            "quantity": q,
            "currency": m.currency,
            "price": round(m.fmv / m.quantity / size, 8),
            "net_amount": m.fmv,
            "gross_amount": m.fmv,
            "fee": 0.0,
            "account": m.taxable.account,
            # Never the broker's own text (it can name the other
            # account's number).
            "description": _distinct(
                f"{what} {m.plan.upper()} AT FAIR MARKET VALUE "
                f"({m.source_text})", (m.taxable.account, m.date, q),
                seen),
        }
        if t.get("source"):
            row["source"] = t["source"]
        for k in ("multiplier", "contract_size_basis", "source_account"):
            if t.get(k):
                row[k] = t[k]
        if size != 1.0 and size != (float(row.get("multiplier") or 0.0)
                                    or 100.0):
            # The size the price is per, when the engines would read
            # another one off the row (the plan's leg's or a MULT line's
            # size, the taxable row declaring none or an assumed one —
            # whose basis then no longer describes the size).
            row.pop("contract_size_basis", None)
            row["multiplier"] = size
        out.setdefault(m.taxable.account, []).append(row)
    return out


def _distinct(text: str, ident: Tuple[Any, ...], seen: Dict[str, int]
              ) -> str:
    """`text`, numbered from the second identical move on (" [2]"): two
    identical moves on one day are two rows, and the books' merge drops
    a row identical to another (core.TaxTransaction.compute_id)."""
    k = repr((text,) + tuple(ident))
    seen[k] = seen.get(k, 0) + 1
    return text if seen[k] == 1 else f"{text} [{seen[k]}]"


def taxable_leg_ids(moves: Iterable[Move]) -> set:
    """The identities of the taxable transfer rows a booked move
    accounts for: lib/transfer_in leaves them out (a withdrawal's in leg
    is no longer an arrival with no cost; a contribution's out leg
    cancels nothing)."""
    return {m.taxable.ident() for m in moves if m.booked}


def mark_sheltered(rows: List[Dict[str, Any]], moves: Iterable[Move], *,
                   key: Optional[Callable[[str], str]] = None,
                   currency: str = "") -> int:
    """The loss-rule context (the registered accounts' combined book):
    each booked contribution's plan acquisition becomes a PURCHASE
    (BUYSELL of IN_KIND_CONTRIBUTION_TYPE) — the plan's TRANSFER row
    when the book holds it (each part's, a delivery in parts), else a
    row added for it (a plan whose transfers are kept out, or one
    outside the project). A withdrawal's
    plan leg missing from the book is added as the plan's disposal, so
    the plan does not look like it still holds the shares; so is a
    withdrawal from a plan outside the project, up to the units its
    earlier contributions added (backlog TJ-0828). Returns the
    number of rows changed or added. `currency`: the label of an added
    row (the book's base currency; an added row carries no money)."""
    keyf = key or (lambda s: str(s or "").upper())
    n = 0
    taken = set()
    seen: Dict[str, int] = {}
    for m in moves:
        if not m.booked:
            continue
        for reg in (m.registered_legs or [None]):
            hit = None
            if reg is not None:
                for i, t in enumerate(rows):
                    if (i in taken or t.get("action") != "TRANSFER"
                            or str(t.get("account") or "") != reg.account
                            or keyf(str(t.get("symbol") or "")) != m.key
                            or str(t.get("date") or "")[:10] != reg.date):
                        continue
                    try:
                        tq = float(t.get("quantity") or 0.0)
                    except (TypeError, ValueError):
                        continue
                    if abs(tq - reg.qty) <= _EPS:
                        hit = i
                        break
            qty = abs(reg.qty) if reg is not None else m.quantity
            if m.kind == CONTRIBUTION:
                if hit is not None:
                    taken.add(hit)
                    rows[hit] = dict(rows[hit], action="BUYSELL",
                                     type=IN_KIND_CONTRIBUTION_TYPE,
                                     **_declared_day(m, rows[hit]))
                else:
                    rows.append(_plan_row(m, reg, +qty,
                                          IN_KIND_CONTRIBUTION_TYPE,
                                          currency, seen))
                n += 1
            elif hit is None and reg is not None:
                rows.append(_plan_row(m, reg, -qty, IN_KIND_WITHDRAWAL_TYPE,
                                      currency, seen))
                n += 1
            elif hit is not None:
                taken.add(hit)
    # A withdrawal declared from a plan outside the project takes the
    # shares out of that plan's side, as far as an earlier contribution
    # put them there: the plan no longer holds a replacement (backlog
    # TJ-0828: the loss was denied for good although the plan gave the
    # shares back before day 30). Never more than it holds: a plan
    # outside the project that never received them is not made short.
    for m in sorted((m for m in moves if m.booked and m.kind == WITHDRAWAL
                     and not m.registered_legs),
                    key=lambda m: (m.date, m.key)):
        acct = f"{m.plan or 'registered'}-outside"
        held = 0.0
        for t in rows:
            if (str(t.get("account") or "") != acct
                    or keyf(str(t.get("symbol") or "")) != m.key
                    or str(t.get("date") or "")[:10] > m.date):
                continue
            try:
                held += float(t.get("quantity") or 0.0)
            except (TypeError, ValueError):
                continue
        qty = min(m.quantity, held)
        if qty > _EPS:
            rows.append(_plan_row(m, None, -qty, IN_KIND_WITHDRAWAL_TYPE,
                                  currency, seen))
            n += 1
    return n


def _declared_day(m: Move, row: Dict[str, Any]) -> Dict[str, Any]:
    """The date fields of a contribution's plan row when an INKIND line
    declares the move: the line's date, as the taxable side's sale has
    it. The plan's posting a few days later used to stay its acquisition
    date for the loss rule, so a declared contribution inside a loss's
    30 days counted outside them (backlog TJ-0925). {} when no line."""
    if m.line is None or m.kind != CONTRIBUTION or not m.date:
        return {}
    d = str(row.get("date") or "")
    out: Dict[str, Any] = {"date": m.date + d[10:]}
    if row.get("date_settle") is not None:
        out["date_settle"] = m.date
    return out


def _plan_row(m: Move, reg: Optional[Leg], qty: float, typ: str,
              currency: str, seen: Dict[str, int]) -> Dict[str, Any]:
    acct = reg.account if reg else f"{m.plan or 'registered'}-outside"
    # A declared contribution's plan side is on the line's date
    # (_declared_day, backlog TJ-0925).
    day = (m.date if reg is None or (m.line is not None
                                     and typ == IN_KIND_CONTRIBUTION_TYPE)
           else reg.date)
    return {"action": "BUYSELL", "type": typ, "date": day,
            "date_settle": day, "time": "09:30:00", "symbol": m.key,
            "quantity": float(qty), "currency": currency or m.currency,
            "price": 0.0, "net_amount": 0.0, "fee": 0.0,
            "account": acct,
            "description": _distinct(
                f"in-kind {m.kind} {m.pair_text()} (the plan's side, for "
                f"the loss rule)", (acct, day, float(qty)), seen)}


def withdrawal_income(m: Move, country: str) -> str:
    """What a withdrawal's value is on a slip ("" when nothing)."""
    from taxjson.lib.country import is_canada
    if m.kind != WITHDRAWAL:
        return ""
    if is_canada(country):
        return CA_WITHDRAWAL_INCOME.get(
            m.plan, "that value may be income on a slip from the plan")
    return US_WITHDRAWAL_INCOME.get(m.plan, US_WITHDRAWAL_DEFAULT)


def _money(x: float) -> str:
    return f"{x:,.2f}"


def message(moves: Sequence[Move], problems: Sequence[str],
            results: Dict[Tuple[str, str, str], Dict[str, float]],
            country: str, base: str, inputs: str = "inputs"
            ) -> Optional[Tuple[str, List[str]]]:
    """(headline, details) of the run's ONE in-kind warning, or None.
    `results`: (taxable account, security key, date) -> {"gain",
    "denied"} of a contribution's sale in the books (base currency).
    `inputs`: the exports folder as the project names it (`../inputs`
    when every year shares one), where the INKIND line goes (backlog
    TJ-0827)."""
    from taxjson.lib.country import COST_TERM, is_canada
    cost = COST_TERM.get(country, "cost")
    if not moves and not problems:
        return None
    booked = [m for m in moves if m.booked]
    head = (f"{len(booked)} in-kind move(s) between your taxable and "
            f"registered accounts booked at fair market value"
            if booked else "in-kind move(s) between your taxable and "
            "registered accounts NOT booked")
    if booked and len(booked) < len(moves):
        head += f"; {len(moves) - len(booked)} NOT booked"
    details: List[str] = []
    for m in moves:
        what = (f"{m.kind} {m.pair_text()}: {m.quantity:g} {m.symbol} on "
                f"{m.date}")
        if m.slow and m.registered is not None:
            o, n = ((m.taxable, m.registered) if m.kind == CONTRIBUTION
                    else (m.registered, m.taxable))
            what += (f" (out {o.date}, in {n.date}: {m.slow} days "
                     f"apart)")
        if m.booked:
            val = (f"at {_money(m.fmv)} {m.currency} ({m.source_text})")
            if m.kind == CONTRIBUTION:
                r = results.get((m.taxable.account, m.key, m.date))
                if r is None:
                    tail = "a sale at that value"
                elif r.get("denied", 0.0) > 0.005:
                    tail = (f"loss {_money(r['denied'])} {base} DENIED for "
                            f"good (s.40(2)(g)(iv)) — no {cost} addition")
                elif r.get("gain", 0.0) >= 0:
                    tail = f"gain {_money(r['gain'])} {base}"
                else:
                    tail = f"loss {_money(-r['gain'])} {base}"
                if (is_canada(country) and m.plan == "sheltered"):
                    tail += (" (the plan is not named: taken as an RRSP/"
                             "TFSA-like plan, s.40(2)(g)(iv) — set "
                             "`plan` on the account)")
            else:
                inc = withdrawal_income(m, country)
                tail = (f"the shares' {cost}"
                        + (f"; {inc} (not booked)" if inc
                           else "; no tax on the withdrawal"))
            details.append(f"- {what} {val}: {tail}.")
        elif m.problem == "ambiguous":
            alts = ", ".join(
                f"{a.account}'s transfer-{'in' if a.qty > 0 else 'out'} "
                f"of {abs(a.qty):g} on {a.date}" for a in m.alternatives)
            q = -m.quantity if m.kind == CONTRIBUTION else m.quantity
            ln = f"INKIND {m.taxable.date} {m.symbol} {q:g}"
            details.append(
                f"- {what}: NOT booked — ambiguous: {alts} could be the "
                f"other side too, so it is not clear which move was in "
                f"kind. Say which in a .tt file in {inputs}/"
                f"{m.taxable.account}/ (or the folder of the taxable "
                f"account whose move it was): `{ln} "
                f"{m.taxable.currency or 'CAD'} <price per share> "
                f"plan={m.plan}` books this move; `{ln} plan=own` keeps "
                f"it a move of your own.")
        elif m.problem == "slow":
            q = -m.quantity if m.kind == CONTRIBUTION else m.quantity
            ln = f"INKIND {m.taxable.date} {m.symbol} {q:g}"
            details.append(
                f"- {what}: NOT booked — the two legs post more than "
                f"{PAIR_DAYS} days apart and nothing else pairs with "
                f"either, so it is not taken as one move on its own. The "
                f"shares stay in {m.taxable.account}'s books as the "
                f"transfer rows left them. If it was one move, add to a "
                f".tt file in {inputs}/{m.taxable.account}/: `{ln} "
                f"{m.taxable.currency or 'CAD'} <price per share>` (it "
                f"books the move at that value, with {m.other_account}'s "
                f"transfer row); if it was not, `{ln} plan=own`.")
        elif m.problem == "cash_only":
            details.append(
                f"- {what}: NOT booked — an IRA, Roth, 401(k) or HSA "
                f"takes contributions in cash only, so a transfer of "
                f"shares into one is likely an error (a rollover between "
                f"retirement accounts is not a move from a taxable "
                f"account). The shares stay in {m.taxable.account}'s "
                f"books; check the transfer rows (`tjs transfers`).")
        else:
            why = ("TAXJSON_OFFLINE is set and the close cache has no "
                   "price for the date" if m.problem == "offline" else
                   "no market value on the transfer rows and no close "
                   "found")
            q = -m.quantity if m.kind == CONTRIBUTION else m.quantity
            details.append(
                f"- {what}: NOT booked — {why}. Add the fair market value "
                f"as a line in a .tt file in {inputs}/{m.taxable.account}/: "
                f"INKIND {m.date} {m.symbol} {q:g} "
                f"{m.taxable.currency or 'CAD'} <price per share>")
    for p in problems:
        details.append(f"- {p}.")
    if booked:
        if is_canada(country):
            details.append(
                "A contribution in kind is a sale at fair market value: a "
                "gain is taxed, a loss is denied for good (s.40(2)(g)(iv); "
                "not a superficial loss, never added to an ACB); the "
                "plan's purchase counts for the superficial-loss rule. A "
                "withdrawal in kind is a purchase at fair market value.")
        else:
            details.append(
                "A distribution in kind is a purchase at fair market value "
                "on the distribution date (its basis; the holding period "
                "starts then).")
        details.append(
            "A .tt INKIND line in the taxable account's folder replaces "
            "the value: INKIND <date> <symbol> <qty> <currency> <price> "
            "(docs/getting-started.md, step 5c).")
    return head, details


def parts_message(found: Sequence[Tuple[Leg, List[Leg]]],
                  inputs: str = "inputs"
                  ) -> Optional[Tuple[str, List[str]]]:
    """(headline, details) of the run's ONE warning about transfer rows
    that are possibly an in-kind move delivered in parts (`in_parts`),
    or None. `inputs`: the exports folder as `message` takes it."""
    if not found:
        return None
    details: List[str] = []
    for g, hs in found:
        out = g.qty < 0
        by: Dict[str, List[str]] = {}
        for h in hs:
            by.setdefault(h.account, []).append(
                f"{abs(h.qty):g} on {h.date}")
        verb = "received" if out else "sent"
        plans = "; ".join(f"{a} {verb} {', '.join(v)}"
                          for a, v in by.items())
        ln = f"INKIND {g.date} {g.symbol} {g.qty:g}"
        details.append(
            f"- {g.account}: {abs(g.qty):g} {g.symbol} "
            f"{'out' if out else 'in'} on {g.date}; {plans} — possibly a "
            f"{'contribution' if out else 'withdrawal'} in kind in "
            f"parts. If the shares {'went into' if out else 'came from'} "
            f"the plan, add to a .tt file in {inputs}/{g.account}/: `{ln} "
            f"{g.currency or 'CAD'} <price per share>` (it books the move "
            f"at that value, with the plan's legs that add up to it); if "
            f"it was a move of your own: `{ln} plan=own`.")
    details.append("Not booked: the shares stay where the transfer rows "
                   "put them (`tjs transfers` lists the rows; "
                   "`run --strict` stops until a line answers each).")
    return (f"{len(found)} transfer row(s) of a taxable account "
            f"possibly in-kind in parts with a registered account — NOT "
            f"booked", details)


def contribution_results(gains_docs: Iterable[Dict[str, Any]], *,
                         key: Optional[Callable[[str], str]] = None
                         ) -> Dict[Tuple[str, str, str], Dict[str, float]]:
    """(account, security key, date) -> {"gain", "denied"} summed over
    the in-kind contribution sales of the given gains documents."""
    keyf = key or (lambda s: str(s or "").upper())
    out: Dict[Tuple[str, str, str], Dict[str, float]] = {}
    for doc in gains_docs:
        rows = doc.get("transactions") if isinstance(doc, dict) else None
        for e in rows or []:
            if not isinstance(e, dict) or e.get("in_kind") != CONTRIBUTION:
                continue
            k = (str(e.get("account") or ""),
                 keyf(str(e.get("symbol") or "")),
                 str(e.get("date") or "")[:10])
            r = out.setdefault(k, {"gain": 0.0, "denied": 0.0})
            r["gain"] += float(e.get("gain") or 0.0)
            r["denied"] += float(e.get("denied_contribution") or 0.0)
    return out
