"""Technical statistics of a daily price series (`tjs technicals`).

Pure functions, no I/O: a series is a list of `Bar`s, oldest first, one per
completed session, its close adjusted for splits and dividends (the caller's
source says so: lib/price_chain `daily_history`). Market statistics, not
money in the books: plain floats throughout.

Every statistic has a window and needs that much history: with less it is
None (a blank in the report), never a figure from a shorter window
(`MIN_SESSIONS` names each one's need). The windows count sessions, not
calendar days: a year is YEAR sessions, its window the last YEAR + 1 closes
(YEAR daily returns). A statistic the data does not allow is None too (no
close moved: no RSI; a benchmark whose returns do not vary: no beta; no
high and low: no ATR; a session without a volume: no average volume):
`blank_reasons` says why each blank is one.

  sma_distance(closes, n)  (last close / SMA(n) - 1) x 100; SMA(n) the mean
                           of the last n closes, the last one included.
  rsi(closes, n)           Wilder's RSI: the first average gain and loss the
                           mean of the first n changes of the series, then
                           avg = (avg x (n - 1) + this change) / n through
                           the rest; 100 - 100 / (1 + gain / loss). No loss
                           in the window: 100; no gain: 0; no change: None.
  period_return(closes, n) (last close / the close n sessions before - 1)
                           x 100.
  range_distances(closes)  the last close's % from the year window's highest
                           and lowest close (<= 0 and >= 0).
  volatility(closes)       the sample standard deviation (n - 1) of the
                           year's daily simple returns x sqrt(YEAR) x 100.
  max_drawdown(closes)     the year window's deepest fall from a running
                           peak close: min(close / peak so far - 1) x 100.
  beta_alpha(a, b)         over the last YEAR daily returns both series have
                           (paired_returns): beta = cov(a, b) / var(b);
                           alpha = Jensen's with a zero risk-free rate:
                           (mean a - beta x mean b) x YEAR x 100 (% a year).
  atr_pct(bars, n)         Wilder's average true range over n sessions as a
                           % of the last close; true range = max(high - low,
                           |high - previous close|, |low - previous close|).
  avg_volume(bars, n)      the mean volume of the last n sessions.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

YEAR = 252              # sessions in a year (the annualisation factor too)
QUARTER = 63
MONTH = 21
SMA_PERIODS = (20, 50, 200)
RSI_PERIOD = 14
ATR_PERIOD = 14
VOLUME_SESSIONS = 20

# The closes (sessions) each statistic needs, by its key in series_stats.
MIN_SESSIONS: Dict[str, int] = {
    "sma20_pct": 20, "sma50_pct": 50, "sma200_pct": 200,
    "rsi14": RSI_PERIOD + 1,
    "return_1m_pct": MONTH + 1, "return_3m_pct": QUARTER + 1,
    "return_1y_pct": YEAR + 1,
    "from_high_pct": YEAR + 1, "from_low_pct": YEAR + 1,
    "volatility_pct": YEAR + 1, "max_drawdown_pct": YEAR + 1,
    # Paired with the benchmark: YEAR returns on dates both have.
    "beta": YEAR + 1, "alpha_pct": YEAR + 1,
    "atr14_pct": ATR_PERIOD + 1, "avg_volume_20": VOLUME_SESSIONS,
}


@dataclass(frozen=True)
class Bar:
    """One completed session: its date (YYYY-MM-DD), its adjusted close, and
    its adjusted high and low and its volume when the source gives them."""
    day: str
    close: float
    high: Optional[float] = None
    low: Optional[float] = None
    volume: Optional[float] = None


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def pct_from(level: Optional[float], ref: Optional[float]) -> Optional[float]:
    """(level / ref - 1) x 100, or None when either is missing or ref is not
    above 0."""
    if level is None or ref is None or ref <= 0:
        return None
    return (level / ref - 1.0) * 100.0


def sma(closes: Sequence[float], n: int) -> Optional[float]:
    """The mean of the last n closes; None with fewer than n."""
    if n <= 0 or len(closes) < n:
        return None
    return _mean(closes[-n:])


def sma_distance(closes: Sequence[float], n: int) -> Optional[float]:
    """The last close's % above (+) or below (-) its n-session SMA."""
    if not closes:
        return None
    return pct_from(closes[-1], sma(closes, n))


