"""`tjs stress` estimates what an index move does to the holdings, on a
made-up Canadian project (taxable accounts `margin` and `99900099`, a
sheltered `tfsa`) whose books end on 2025-06-20 and whose daily history,
a made-up series per symbol in the project's history cache
(work/.history_cache.json) or handed to the command as an injected live
tier, ends six months later, on 2025-12-19, the valuation date:

- the model (lib/stress): Black-Scholes against the formula worked by
  hand here and textbook values (S=100, K=100, T=1, r=0, vol 0.2: a call
  of 7.97), the volatility of a series built to have 20% exactly, a
  coin's over the calendar;
- a beta-1 and a beta-2 share, a long call, a written put, a written call
  and a long put re-priced at the moved price, the years counted from the
  last close (never from the books' end, nor from what the accounts
  named change), an underlying not held, a contract expired by the
  valuation date left out of the TOTAL and named, a holding with no beta
  left out and named, --beta, --rate, a move of 0, one move, several and
  none, the -100% floor, the narrow terminal, --json and --details;
- one account, a numbered one, a comma list; the header names them;
- the holdings priced in US dollars at the rate, a short position, each
  reason a holding is left out (no history, no currency, no rate, no
  volatility, no strike or expiry, two contract sizes), a declared and a
  ticker.map MULT size, a stale series, a stale benchmark, a coin's
  weekend close (no listing marked), one valuation date whatever the
  accounts, futures, no holdings at all (text and the full --json);
- offline with and without the cache, an injected tier (the view writes
  the history cache and nothing else), the refusal without a benchmark;
- the parser: -10, -10%, -2.5 and 10 are moves, the options go before or
  after them.

The series: the index's daily log returns alternate +A and -A, so the
sample standard deviation of any 252 of them x sqrt(252) is 0.2; ZZQA.TO
is the index scaled (beta 1, last close 100), ZZQB.TO compounds twice the
index's simple returns (beta 2, last close 40), ZZQD.TO 1.5 times (last
close 50), ZZQC.TO has 100 sessions (too few for a beta). No test reaches
the network: the children run offline, the in-process runs inject the
tier. All data is synthetic."""
import io
import json
import math
import shutil
import statistics
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from _style import CapturedWidth, assert_concise
from _support.cli_command_runs import _run_project, _tjs

INDEX = "^ZZQX"                  # a made-up index, the benchmark
BOOKS_END = "2025-06-20"         # the books' end: the last trade
LAST = "2025-12-19"              # the last close: the valuation date
FETCHED = "2025-12-22"           # the day the made-up cache was "fetched"
A = 0.2 * math.sqrt(251) / 252   # the index's daily log return, +/-
CACHE = ".history_cache.json"
MARGIN = """\
BUYSELL 2025-01-06 10:00:00 ZZQA.TO 100 CAD 90 9000 0
BUYSELL 2025-01-06 10:01:00 ZZQB.TO 50 CAD 40 2000 0
BUYSELL 2025-01-06 10:02:00 ZZQC.TO 10 CAD 20 200 0
BUYSELL 2025-01-10 10:00:00 ZZQA250919C00080000.TO 1 CAD 2.5 250 0
BUYSELL 2025-02-03 10:00:00 ZZQA261219C00100000.TO 2 CAD 5 1000 0
BUYSELL 2025-02-03 10:01:00 ZZQA261219P00100000.TO -1 CAD 6 600 0
BUYSELL 2025-02-03 10:02:00 ZZQB261219C00045000.TO -1 CAD 3 300 0
BUYSELL 2025-02-03 10:03:00 ZZQD261219C00050000.TO 2 CAD 3 600 0
BUYSELL 2025-06-20 10:00:00 ZZQC.TO 5 CAD 20 100 0
"""
TFSA = """\
BUYSELL 2025-01-07 10:00:00 ZZQA.TO 20 CAD 90 1800 0
BUYSELL 2025-02-03 10:00:00 ZZQB261219P00040000.TO 1 CAD 4 400 0
"""
NUMBERED = "BUYSELL 2025-01-07 10:00:00 ZZQB.TO 10 CAD 40 400 0\n"
ACCOUNTS = ('[accounts.tfsa]\ntype = "sheltered"\n'
            '[accounts.99900099]\ntype = "taxable"\n')
CALL = "ZZQA261219C00100000.TO"      # 2 long, a year after the last close
PUT = "ZZQA261219P00100000.TO"       # 1 written
WRITTEN_CALL = "ZZQB261219C00045000.TO"
LONG_PUT = "ZZQB261219P00040000.TO"  # the tfsa's, its last trade 2025-02-03
NOT_HELD = "ZZQD261219C00050000.TO"  # on ZZQD.TO, which no account holds
EXPIRED = "ZZQA250919C00080000.TO"   # expired after the books' end
_WIDTH = CapturedWidth()
_P = {}


# ------------------------------------------------------- the hand model

