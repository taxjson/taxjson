"""The model of `tjs stress`: what an index move does to a holding.

Pure functions, no I/O, plain floats (a view's estimates, not money in the
books). The command (cli/stress) reads the holdings, the closes and the
betas; this module moves the prices and prices the options.

  parse_move(text)        a move as typed, `-10` or `-10%`, in % (None: not
                          a number).
  moved(beta, move)       the holding's own move in %, BETA x the index
                          move, held at -100 (no price goes below 0), and
                          whether it was held there.
  log_volatility(closes, per_year)
                          the sample standard deviation (n - 1) of the
                          last `per_year` daily log returns x
                          sqrt(per_year), a fraction a year (0.2 is 20%):
                          YEAR sessions for a listing, COIN_YEAR calendar
                          days for a coin (traded every day); None with
                          fewer than per_year + 1 closes.
  black_scholes(right, spot, strike, years, rate, vol)
                          a European option's value a share, no dividends:
                          call = S N(d1) - K e^(-rT) N(d2), put = K e^(-rT)
                          N(-d2) - S N(-d1), d1 = (ln(S / K) + (r + vol^2 /
                          2) T) / (vol sqrt(T)), d2 = d1 - vol sqrt(T); at
                          or after expiry (T <= 0) the intrinsic value,
                          max(S - K, 0) or max(K - S, 0); with no
                          volatility the discounted forward's intrinsic;
                          with no strike (K <= 0) a call is the share, a
                          put nothing.
  delta(right, ...)       its delta, N(d1) or N(d1) - 1 (at expiry: 1, 0 or
                          -1 by moneyness).

The rate is continuous and a fraction a year; T is calendar days / 365.
"""
from __future__ import annotations

import math
import re
from typing import Optional, Sequence, Tuple

from taxjson.lib.technicals import YEAR

# The index moves shown when none is given, in %.
DEFAULT_MOVES = (-20.0, -10.0, -5.0, 5.0, 10.0)
# Calendar days in the years to expiry.
YEAR_DAYS = 365.0
# The lowest move a price can take: all of it.
FLOOR_PCT = -100.0
# A coin's year of daily closes: it trades every calendar day.
COIN_YEAR = 365

# ASCII digits only (`\d` takes any script's).
_MOVE_RE = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)%?")


def parse_move(text: str) -> Optional[float]:
    """A move as typed (`-10`, `-10%`, `+5`, `2.5%`) in %, or None when
    `text` is not one."""
    t = str(text or "").strip()
    if not _MOVE_RE.fullmatch(t):
        return None
    return float(t.rstrip("%"))


def moved(beta: float, move_pct: float) -> Tuple[float, bool]:
    """(the holding's move in %: `beta` x `move_pct`, held at FLOOR_PCT,
    whether it was held there)."""
    pct = beta * move_pct
    if pct < FLOOR_PCT:
        return FLOOR_PCT, True
    return pct, False


def log_volatility(closes: Sequence[float], per_year: int = YEAR
                   ) -> Optional[float]:
    """The annualised volatility of the last `per_year` daily log
    returns, a fraction (module docstring); None with fewer than per_year
    + 1 closes or a close not above 0."""
    if len(closes) < per_year + 1:
        return None
    win = closes[-(per_year + 1):]
    if any(c <= 0 for c in win):
        return None
    rets = [math.log(b / a) for a, b in zip(win, win[1:])]
    m = sum(rets) / len(rets)
    var = sum((x - m) * (x - m) for x in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(per_year)


def norm_cdf(x: float) -> float:
    """The standard normal distribution function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _d1(spot: float, strike: float, years: float, rate: float,
        vol: float) -> float:
    return ((math.log(spot / strike) + (rate + vol * vol / 2.0) * years)
            / (vol * math.sqrt(years)))


def black_scholes(right: str, spot: float, strike: float, years: float,
                  rate: float, vol: float) -> float:
    """A call's (`right` "C") or a put's ("P") value a share (module
    docstring)."""
    call = right == "C"
    if strike <= 0:
        return max(spot, 0.0) if call else 0.0
    if years <= 0:
        return max(spot - strike, 0.0) if call else max(strike - spot, 0.0)
    disc = strike * math.exp(-rate * years)
    if spot <= 0:
        return 0.0 if call else disc
    if vol <= 0:
        return max(spot - disc, 0.0) if call else max(disc - spot, 0.0)
    d1 = _d1(spot, strike, years, rate, vol)
    d2 = d1 - vol * math.sqrt(years)
    if call:
        return spot * norm_cdf(d1) - disc * norm_cdf(d2)
    return disc * norm_cdf(-d2) - spot * norm_cdf(-d1)


def delta(right: str, spot: float, strike: float, years: float,
          rate: float, vol: float) -> float:
    """The option's delta a share: N(d1) for a call, N(d1) - 1 for a put;
    at expiry, with no volatility or no price, by moneyness (1 or 0 for
    a call in or out of the money, -1 or 0 for a put)."""
    call = right == "C"
    if strike <= 0:
        return 1.0 if call else 0.0
    if years <= 0 or vol <= 0 or spot <= 0:
        fwd = strike * (math.exp(-rate * years) if years > 0 else 1.0)
        if call:
            return 1.0 if spot > fwd else 0.0
        return -1.0 if spot < fwd else 0.0
    n = norm_cdf(_d1(spot, strike, years, rate, vol))
    return n if call else n - 1.0