def rsi(closes: Sequence[float], n: int = RSI_PERIOD) -> Optional[float]:
    """Wilder's RSI(n) over the whole series (module docstring); None with
    fewer than n + 1 closes or no change at all."""
    if n <= 0 or len(closes) < n + 1:
        return None
    changes = [b - a for a, b in zip(closes, closes[1:])]
    up = _mean([max(c, 0.0) for c in changes[:n]])
    down = _mean([max(-c, 0.0) for c in changes[:n]])
    for c in changes[n:]:
        up = (up * (n - 1) + max(c, 0.0)) / n
        down = (down * (n - 1) + max(-c, 0.0)) / n
    if down == 0:
        return 100.0 if up > 0 else None
    return 100.0 - 100.0 / (1.0 + up / down)


def daily_returns(closes: Sequence[float]) -> List[float]:
    """Each session's simple return, close / the previous close - 1."""
    return [b / a - 1.0 for a, b in zip(closes, closes[1:])]


def period_return(closes: Sequence[float], n: int) -> Optional[float]:
    """The % change over the last n sessions; None with fewer than n + 1
    closes."""
    if n <= 0 or len(closes) < n + 1:
        return None
    return pct_from(closes[-1], closes[-1 - n])


def _year(closes: Sequence[float]) -> Optional[Sequence[float]]:
    """The year window: the last YEAR + 1 closes, or None with fewer."""
    if len(closes) < YEAR + 1:
        return None
    return closes[-(YEAR + 1):]


def range_distances(closes: Sequence[float]
                    ) -> Tuple[Optional[float], Optional[float]]:
    """(% from the year's highest close, % from its lowest), the last close's;
    (None, None) with less than a year."""
    win = _year(closes)
    if win is None:
        return None, None
    return pct_from(win[-1], max(win)), pct_from(win[-1], min(win))


def _sample_sd(xs: Sequence[float]) -> float:
    m = _mean(xs)
    return math.sqrt(sum((x - m) * (x - m) for x in xs) / (len(xs) - 1))


def volatility(closes: Sequence[float]) -> Optional[float]:
    """The year's daily-return standard deviation, annualised, in %."""
    win = _year(closes)
    if win is None:
        return None
    return _sample_sd(daily_returns(win)) * math.sqrt(YEAR) * 100.0


def max_drawdown(closes: Sequence[float]) -> Optional[float]:
    """The year window's deepest fall from a running peak, in % (<= 0)."""
    win = _year(closes)
    if win is None:
        return None
    peak, worst = win[0], 0.0
    for c in win:
        peak = max(peak, c)
        worst = min(worst, (c / peak - 1.0) * 100.0)
    return worst


def paired_returns(a: Sequence[Bar], b: Sequence[Bar]
                   ) -> List[Tuple[float, float]]:
    """The daily returns of two series on the dates both have: between each
    two consecutive common dates, (a's return, b's return). A date only one
    series has (a holiday on one market) is skipped by both, so the return
    after it spans the gap in both."""
    bc = {x.day: x.close for x in b}
    common = [(x.close, bc[x.day]) for x in a if x.day in bc]
    return [(a1 / a0 - 1.0, b1 / b0 - 1.0)
            for (a0, b0), (a1, b1) in zip(common, common[1:])]


def beta_alpha(a: Sequence[Bar], b: Sequence[Bar]
               ) -> Tuple[Optional[float], Optional[float]]:
    """(beta, alpha in % a year) of series `a` against benchmark `b` over the
    last YEAR paired returns (module docstring); (None, None) with fewer, or
    when the benchmark's returns do not vary."""
    pairs = paired_returns(a, b)
    if len(pairs) < YEAR:
        return None, None
    pairs = pairs[-YEAR:]
    ra = [p[0] for p in pairs]
    rb = [p[1] for p in pairs]
    ma, mb = _mean(ra), _mean(rb)
    cov = sum((x - ma) * (y - mb) for x, y in pairs)
    var = sum((y - mb) * (y - mb) for y in rb)
    if var == 0:
        return None, None
    beta = cov / var
    return beta, (ma - beta * mb) * YEAR * 100.0