def _n(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _by_hand(right, s, k, t, r, v):
    """Black-Scholes as the textbook writes it, for the checks."""
    d1 = (math.log(s / k) + (r + v * v / 2.0) * t) / (v * math.sqrt(t))
    d2 = d1 - v * math.sqrt(t)
    if right == "C":
        return s * _n(d1) - k * math.exp(-r * t) * _n(d2)
    return k * math.exp(-r * t) * _n(-d2) - s * _n(-d1)


# --------------------------------------------------- the made-up closes

def _sessions(last: str, n: int):
    d, out = date.fromisoformat(last), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return out[::-1]


def _index(n: int):
    rets = [A if i % 2 == 0 else -A for i in range(n - 1)]
    closes = [1.0]
    for r in rets:
        closes.append(closes[-1] * math.exp(r))
    return closes, rets


def _scaled(closes, last):
    return [c * last / closes[-1] for c in closes]


def _geared(rets, beta, last):
    closes = [1.0]
    for r in rets:
        closes.append(closes[-1] * (1.0 + beta * (math.exp(r) - 1.0)))
    return _scaled(closes, last)


def _bars(ds, cs):
    return [[d, c, c * 1.01, c * 0.99, 10000.0] for d, c in zip(ds, cs)]


def _series():
    """{symbol: [day, close, high, low, volume] rows}."""
    days = _sessions(LAST, 300)
    idx, rets = _index(300)
    return {INDEX: _bars(days, _scaled(idx, 5000.0)),
            "ZZQA.TO": _bars(days, _scaled(idx, 100.0)),
            "ZZQB.TO": _bars(days, _geared(rets, 2.0, 40.0)),
            "ZZQD.TO": _bars(days, _geared(rets, 1.5, 50.0)),
            "ZZQC.TO": _bars(days[-100:],
                             [20.0 + 0.01 * i for i in range(100)])}


def _cache_doc():
    from taxjson.lib.price_chain import HISTORY_ADJUSTED, yf_symbol_for
    doc = {}
    for sym, rows in _series().items():
        q = yf_symbol_for(sym)
        doc[f"{sym}|{q}"] = {
            "quote_symbol": q, "fetched": FETCHED, "source": "yfinance",
            "currency": "CAD", "unit": "provider",
            "adjusted": HISTORY_ADJUSTED, "start": rows[0][0], "bars": rows}
    return doc


def setUpModule():
    _WIDTH.start()
    top = Path(tempfile.mkdtemp(prefix="taxjson-stress-"))
    _P["top"] = top
    root = _run_project(top, trades=MARGIN, extra=ACCOUNTS,
                        more_inputs={"tfsa": TFSA, "99900099": NUMBERED})
    (root / "work" / CACHE).write_text(json.dumps(_cache_doc()),
                                       encoding="utf-8")
    _P["root"] = root


def tearDownModule():
    _WIDTH.stop()
    shutil.rmtree(_P.get("top", ""), ignore_errors=True)


def _copy(cache: bool = True) -> Path:
    """A copy of the built project (a folder of its own), with or without
    the history cache."""
    dst = Path(tempfile.mkdtemp(prefix="taxjson-stress-")) / "p"
    shutil.copytree(_P["root"], dst, symlinks=True)
    if not cache:
        (dst / "work" / CACHE).unlink()
    return dst


def _stress(*argv, root=None, width=None):
    env = {"TAXJSON_WIDTH": str(width)} if width else None
    return _tjs(root or _P["root"], "stress", *argv, env_extra=env)


def _json(*argv, root=None):
    r = _stress(*argv, "--json", root=root)
    if r.returncode != 0:
        raise AssertionError(r.stdout + r.stderr)
    return json.loads(r.stdout)


def _in_process(root: Path, *argv, fetchers=None, today=FETCHED):
    """cmd_stress in this process: (exit code, stdout, stderr)."""
    from taxjson.cli.parser import _build_parser
    from taxjson.cli.stress import cmd_stress
    args = _build_parser()[0].parse_args(["-C", str(root), "stress", *argv])
    out, err, code = io.StringIO(), io.StringIO(), 0
    with redirect_stdout(out), redirect_stderr(err):
        try:
            cmd_stress(args, fetchers=fetchers, today=today)
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def _tier(answers, asked=None):
    def _fetch(pairs, start, end):
        if asked is not None:
            asked.append(dict(pairs))
        return {s: answers[s] for s in pairs if s in answers}
    return _fetch


def _acts(out: str):
    return [ln for ln in out.splitlines() if ln.startswith("! ")]


def _rows(out: str):
    """{label: cells} of the table's rows (an option's label: `C 100
    2026-12-19`)."""
    rows = {}
    for ln in out.splitlines():
        parts = ln.split()
        if not parts:
            continue
        if parts[0] in ("C", "P") and ln.startswith("  "):
            rows[" ".join(parts[:3])] = parts[3:]
        elif ((parts[0].startswith("ZZQ") or parts[0] in ("TOTAL", "CHANGE%"))
              and parts[1:2] != ["="]):     # not the legend's `CHANGE% =`
            rows.setdefault(parts[0], parts[1:])
    return rows


def _money(text: str) -> float:
    return float(text.replace(",", "").replace("+", ""))


def _row(doc, symbol):
    return next(r for r in doc["rows"] if r["symbol"] == symbol)


def _vol(doc, symbol):
    return next(h for h in doc["history"] if h["symbol"] == symbol)[
        "volatility"]


class TestTheModel(unittest.TestCase):
    """lib/stress: Black-Scholes, the volatility, the moves."""

    def test_black_scholes_against_the_hand_formula_and_textbook_values(self):
        from taxjson.lib.stress import black_scholes
        cases = [
            # (right, S, K, T, r, vol, the textbook's value)
            ("C", 100, 100, 1.0, 0.0, 0.2, 7.97),
            ("P", 100, 100, 1.0, 0.0, 0.2, 7.97),   # put-call parity, r=0
            ("C", 42, 40, 0.5, 0.10, 0.2, 4.76),    # Hull's example
            ("P", 42, 40, 0.5, 0.10, 0.2, 0.81),
            ("C", 90, 100, 1.0, 0.0, 0.2, 3.59),    # the -10% price below
            ("P", 90, 100, 1.0, 0.0, 0.2, 13.59),
        ]
        for right, s, k, t, r, v, book in cases:
            with self.subTest(right=right, s=s, k=k):
                hand = _by_hand(right, s, k, t, r, v)
                self.assertAlmostEqual(black_scholes(right, s, k, t, r, v),
                                       hand, places=10)
                self.assertAlmostEqual(hand, book, places=2)

    def test_at_expiry_with_no_volatility_or_no_strike(self):
        from taxjson.lib.stress import black_scholes, delta
        self.assertEqual(black_scholes("C", 100, 80, 0.0, 0.0, 0.2), 20.0)
        self.assertEqual(black_scholes("P", 100, 80, -0.1, 0.0, 0.2), 0.0)
        self.assertEqual(black_scholes("P", 70, 80, 0.0, 0.0, 0.2), 10.0)
        self.assertEqual(delta("C", 100, 80, 0.0, 0.0, 0.2), 1.0)
        self.assertEqual(delta("P", 100, 80, 0.0, 0.0, 0.2), 0.0)
        # No volatility: the discounted forward's intrinsic value.
        self.assertAlmostEqual(black_scholes("C", 100, 100, 1.0, 0.05, 0.0),
                               100 - 100 * math.exp(-0.05))
        self.assertAlmostEqual(delta("C", 100, 100, 1.0, 0.0, 0.2),
                               _n(0.1), places=12)
        # No strike: a call is the share, a put nothing (no ZeroDivision).
        self.assertEqual(black_scholes("C", 100, 0, 1.0, 0.0, 0.2), 100.0)
        self.assertEqual(black_scholes("P", 100, 0, 1.0, 0.0, 0.2), 0.0)
        self.assertEqual((delta("C", 100, 0, 1.0, 0.0, 0.2),
                          delta("P", 100, 0, 1.0, 0.0, 0.2)), (1.0, 0.0))

    def test_volatility_of_the_made_up_series(self):
        from taxjson.lib.stress import log_volatility
        closes = [r[1] for r in _series()["ZZQA.TO"]]
        self.assertAlmostEqual(log_volatility(closes), 0.2, places=12)
        self.assertAlmostEqual(log_volatility(closes[-253:]), 0.2, places=12)
        self.assertIsNone(log_volatility(closes[-252:]))

    def test_a_coins_volatility_is_over_the_calendar(self):
        """A coin trades every day: its year is 365 returns, x sqrt(365)."""
        from taxjson.lib.stress import COIN_YEAR, log_volatility
        closes = [r[1] for r in _coin_bars()]
        rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
        self.assertEqual(COIN_YEAR, 365)
        self.assertAlmostEqual(log_volatility(closes, COIN_YEAR),
                               statistics.stdev(rets[-365:]) * math.sqrt(365),
                               places=12)
        self.assertIsNone(log_volatility(closes[-365:], COIN_YEAR))

    def test_moves(self):
        from taxjson.lib.stress import moved, parse_move
        for text, want in (("-10", -10.0), ("-10%", -10.0), ("+5", 5.0),
                           ("2.5%", 2.5), ("-.5", -0.5), ("0", 0.0)):
            self.assertEqual(parse_move(text), want)
        # ASCII digits only: a full-width or Arabic-Indic one is no move.
        for text in ("x", "-10%%", "10 %", "", "--10", "-１０",
                     "١٠"):
            self.assertIsNone(parse_move(text))
        self.assertEqual(moved(2.0, -10.0), (-20.0, False))
        self.assertEqual(moved(3.0, -50.0), (-100.0, True))


class TestOneMove(unittest.TestCase):
    """`tjs stress -10`: offline, from the cache."""

    @classmethod
    def setUpClass(cls):
        cls.r = _stress("-10", "--benchmark", INDEX, width=100)
        cls.rows = _rows(cls.r.stdout)
        cls.doc = _json("-10", "--benchmark", INDEX)

    def test_the_table(self):
        r = self.r
        self.assertEqual(r.returncode, 0, r.stderr)
        head = next(ln for ln in r.stdout.splitlines()
                    if ln.startswith("SYMBOL"))
        self.assertEqual(head.split(), ["SYMBOL", "QTY", "PRICE", "VALUE",
                                        "BETA", "MOVE%", "CHANGE", "CHANGE%"])
        rows = self.rows
        # A beta-1 share (two accounts: 100 + 20) and a beta-2 share (two:
        # 50 + 10).
        self.assertEqual(rows["ZZQA.TO"], ["120", "100.00", "12,000.00",
                                           "1.00", "-10.0", "-1,200.00",
                                           "-10.0"])
        self.assertEqual(rows["ZZQB.TO"], ["60", "40.00", "2,400.00", "2.00",
                                           "-20.0", "-480.00", "-20.0"])
        # Options sit under their underlying, in expiry order; one whose
        # underlying is not held under a row of its own.
        labels = [k for k in rows if k not in ("TOTAL",)]
        self.assertEqual(labels, ["ZZQA.TO", "C 80 2025-09-19",
                                  "C 100 2026-12-19", "P 100 2026-12-19",
                                  "ZZQB.TO", "C 45 2026-12-19",
                                  "P 40 2026-12-19", "ZZQC.TO", "ZZQD.TO",
                                  "C 50 2026-12-19"])
        self.assertEqual(rows["ZZQD.TO"], ["50.00", "1.50", "-15.0"])
        self.assertIn("\nBenchmark:  ^ZZQX (--benchmark), last close "
                      "2025-12-19\nPositions:  the books' end, 2025-06-20, of "
                      "3 accounts: 99900099, margin, tfsa (registered: tfsa)\n"
                      "Valued:     at the closes of 2025-12-19, in CAD\n"
                      "Options:    Black-Scholes, rate 0% (the default, "
                      "--rate R), the volatility of 1 year, held\n\n",
                      r.stdout)
        self.assertTrue(r.stdout.startswith(
            "STRESS — -10% in ^ZZQX: 9 holdings\n"), r.stdout[:200])
        assert_concise(self, r.stdout, r.stderr,
                       legend=["BETA vs ^ZZQX, 252 daily returns",
                               "an option's PRICE: the model's"])

    def test_the_total_and_its_change_over_the_long_value(self):
        doc = self.doc
        inc = [r for r in doc["rows"] if r["in_total"]]
        value = sum(r["value"] for r in inc)
        long_value = sum(r["value"] for r in inc if r["value"] > 0)
        change = sum(r["moves"][0]["change"] for r in inc)
        self.assertAlmostEqual(doc["total_value"], value, places=6)
        self.assertAlmostEqual(doc["long_value"], long_value, places=6)
        self.assertAlmostEqual(doc["totals"][0]["change"], change, places=6)
        self.assertAlmostEqual(doc["totals"][0]["change_pct"],
                               change / long_value * 100, places=3)
        tot = self.rows["TOTAL"]
        self.assertEqual(_money(tot[0]), doc["total_value"])
        self.assertEqual(_money(tot[1]), doc["totals"][0]["change"])
        self.assertEqual(tot[2], f"{change / long_value * 100:+.1f}")
        self.assertIn("CHANGE% = CHANGE / |VALUE|, the TOTAL's / the "
                      "positive VALUEs", self.r.stdout)

    def test_the_years_count_from_the_last_close(self):
        """S=K=100, T = 365 days / 365 from the last close (the books end
        six months earlier): the textbook's 7.97."""
        now = _by_hand("C", 100, 100, 1.0, 0.0, 0.2)
        call = self.rows["C 100 2026-12-19"]
        self.assertEqual(call[:4], ["2", "7.97", "1,593.11", "1.00"])
        self.assertAlmostEqual(_money(call[2]), 2 * 100 * now, delta=0.005)
        self.assertAlmostEqual(
            _money(call[5]),
            2 * 100 * (_by_hand("C", 90, 100, 1.0, 0.0, 0.2) - now),
            delta=0.01)
        o = _row(self.doc, CALL)["option"]
        self.assertEqual((o["years"], _row(self.doc, CALL)["valued_on"]),
                         (1.0, LAST))
        self.assertEqual((self.doc["as_of"], self.doc["expiry_from"],
                          self.doc["latest_close_date"],
                          self.doc["coins_valued_on"]),
                         (BOOKS_END, LAST, LAST, None))

    def test_a_written_put_and_a_written_call(self):
        put = self.rows["P 100 2026-12-19"]
        self.assertEqual(put[:4], ["-1", "7.97", "-796.56", "1.00"])
        self.assertAlmostEqual(
            _money(put[5]),
            -1 * 100 * (_by_hand("P", 90, 100, 1.0, 0.0, 0.2)
                        - _by_hand("P", 100, 100, 1.0, 0.0, 0.2)),
            delta=0.01)
        self.assertEqual(put[5], "-562.35")
        call = self.rows["C 45 2026-12-19"]
        self.assertEqual(call[0], "-1")
        self.assertTrue(call[5].startswith("+"), call)
        doc = _json("-10", "10", "--benchmark", INDEX)
        row = _row(doc, WRITTEN_CALL)
        self.assertGreater(row["moves"][0]["change"], 0)
        self.assertLess(row["moves"][1]["change"], 0)
        # Its underlying moves twice the index: S = 40 x 0.8 at -10%.
        self.assertEqual([m["price_move_pct"] for m in row["moves"]],
                         [-20.0, 20.0])
        vol = _vol(doc, "ZZQB.TO")
        self.assertAlmostEqual(
            row["moves"][0]["change"],
            -100 * (_by_hand("C", 32, 45, 1.0, 0.0, vol)
                    - _by_hand("C", 40, 45, 1.0, 0.0, vol)), delta=0.01)

    def test_a_long_put_and_an_underlying_not_held(self):
        vol = _vol(self.doc, "ZZQB.TO")
        put = _row(self.doc, LONG_PUT)
        self.assertEqual((put["qty"], put["accounts"]), (1.0, {"tfsa": 1.0}))
        self.assertAlmostEqual(put["value"],
                               100 * _by_hand("P", 40, 40, 1.0, 0.0, vol),
                               delta=0.005)
        self.assertAlmostEqual(
            put["moves"][0]["change"],
            100 * (_by_hand("P", 32, 40, 1.0, 0.0, vol)
                   - _by_hand("P", 40, 40, 1.0, 0.0, vol)), delta=0.01)
        self.assertGreater(put["moves"][0]["change"], 0)
        und = _row(self.doc, "ZZQD.TO")
        self.assertEqual((und["kind"], und["qty"], und["value"],
                          und["in_total"]), ("underlying", None, None, False))
        self.assertAlmostEqual(und["beta"], 1.5, places=2)
        call = _row(self.doc, NOT_HELD)
        vol_d = _vol(self.doc, "ZZQD.TO")
        pct = call["moves"][0]["price_move_pct"]
        self.assertAlmostEqual(call["value"],
                               200 * _by_hand("C", 50, 50, 1.0, 0.0, vol_d),
                               delta=0.005)
        self.assertAlmostEqual(
            call["moves"][0]["change"],
            200 * (_by_hand("C", 50 * (1 + pct / 100), 50, 1.0, 0.0, vol_d)
                   - _by_hand("C", 50, 50, 1.0, 0.0, vol_d)), delta=0.01)
        self.assertTrue(call["in_total"])

    def test_an_expired_contract_is_left_out_and_named(self):
        """C 80 2025-09-19: held at the books' end, expired before the
        closes the view is valued at: its intrinsic value, max(100 - 80,
        0) x 100, out of the TOTAL, named by default."""
        self.assertEqual(self.rows["C 80 2025-09-19"],
                         ["1", "20.00", "2,000.00"])
        row = _row(self.doc, EXPIRED)
        self.assertEqual((row["left_out"], row["in_total"], row["value"],
                          row["moves"]), ("expired", False, 2000.0, []))
        self.assertEqual((row["option"]["expired"], row["option"]["years"]),
                         (True, 0.0))
        self.assertEqual(self.doc["left_out"]["expired"], [EXPIRED])
        self.assertIn(f"! expired but in the books, not in the TOTAL: "
                      f"2,000.00 intrinsic ({EXPIRED}) — tjs run",
                      _acts(self.r.stdout))
        r = _stress("-10", "--benchmark", INDEX, "--details")
        self.assertIn(f"- {EXPIRED}: expired on 2025-09-19, on or before the "
                      f"closes of 2025-12-19, but the books still hold it: "
                      f"its expiry, exercise or assignment row is missing "
                      f"(tjs run names it too).", " ".join(
                          r.stdout.replace("\n  ", " ").split(" ")))

    def test_a_holding_with_no_beta_is_left_out_and_named(self):
        self.assertEqual(self.rows["ZZQC.TO"], ["15", "20.99", "314.85"])
        self.assertEqual(_acts(self.r.stdout), [
            "! no beta: 1 holding, 314.85 of value, not in the TOTAL "
            "(ZZQC.TO) — tjs stress --beta SYMBOL=X",
            f"! expired but in the books, not in the TOTAL: 2,000.00 "
            f"intrinsic ({EXPIRED}) — tjs run",
            f"! TAXJSON_OFFLINE set: 5 series from the history cache of "
            f"{FETCHED} — unset it to fetch"])
        row = _row(self.doc, "ZZQC.TO")
        self.assertEqual((row["left_out"], row["in_total"], row["beta"]),
                         ("no-beta", False, None))
        self.assertEqual(self.doc["left_out_value"], 2314.85)
        self.assertEqual(self.doc["left_out"]["no-beta"], ["ZZQC.TO"])
        r = _stress("-10", "--benchmark", INDEX, "--details", width=200)
        self.assertIn("- ZZQC.TO: not in the TOTAL: no beta (fewer than 252 "
                      "daily returns on dates the benchmark has too); --beta "
                      "ZZQC.TO=X gives one.", r.stdout)


class TestTheAccounts(unittest.TestCase):
    def test_one_contract_one_figure_whichever_accounts_are_named(self):
        """The tfsa's put: the same figures read alone, with every
        account, or in a list; only the positions' date moves."""
        docs = [_json(*a, "-10", "--benchmark", INDEX)
                for a in ([], ["tfsa"], ["margin,tfsa"], ["tfsa,99900099"])]
        rows = [_row(d, LONG_PUT) for d in docs]
        for row in rows[1:]:
            self.assertEqual(row, rows[0])
        self.assertEqual((rows[0]["option"]["years"], rows[0]["valued_on"]),
                         (1.0, LAST))
        self.assertEqual([(d["as_of"], d["expiry_from"]) for d in docs],
                         [(BOOKS_END, LAST), ("2025-02-03", LAST),
                          (BOOKS_END, LAST), ("2025-02-03", LAST)])

    def test_one_account(self):
        doc = _json("tfsa", "-10", "--benchmark", INDEX)
        self.assertEqual([r["symbol"] for r in doc["rows"]],
                         ["ZZQA.TO", "ZZQB.TO", LONG_PUT])
        self.assertEqual(_row(doc, "ZZQB.TO")["kind"], "underlying")
        r = _stress("tfsa", "-10", "--benchmark", INDEX)
        self.assertIn("Positions:  the books' end, 2025-02-03, of 1 account: "
                      "tfsa (registered)\n", r.stdout)

    def test_a_numbered_account_alone(self):
        doc = _json("99900099", "-10", "--benchmark", INDEX)
        self.assertEqual([(r["symbol"], r["value"]) for r in doc["rows"]],
                         [("ZZQB.TO", 400.0)])
        r = _stress("99900099", "-10", "--benchmark", INDEX)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("of 1 account: 99900099\n", r.stdout)

    def test_a_comma_list(self):
        both = _json("99900099,margin,tfsa", "-10", "--benchmark", INDEX)
        every = _json("-10", "--benchmark", INDEX)
        self.assertEqual(both["rows"], every["rows"])
        self.assertEqual(both["totals"], every["totals"])
        r = _stress("margin,99900099", "-10", "--benchmark", INDEX)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("of 2 accounts: 99900099, margin\n", r.stdout)
        r = _stress("margin,nosuch", "-10", "--benchmark", INDEX)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("nosuch", r.stderr)


class TestTheOptions(unittest.TestCase):
    def test_beta_supplies_and_overrides(self):
        r = _stress("-10", "--benchmark", INDEX, "--beta", "zzqc.to=0.5",
                    "--beta", "ZZQB.TO=1", width=100)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = _rows(r.stdout)
        # Supplied: in the TOTAL now, the beta marked as given, by a mark
        # of its own (PRICE's * is a stale close; backlog TJ-5428).
        self.assertEqual(rows["ZZQC.TO"], ["15", "20.99", "314.85", "0.50#",
                                           "-5.0", "-15.74", "-5.0"])
        # Overridden: 1 for 2, its written call re-priced at 40 x 0.9.
        self.assertEqual(rows["ZZQB.TO"][3:6], ["1.00#", "-10.0", "-240.00"])
        self.assertEqual(rows["C 45 2026-12-19"][3:5], ["1.00#", "-10.0"])
        self.assertIn("BETA vs ^ZZQX, 252 daily returns (# --beta); MOVE% = "
                      "BETA x -10%.", r.stdout)
        self.assertNotIn("*", "".join(c for cells in rows.values()
                                      for c in cells))
        self.assertNotIn("! no beta", r.stdout)
        doc = _json("-10", "--benchmark", INDEX, "--beta", "ZZQC.TO=0.5")
        self.assertEqual(_row(doc, "ZZQC.TO")["beta_from"], "--beta")
        self.assertEqual(_row(doc, "ZZQA.TO")["beta_from"], "history")

    def test_beta_refusals(self):
        for argv, words in (
                ([f"{CALL}=1"], "an option moves with its underlying"),
                (["NOPE.TO=1"], "no holding of these accounts is it"),
                (["ZZQA.TO=x"], "is not SYMBOL=BETA"),
                (["ZZQA.TO=1e3"], "is not SYMBOL=BETA"),
                (["ZZQA.TO=１"], "is not SYMBOL=BETA"),
                (["ZZQA.TO=11"], "a beta from -10 to 10"),
                (["ZZQA.TO=-10.5"], "a beta from -10 to 10"),
                (["ZZQA.TO=1", "--beta", "ZZQA.TO=2"], "is given twice")):
            with self.subTest(argv=argv):
                r = _stress("-10", "--benchmark", INDEX, "--beta", *argv)
                self.assertEqual(r.returncode, 2, r.stdout)
                self.assertIn(words, r.stderr)
        r = _stress("-10", "--benchmark", INDEX, "--beta", f"{CALL}=1")
        self.assertIn("--beta ZZQA.TO=X", r.stderr)
        self.assertEqual(_stress("-10", "--benchmark", INDEX, "--beta",
                                 "ZZQA.TO=-10").returncode, 0)

    def test_rate(self):
        doc = _json("-10", "--benchmark", INDEX, "--rate", "4")
        o = _row(doc, CALL)["option"]
        self.assertAlmostEqual(o["model_price"],
                               _by_hand("C", 100, 100, 1.0, 0.04, 0.2),
                               places=5)
        self.assertAlmostEqual(o["model_price"], 9.93, places=2)
        self.assertEqual((doc["model"]["rate_pct"], doc["model"]["rate_from"]),
                         (4.0, "--rate"))
        r = _stress("-10", "--benchmark", INDEX, "--rate", "4%")
        self.assertIn("Black-Scholes, rate 4% (--rate)", r.stdout)
        r = _stress("-10", "--benchmark", INDEX, "--rate", "x")
        self.assertEqual(r.returncode, 2)

    def test_a_ticker_map_mult_size(self):
        root = _copy()
        (root / "ticker.map").write_text("MULT ZZQD 10\n", encoding="utf-8")
        doc = _json("-10", "--benchmark", INDEX, root=root)
        row = _row(doc, NOT_HELD)
        self.assertEqual((row["option"]["size"], row["option"]["size_from"]),
                         (10.0, "ticker.map MULT"))
        self.assertAlmostEqual(
            row["value"],
            20 * _by_hand("C", 50, 50, 1.0, 0.0, _vol(doc, "ZZQD.TO")),
            delta=0.005)

    def test_two_sizes_in_one_book_leave_the_contract_out(self):
        """The rows of one base book declare 10 and 100: the gains file's
        line carries none, the view reads the rows and never takes 100."""
        root = _copy()
        p = root / "work" / "margin_base.json"
        book = json.loads(p.read_text(encoding="utf-8"))
        txs = book["transactions"]
        row = next(t for t in txs if t.get("symbol") == NOT_HELD)
        row["multiplier"] = 10.0
        txs.append(dict(row, multiplier=100.0))
        p.write_text(json.dumps(book), encoding="utf-8")
        code, out, err = _in_process(root, "-10", "--benchmark", INDEX,
                                     "--json")
        self.assertEqual(code, 0, err)
        row = _row(json.loads(out), NOT_HELD)
        self.assertEqual((row["left_out"], row["value"], row["option"]),
                         ("no-size", None, None))
        code, out, err = _in_process(root, "-10", "--benchmark", INDEX,
                                     "--details")
        self.assertIn(f"- {NOT_HELD}: not in the TOTAL: the books declare 10, "
                      f"100 (ZZQD.TO); a ticker.map MULT line gives the size.",
                      " ".join(out.replace("\n  ", " ").split(" ")))


class TestTheMoves(unittest.TestCase):
    def test_several_moves_one_column_a_move(self):
        r = _stress("-20", "-10%", "+10", "--benchmark", INDEX, width=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        head = next(ln for ln in r.stdout.splitlines()
                    if ln.startswith("SYMBOL"))
        self.assertEqual(head.split(), ["SYMBOL", "QTY", "PRICE", "VALUE",
                                        "BETA", "-20%", "-10%", "+10%"])
        rows = _rows(r.stdout)
        self.assertEqual(rows["ZZQA.TO"][4:], ["-2,400.00", "-1,200.00",
                                               "+1,200.00"])
        doc = _json("-20", "-10%", "+10", "--benchmark", INDEX)
        self.assertEqual([_money(c) for c in rows["TOTAL"][1:]],
                         [t["change"] for t in doc["totals"]])
        self.assertEqual(rows["CHANGE%"], [f"{t['change_pct']:+.1f}"
                                           for t in doc["totals"]])
        self.assertIn("STRESS — -20%, -10%, +10% in ^ZZQX:", r.stdout)
        self.assertIn("CHANGE%: the TOTAL's / the positive VALUEs; an "
                      "option's PRICE: the model's.", r.stdout)

    def test_no_move_the_default_five(self):
        doc = _json("--benchmark", INDEX)
        self.assertEqual(doc["moves_pct"], [-20.0, -10.0, -5.0, 5.0, 10.0])
        self.assertEqual(len(doc["totals"]), 5)
        r = _stress("--benchmark", INDEX, width=120)
        head = next(ln for ln in r.stdout.splitlines()
                    if ln.startswith("SYMBOL"))
        self.assertEqual(head.split()[5:], ["-20%", "-10%", "-5%", "+5%",
                                            "+10%"])

    def test_a_move_of_0(self):
        doc = _json("0", "--benchmark", INDEX)
        self.assertEqual(doc["moves_pct"], [0.0])
        self.assertEqual(doc["totals"][0]["change"], 0.0)
        self.assertEqual({m["change"] for r in doc["rows"] if r["in_total"]
                          for m in r["moves"]}, {0.0})
        r = _stress("0", "--benchmark", INDEX, width=100)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_rows(r.stdout)["ZZQA.TO"][4:], ["0.0", "0.00",
                                                          "0.0"])

    def test_the_floor(self):
        """-60% on a beta-2 share is -120%: held at -100%, said."""
        doc = _json("-60", "--benchmark", INDEX)
        m = _row(doc, "ZZQB.TO")["moves"][0]
        self.assertEqual((m["price_move_pct"], m["held_at_floor"],
                          m["change"]), (-100.0, True, -2400.0))
        r = _stress("-60", "--benchmark", INDEX, width=100)
        self.assertIn("! MOVE% held at -100% (BETA x the index move is below "
                      "it) (ZZQB.TO) — tjs stress --details", _acts(r.stdout))

    def test_a_narrow_terminal_keeps_every_column(self):
        """Too wide for 100 columns: one block a row, every cell kept."""
        r = _stress("--benchmark", INDEX, width=100)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(all(len(ln) <= 100 for ln in r.stdout.splitlines()))
        block = r.stdout.split("\nZZQA.TO\n", 1)[1].split("\n\n", 1)[0]
        for cell in ("qty 120", "price 100.00", "value 12,000.00",
                     "beta 1.00", "-20% -2,400.00", "-10% -1,200.00",
                     "-5% -600.00", "+5% +600.00", "+10% +1,200.00"):
            self.assertIn(cell, block)

    def test_the_parser_reads_negative_numbers_as_moves(self):
        """argparse's private `_negative_number_matcher`, set on this
        command's parser alone: -10, -10% and -2.5 are moves, as 10 is;
        the options go before or after the ACCOUNT and the moves."""
        from taxjson.cli.parser import _build_parser
        from taxjson.cli.stress import NEGATIVE_WORD
        p = _build_parser()[0]
        for argv, account, moves in (
                (["-10"], None, ["-10"]),
                (["-10%"], None, ["-10%"]),
                (["-2.5"], None, ["-2.5"]),
                (["10"], None, ["10"]),
                (["-10%", "-5.5%", "+5"], None, ["-10%", "-5.5%", "+5"]),
                (["--json", "-10"], None, ["-10"]),
                (["margin,tfsa", "-10"], "margin,tfsa", ["-10"]),
                (["margin", "-10", "--details"], "margin", ["-10"]),
                (["--details", "--rate", "-0.5", "margin", "-10"], "margin",
                 ["-10"]),
                ([], None, [])):
            with self.subTest(argv=argv):
                a = p.parse_args(["stress", *argv])
                self.assertEqual((a.account, a.moves), (account, moves))
        for word in ("-10", "-10%", "-2.5", "-.5"):
            self.assertTrue(NEGATIVE_WORD.match(word), word)
        for word in ("10", "--json", "-x", "-", "--10"):
            self.assertFalse(NEGATIVE_WORD.match(word), word)
        for bad in (["-10", "margin"], ["-10", "x%"],
                    ["margin", "--details", "-10"]):
            with self.subTest(bad=bad), \
                    redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as e:
                p.parse_args(["stress", *bad])
            self.assertEqual(e.exception.code, 2)
        sub = next(a for a in p._actions if a.dest == "cmd").choices["stress"]
        self.assertIs(sub._negative_number_matcher, NEGATIVE_WORD)
        self.assertIn("(the ACCOUNT and the MOVEs together, the options "
                      "before or after them)", sub.format_usage())
        r = _stress("-150", "--benchmark", INDEX)
        self.assertEqual(r.returncode, 2)
        self.assertIn("move '-150' is not a % from above -100", r.stderr)


class TestTheOutputs(unittest.TestCase):
    def test_json(self):
        from taxjson.lib.json_contract import SCHEMAS
        doc = _json("-10", "--benchmark", INDEX)
        self.assertEqual(doc["schema_version"], SCHEMAS["stress"])
        self.assertEqual(sorted(doc), sorted(JSON_KEYS))
        self.assertEqual(doc["currency"], "CAD")
        self.assertEqual(doc["benchmark"]["symbol"], INDEX)
        self.assertEqual(doc["model"], {
            "rate_pct": 0.0, "rate_from": "default", "beta_returns": 252,
            "volatility_returns": 252, "coin_volatility_returns": 365,
            "year_days": 365.0, "volatility_held": True, "fx_held": True,
            "time_passes": False, "cash_modelled": False})
        row = _row(doc, CALL)
        self.assertEqual((row["kind"], row["underlying"], row["qty"],
                          row["value"], row["in_total"]),
                         ("option", "ZZQA.TO", 2.0, 1593.11, True))
        o = row["option"]
        self.assertEqual((o["right"], o["strike"], o["expiry"], o["size"],
                          o["size_from"], o["years"], o["volatility"]),
                         ("C", 100.0, "2026-12-19", 100.0, "standard", 1.0,
                          0.2))
        self.assertAlmostEqual(o["delta"], _n(0.1), places=6)
        self.assertEqual(row["moves"], [{
            "move_pct": -10.0, "price_move_pct": -10.0,
            "held_at_floor": False, "change": -875.29,
            "change_pct": -54.9422}])
        self.assertEqual(_row(doc, "ZZQA.TO")["accounts"],
                         {"margin": 100.0, "tfsa": 20.0})
        self.assertEqual(sorted(doc["left_out"]), sorted([
            "futures", "no-history", "no-currency", "no-rate",
            "no-volatility", "no-terms", "no-size", "no-beta", "expired"]))

    def test_details(self):
        r = _stress("-10", "--benchmark", INDEX, "--details", width=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        for heading in ("BY ACCOUNT", "OPTIONS", "HISTORY", "NOTES",
                        "DEFINITIONS"):
            self.assertIn(f"\n{heading}\n", r.stdout)
        opt = next(ln for ln in r.stdout.splitlines()
                   if ln.startswith(CALL))
        self.assertEqual(opt.split(), [CALL, "100", "100.00", "100",
                                       "2026-12-19", "1.000", "20.0", "0",
                                       "7.9656", "+0.540"])
        tfsa = [ln.split() for ln in r.stdout.splitlines()
                if ln.startswith("tfsa ")]
        put = _row(_json("-10", "--benchmark", INDEX), LONG_PUT)
        self.assertEqual(tfsa, [
            ["tfsa", "ZZQA.TO", "20", "2,000.00", "-200.00"],
            ["tfsa", LONG_PUT, "1", f"{put['value']:,.2f}",
             f"{put['moves'][0]['change']:+,.2f}"],
            ["tfsa", "TOTAL", f"{2000 + put['value']:,.2f}",
             f"{put['moves'][0]['change'] - 200:+,.2f}"]])
        text = " ".join(r.stdout.replace("\n  ", " ").split(" "))
        for words in (
                "- A view only: no tax figure reads it. Not advice",
                "No contract size in the books for 6 options: the standard "
                "100",
                "- The volatility is held. It usually rises in a drop, which "
                "raises every option's price: written options lose more, and "
                "long options less, than shown.",
                "prices: the closes of 2025-12-19 (the newest close of a "
                "listing or the benchmark), daily, adjusted for "
                "splits and dividends, from the history cache, fetched "
                "2025-12-22."):
            self.assertIn(words, text)
        self.assertNotIn("loss is understated", r.stdout)
        self.assertNotIn("More: tjs stress --details", r.stdout)


JSON_KEYS = ["schema_version", "as_of", "expiry_from", "coins_valued_on",
             "latest_close_date", "currency", "adjusted", "moves_pct",
             "benchmark", "model", "fx", "rows", "totals", "total_value",
             "long_value", "left_out_value", "left_out", "history"]


# --------------------------------------- the edge cases, in this process

def _entry(qty, **kw):
    return {"qty": float(qty), "cost": 0.0, "accounts": {"margin": float(qty)},
            "unknown_cost": False, **kw}


STALE = "2025-09-19"             # ZZQF.TO's last close
SUNDAY = "2025-12-21"            # a coin's weekend close, after LAST
EDGE = {
    "ZZQA.TO": _entry(10),
    "ZZQE.TO": _entry(-100),                      # a short, beta 1, close 50
    "ZZQU.US": _entry(10),                        # in USD, beta 1, close 50
    "ZZQV.US": _entry(10),                        # in EUR: no rate
    "ZZQW.DE": _entry(10),                        # a series with no currency
    "ZZQN.TO": _entry(10),                        # no history
    "ZZQF.TO": _entry(10),                        # stale, beta 1, close 50
    "ZZQK": _entry(2),                            # a coin, in USD, close 20
    "ZZQC261219C00020000.TO": _entry(1),          # ZZQC.TO: no volatility
    "ZZQA261219C00000000.TO": _entry(1),          # a strike of 0
    "ZZQA251340C00100000.TO": _entry(1),          # no such expiry
    "ZZQA261219C00110000.TO": _entry(1, sizes={10.0, 100.0}),
    "ZZQA261219C00120000.TO": _entry(1, size_conflict=True),
    "ZZQA261219C00130000.TO": _entry(3, sizes={10.0}),   # declared: 10
    "ZZQF261219C00050000.TO": _entry(1),          # on the stale series
}


def _coin_bars():
    """The coin: every calendar day for 400 days to LAST, the index's last
    close on each (flat over a weekend), scaled to 20."""
    days = _sessions(LAST, 400)
    idx = dict(zip(days, _scaled(_index(400)[0], 20.0)))
    d, out, last = date.fromisoformat(days[0]), [], None
    while d.isoformat() <= LAST:
        last = idx.get(d.isoformat(), last)
        out.append([d.isoformat(), last, last, last, 1.0])
        d += timedelta(days=1)
    return out


def _edge_tier():
    days = _sessions(LAST, 400)
    idx, _rets = _index(400)
    k = days.index(STALE) + 1
    rows = {INDEX: (_bars(days, _scaled(idx, 5000.0)), "CAD"),
            "ZZQA.TO": (_bars(days, _scaled(idx, 100.0)), "CAD"),
            "ZZQE.TO": (_bars(days, _scaled(idx, 50.0)), "CAD"),
            "ZZQU.US": (_bars(days, _scaled(idx, 50.0)), "USD"),
            "ZZQV.US": (_bars(days, _scaled(idx, 50.0)), "EUR"),
            "ZZQW.DE": (_bars(days, _scaled(idx, 50.0)), None),
            "ZZQF.TO": (_bars(days[:k], _scaled(idx[:k], 50.0)), "CAD"),
            "ZZQK": (_coin_bars(), "USD"),
            "ZZQC.TO": (_bars(days[-100:],
                              [20.0 + 0.01 * i for i in range(100)]), "CAD")}
    return _tier(rows)


class TestTheEdges(unittest.TestCase):
    """A made-up inventory (cli/positions held_by_symbol replaced) over
    the built project, the closes from an injected tier, a US-dollar rate
    in work/to_base.csv."""

    @classmethod
    def setUpClass(cls):
        cls.root = _copy(cache=False)
        (cls.root / "work" / "to_base.csv").write_text(
            "2025-06-20 12:00:00 USD CAD 1.3500 boc\n", encoding="utf-8")
        cls.code, cls.out, cls.err = cls._run("-10", "--benchmark", INDEX)
        cls.rows = _rows(cls.out)
        code, out, err = cls._run("-10", "--benchmark", INDEX, "--json")
        assert code == 0, err
        cls.doc = json.loads(out)
        code, out, _err = cls._run("-10", "--benchmark", INDEX, "--details")
        cls.details = " ".join(out.replace("\n  ", " ").split(" "))

    @classmethod
    def _run(cls, *argv, held=None):
        from taxjson.cli import stress as ST
        held = EDGE if held is None else held
        with mock.patch.object(ST, "held_by_symbol",
                               lambda files, **kw: (dict(held), 2025)):
            return _in_process(cls.root, *argv, fetchers=[_edge_tier()])

    def test_a_listing_in_another_currency_at_the_rate(self):
        self.assertEqual(self.code, 0, self.err)
        self.assertEqual(self.rows["ZZQU.US"], ["10", "67.50", "675.00", "1.00",
                                                "-10.0", "-67.50", "-10.0"])
        row = _row(self.doc, "ZZQU.US")
        self.assertEqual((row["currency"], row["fx_rate"]), ("USD", 1.35))
        self.assertEqual(self.doc["fx"]["USD"], {"rate": 1.35,
                                                 "date": BOOKS_END})
        self.assertIn("\nRates:      USD 1.3500 (2025-06-20), held constant\n",
                      self.out)
        self.assertIn("- Priced in another currency: ZZQK (USD), ZZQU.US "
                      "(USD). A BETA is of the listing's own-currency returns "
                      "against the benchmark's (CAD), and the rate to CAD is "
                      "held: a CHANGE carries no exchange-rate move.",
                      self.details)

    def test_a_coin_at_the_rate_its_volatility_over_the_calendar(self):
        row = _row(self.doc, "ZZQK")
        self.assertEqual((row["kind"], row["value"]), ("crypto", 54.0))
        closes = [b[1] for b in _coin_bars()]
        rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
        self.assertAlmostEqual(
            _vol(self.doc, "ZZQK"),
            statistics.stdev(rets[-365:]) * math.sqrt(365), places=6)

    def test_a_short_position(self):
        self.assertEqual(self.rows["ZZQE.TO"], ["-100", "50.00", "-5,000.00",
                                                "1.00", "-10.0", "+500.00",
                                                "+10.0"])

    def test_each_reason_to_leave_a_holding_out(self):
        lo = self.doc["left_out"]
        self.assertEqual((lo["no-history"], lo["no-currency"], lo["no-rate"],
                          lo["no-volatility"]),
                         (["ZZQN.TO"], ["ZZQW.DE"], ["ZZQV.US"],
                          ["ZZQC261219C00020000.TO"]))
        self.assertEqual(sorted(lo["no-terms"]),
                         ["ZZQA251340C00100000.TO", "ZZQA261219C00000000.TO"])
        self.assertEqual(lo["no-size"], ["ZZQA261219C00110000.TO",
                                         "ZZQA261219C00120000.TO"])
        for sym in lo["no-terms"] + lo["no-size"] + lo["no-history"]:
            row = _row(self.doc, sym)
            self.assertEqual((row["value"], row["in_total"], row["moves"]),
                             (None, False, []))
        self.assertIn("! 8 holdings with no value, not in the TOTAL "
                      "(ZZQA251340C00100000.TO", "\n".join(_acts(self.out)))
        for words in (
                "- ZZQA261219C00000000.TO: not in the TOTAL: its symbol gives "
                "no strike above 0, expiry or right (C or P) to price it with "
                "(ZZQA.TO).",
                "- ZZQA261219C00110000.TO: not in the TOTAL: the books declare "
                "10, 100 (ZZQA.TO); a ticker.map MULT line gives the size.",
                "- ZZQA261219C00120000.TO: not in the TOTAL: the books declare "
                "more than one contract size (ZZQA.TO)",
                "- ZZQN.TO: not in the TOTAL: no daily history.",
                "- ZZQV.US: not in the TOTAL: no rate to the base currency",
                "- ZZQW.DE: not in the TOTAL: its series names no currency.",
                "- ZZQC261219C00020000.TO: not in the TOTAL: fewer than 253 "
                "closes of its underlying"):
            self.assertIn(words, self.details)

    def test_a_declared_size(self):
        o = _row(self.doc, "ZZQA261219C00130000.TO")["option"]
        self.assertEqual((o["size"], o["size_from"]), (10.0, "declared"))
        self.assertAlmostEqual(
            _row(self.doc, "ZZQA261219C00130000.TO")["value"],
            30 * _by_hand("C", 100, 130, 1.0, 0.0, 0.2), delta=0.005)

    def test_a_stale_series_is_marked_and_stays_in_the_total(self):
        """ZZQF.TO's closes end 2025-09-19: its PRICE marked *, in the
        TOTAL, its option's years counted from that close."""
        self.assertEqual(self.rows["ZZQF.TO"][:2], ["10", "50.00*"])
        row = _row(self.doc, "ZZQF.TO")
        self.assertEqual((row["valued_on"], row["in_total"]), (STALE, True))
        call = _row(self.doc, "ZZQF261219C00050000.TO")
        years = (date(2026, 12, 19) - date(2025, 9, 19)).days / 365
        self.assertEqual((call["valued_on"], call["in_total"]), (STALE, True))
        self.assertAlmostEqual(call["option"]["years"], years, places=6)
        self.assertAlmostEqual(call["value"],
                               100 * _by_hand("C", 50, 50, years, 0.0, 0.2),
                               delta=0.005)
        self.assertTrue(self.rows["C 50 2026-12-19"][1].endswith("*"))
        self.assertIn("! PRICE marked *: last close older than 2025-12-19 for "
                      "1 symbol (ZZQF.TO) — tjs stress --details",
                      _acts(self.out))

    def test_a_stale_benchmark(self):
        tier = _edge_tier()

        def short_index(pairs, start, end):
            got = tier(pairs, start, end)
            if INDEX in got:
                got[INDEX] = (got[INDEX][0][:-1], "CAD")
            return got
        from taxjson.cli import stress as ST
        with mock.patch.object(ST, "held_by_symbol", lambda files, **kw: (
                {"ZZQA.TO": _entry(10)}, 2025)):
            code, out, err = _in_process(_copy(cache=False), "-10",
                                         "--benchmark", INDEX,
                                         fetchers=[short_index])
        self.assertEqual(code, 0, err)
        self.assertIn("! BETA ends on 2025-12-18: the benchmark's last close is "
                      "older than the holdings' (2025-12-19)", _acts(out))

    def test_a_coins_weekend_close_marks_no_listing(self):
        """The coin's closes run to Sunday 2025-12-21, the listings' and
        the benchmark's to Friday 2025-12-19: a listing and its option are
        judged against the listings' newest close, the coin against the
        coins', so nothing is marked; the coin is valued at Sunday's close
        and the header says both dates."""
        tier = _edge_tier()

        def weekend(pairs, start, end):
            got = tier(pairs, start, end)
            if "ZZQK" in got:
                rows, cur = got["ZZQK"]
                got["ZZQK"] = (rows + [[d, 25.0, 25.0, 25.0, 1.0]
                                       for d in ("2025-12-20", SUNDAY)], cur)
            return got
        from taxjson.cli import stress as ST
        root = _copy(cache=False)
        (root / "work" / "to_base.csv").write_text(
            "2025-06-20 12:00:00 USD CAD 1.3500 boc\n", encoding="utf-8")
        held = {"ZZQA.TO": _entry(10), "ZZQU.US": _entry(10),
                "ZZQK": _entry(2), CALL: _entry(1)}
        runs = []
        for argv in ((), ("--json",), ("--details",)):
            with mock.patch.object(ST, "held_by_symbol",
                                   lambda files, **kw: (dict(held), 2025)):
                code, out, err = _in_process(root, "-10", "--benchmark",
                                             INDEX, *argv,
                                             fetchers=[weekend])
            self.assertEqual(code, 0, err)
            runs.append(out)
        rows = _rows(runs[0])
        self.assertEqual([rows[s][1] for s in ("ZZQA.TO", "ZZQU.US", "ZZQK")],
                         ["100.00", "67.50", "33.75"])
        self.assertNotIn("*", "".join(c for cells in rows.values()
                                      for c in cells))
        self.assertNotIn("PRICE marked", runs[0])
        self.assertIn("\nValued:     at the closes of 2025-12-19 (coins: "
                      "2025-12-21), in CAD\n", runs[0])
        doc = json.loads(runs[1])
        self.assertEqual((doc["expiry_from"], doc["coins_valued_on"],
                          doc["latest_close_date"]), (LAST, SUNDAY, SUNDAY))
        coin, call = _row(doc, "ZZQK"), _row(doc, CALL)
        self.assertEqual((coin["valued_on"], coin["value"], coin["in_total"]),
                         (SUNDAY, 67.5, True))
        self.assertEqual((call["valued_on"], call["option"]["years"],
                          call["in_total"]), (LAST, 1.0, True))
        self.assertIn("prices: the closes of 2025-12-19 (the newest close of a "
                      "listing or the benchmark; a coin's: of 2025-12-21, the "
                      "newest coin close, a coin trading every day), daily",
                      " ".join(runs[2].replace("\n  ", " ").split(" ")))

    def test_one_valuation_date_whatever_the_accounts(self):
        """A book holding only a series that stopped on 2025-09-19 and a
        contract on it expiring in October, alone and with the rest: the
        valuation date takes the benchmark's last close in, 2025-12-19,
        so the contract is expired, and the series marked, either way
        (backlog TJ-5427)."""
        october = "ZZQF251017C00045000.TO"
        alone = {"ZZQF.TO": _entry(10), october: _entry(1)}
        for held in (alone, dict(EDGE, **alone)):
            code, out, err = self._run("-10", "--benchmark", INDEX, held=held)
            self.assertEqual(code, 0, err)
            self.assertIn("\nValued:     at the closes of 2025-12-19, in CAD\n",
                          out)
            self.assertEqual(_rows(out)["ZZQF.TO"][1], "50.00*")
            self.assertIn("! PRICE marked *: last close older than 2025-12-19 "
                          "for 1 symbol (ZZQF.TO) — tjs stress --details",
                          _acts(out))
            self.assertIn("! expired but in the books, not in the TOTAL: "
                          "500.00 intrinsic", "\n".join(_acts(out)))
            code, out, err = self._run("-10", "--benchmark", INDEX, "--json",
                                       held=held)
            self.assertEqual(code, 0, err)
            doc = json.loads(out)
            row = _row(doc, october)
            self.assertEqual((doc["expiry_from"], row["left_out"],
                              row["value"]), (LAST, "expired", 500.0))

    def test_futures_are_named(self):
        held = dict(EDGE, **{"F:ZZQZ6": _entry(1),
                             "F:QZ260620C00010000.US": _entry(1)})
        code, out, err = self._run("-10", "--benchmark", INDEX, held=held)
        self.assertEqual(code, 0, err)
        self.assertIn("! futures not modelled: 2 contracts, not in the TOTAL "
                      "(F:QZ260620C00010000.US, F:ZZQZ6)", _acts(out))
        self.assertNotIn("F:ZZQZ6", _rows(out))

    def test_no_holdings(self):
        """No position: the text says so, --json is the full document,
        --beta is still checked."""
        code, out, err = self._run("-10", "--json", held={})
        self.assertEqual(code, 0, err)
        doc = json.loads(out)
        self.assertEqual(sorted(doc), sorted(JSON_KEYS))
        self.assertEqual((doc["rows"], doc["history"], doc["total_value"],
                          doc["long_value"], doc["left_out_value"]),
                         ([], [], 0.0, 0.0, 0.0))
        self.assertEqual(doc["totals"], [{"move_pct": -10.0, "change": 0.0,
                                          "change_pct": None}])
        self.assertEqual(doc["left_out"]["futures"], [])
        self.assertEqual(doc["model"]["rate_from"], "default")
        code, out, err = self._run("-10", "--beta", "NOPE.TO=1", "--json",
                                   held={})
        self.assertEqual(code, 2, out)
        self.assertIn("no holding of these accounts is it", err)
        code, out, err = self._run("-10", held={"F:ZZQZ6": _entry(1)})
        self.assertEqual(code, 0, err)
        self.assertIn("No open positions to move.", out)
        self.assertIn("! futures not modelled: 1 contract, not in the TOTAL "
                      "(F:ZZQZ6)", _acts(out))
        self.assertTrue(out.startswith("STRESS — -10%: no holdings\n"), out)


class TestTheHistory(unittest.TestCase):
    def test_offline_without_the_cache(self):
        r = _stress("-10", "--benchmark", INDEX, root=_copy(cache=False))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("TAXJSON_OFFLINE is set and work/.history_cache.json "
                      "holds no daily history", r.stderr)

    def test_an_injected_tier_writes_the_cache_and_nothing_else(self):
        root = _copy(cache=False)
        before = {p.relative_to(root): p.read_bytes()
                  for p in root.rglob("*") if p.is_file()}
        answers = {s: (rows, "CAD") for s, rows in _series().items()}
        asked = []
        code, out, err = _in_process(root, "-10", "--benchmark", INDEX,
                                     "--details",
                                     fetchers=[_tier(answers, asked)])
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(asked[0]), sorted(answers))
        self.assertIn(f"from Yahoo Finance ({FETCHED})", out)
        doc = _json("-10", "--benchmark", INDEX)
        self.assertEqual(_rows(out)["TOTAL"][:2],
                         [f"{doc['total_value']:,.2f}",
                          f"{doc['totals'][0]['change']:,.2f}"])
        after = {p.relative_to(root): p.read_bytes()
                 for p in root.rglob("*") if p.is_file()}
        # The history cache (and its lock file) only.
        self.assertEqual(sorted({(str(p.parent), p.name.split(".json")[0])
                                 for p in set(after) - set(before)}),
                         [("work", CACHE.split(".json")[0])])
        self.assertEqual([k for k in before if before[k] != after[k]], [])


class TestTheBenchmark(unittest.TestCase):
    def test_refused_without_one(self):
        r = _stress("-10")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('no benchmark (`benchmark = "SYMBOL"` under '
                      '[settings], or --benchmark)', r.stderr)
        self.assertEqual(r.stdout, "")

    def test_every_beta_given_needs_none(self):
        r = _stress("-10", "--beta", "ZZQA.TO=1", "--beta", "ZZQB.TO=2",
                    "--beta", "ZZQC.TO=1", "--beta", "ZZQD.TO=1.5")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Benchmark:  none: every beta given by --beta", r.stdout)
        self.assertEqual(_rows(r.stdout)["ZZQA.TO"][3], "1.00#")
        self.assertIn("BETA given by --beta, marked # (no benchmark); MOVE% = "
                      "BETA x -10%.", r.stdout)

    def test_the_settings_key(self):
        root = _copy()
        toml = root / "taxjson.toml"
        toml.write_text(toml.read_text().replace(
            "source_currencies = []\n",
            f'source_currencies = []\nbenchmark = "{INDEX}"\n'))
        r = _stress("-10", root=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"Benchmark:  {INDEX} ([settings] benchmark)",
                      r.stdout)
        self.assertEqual(_rows(r.stdout)["TOTAL"],
                         _rows(_stress("-10", "--benchmark", INDEX).stdout)[
                             "TOTAL"])


if __name__ == "__main__":
    unittest.main()