def atr_pct(bars: Sequence[Bar], n: int = ATR_PERIOD) -> Optional[float]:
    """Wilder's ATR(n) as a % of the last close, over the trailing run of
    sessions that all have a high and a low; None when that run is shorter
    than n + 1 sessions."""
    run: List[Tuple[float, float, float]] = []      # (high, low, close)
    for x in reversed(bars):
        if x.high is None or x.low is None:
            break
        run.append((x.high, x.low, x.close))
    run.reverse()
    if n <= 0 or len(run) < n + 1:
        return None
    trs = [max(hi - lo, abs(hi - prev[2]), abs(lo - prev[2]))
           for prev, (hi, lo, _c) in zip(run, run[1:])]
    atr = _mean(trs[:n])
    for tr in trs[n:]:
        atr = (atr * (n - 1) + tr) / n
    return atr / run[-1][2] * 100.0


def avg_volume(bars: Sequence[Bar], n: int = VOLUME_SESSIONS
               ) -> Optional[float]:
    """The mean volume of the last n sessions; None with fewer, or when one
    of them has no volume."""
    if n <= 0 or len(bars) < n:
        return None
    vols = [x.volume for x in bars[-n:]]
    if any(v is None for v in vols):
        return None
    return _mean([v for v in vols if v is not None])


def series_stats(bars: Sequence[Bar],
                 benchmark: Optional[Sequence[Bar]] = None
                 ) -> Dict[str, Optional[float]]:
    """Every statistic of one series, by key (MIN_SESSIONS' keys); beta and
    alpha against `benchmark` (None without one)."""
    closes = [x.close for x in bars]
    hi, lo = range_distances(closes)
    beta, alpha = (beta_alpha(bars, benchmark) if benchmark
                   else (None, None))
    out: Dict[str, Optional[float]] = {
        f"sma{n}_pct": sma_distance(closes, n) for n in SMA_PERIODS}
    out.update({
        "rsi14": rsi(closes, RSI_PERIOD),
        "beta": beta, "alpha_pct": alpha,
        "from_high_pct": hi, "from_low_pct": lo,
        "volatility_pct": volatility(closes),
        "return_1m_pct": period_return(closes, MONTH),
        "return_3m_pct": period_return(closes, QUARTER),
        "return_1y_pct": period_return(closes, YEAR),
        "max_drawdown_pct": max_drawdown(closes),
        "atr14_pct": atr_pct(bars, ATR_PERIOD),
        "avg_volume_20": avg_volume(bars, VOLUME_SESSIONS),
    })
    return out


# Why a statistic is blank (blank_reasons' values).
TOO_SHORT = "history"            # fewer closes than its window
TOO_FEW_PAIRS = "paired"         # fewer than YEAR returns on common dates
NO_BENCHMARK = "no-benchmark"    # beta and alpha without a benchmark
FLAT = "flat"                    # RSI: no close moved
FLAT_BENCHMARK = "flat-benchmark"  # beta and alpha: its returns do not vary
NO_HIGH_LOW = "no-high-low"      # ATR: too few sessions in a row with both
NO_VOLUME = "no-volume"          # a session of the window has no volume


def blank_reasons(bars: Sequence[Bar], st: Dict[str, Optional[float]],
                  benchmark: Optional[Sequence[Bar]] = None
                  ) -> Dict[str, str]:
    """{key: why it is blank} for each None in `st` (series_stats(bars,
    benchmark)): TOO_SHORT, TOO_FEW_PAIRS, NO_BENCHMARK, FLAT,
    FLAT_BENCHMARK, NO_HIGH_LOW or NO_VOLUME."""
    n = len(bars)
    out: Dict[str, str] = {}
    for key, v in st.items():
        if v is not None:
            continue
        if key in ("beta", "alpha_pct"):
            out[key] = (NO_BENCHMARK if not benchmark
                        else TOO_FEW_PAIRS
                        if len(paired_returns(bars, benchmark)) < YEAR
                        else FLAT_BENCHMARK)
        elif n < MIN_SESSIONS[key]:
            out[key] = TOO_SHORT
        elif key == "rsi14":
            out[key] = FLAT
        elif key == "atr14_pct":
            out[key] = NO_HIGH_LOW
        elif key == "avg_volume_20":
            out[key] = NO_VOLUME
        else:
            out[key] = TOO_SHORT
    return out


def short_of(bars: Sequence[Bar]) -> List[str]:
    """The statistics `bars` is too short for (MIN_SESSIONS), by key, in
    MIN_SESSIONS' order; beta and alpha's need is the paired returns', which
    the benchmark decides too, so they are left to the caller."""
    n = len(bars)
    return [k for k, need in MIN_SESSIONS.items()
            if k not in ("beta", "alpha_pct") and n < need]
