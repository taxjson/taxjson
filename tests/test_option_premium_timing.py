"""ITA s.49(1) option premium timing (Canada), s.40(3) deemed gains, and
the s.54 short-sale correction — 2026-09 design (docs/design). Library
default stays `close`; the pipeline passes `[settings]`."""
import io
import json
import random
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from taxjson.lib.core import TaxTransaction, get_tax_rules

REPO_ROOT = Path(__file__).resolve().parent.parent
OPT = "Q260116C00050000.TO"
PUT = "Q260116P00050000.TO"


def T(**kw):
    return TaxTransaction(**{"action": "BUYSELL", "currency": "CAD",
                             "account": "A0", **kw})


def run(book, **kw):
    with redirect_stderr(io.StringIO()):
        r = get_tax_rules("canada").compute_gains(book, **kw)
    return [(g["date"], round(float(g["gain"]), 2)) for g in r["transactions"] if "gain" in g], r


GRANT = dict(option_premium_timing="grant", option_grant_since=2025)


class TestGrantTiming(unittest.TestCase):
    def test_straddle_buy_back_splits_years_close_mode_nets(self):
        book = [T(date="2025-12-15", date_settle="2025-12-16", symbol=OPT, quantity=-1, price=4, net_amount=399.0),
                T(date="2026-01-10", date_settle="2026-01-12", symbol=OPT, quantity=1, price=1, net_amount=101.0)]
        recs, r = run(book, **GRANT)
        self.assertEqual(recs, [("2025-12-15", 399.0), ("2026-01-10", -101.0)])
        g = [x for x in r["transactions"] if "gain" in x]
        self.assertEqual((g[0]["cost"], g[0]["proceeds"], g[0]["direction"]), (-399.0, 0.0, "SHORT"))
        self.assertEqual((g[1]["cost"], g[1]["proceeds"]), (0.0, -101.0))
        self.assertTrue(all(abs(x["proceeds"] - x["cost"] - x["gain"]) < 1e-6 for x in g))
        self.assertEqual(run(book)[0], [("2026-01-10", 298.0)])          # library default: close

    def test_expiry_adds_nothing_and_same_year_totals_match(self):
        book = [T(date="2025-12-15", date_settle="2025-12-16", symbol=OPT, quantity=-1, price=4, net_amount=399.0),
                T(date="2026-01-16", date_settle="2026-01-16", symbol=OPT, quantity=1, price=0, net_amount=0.0)]
        self.assertEqual(run(book, **GRANT)[0], [("2025-12-15", 399.0)])
        same = [T(date="2025-03-01", date_settle="2025-03-03", symbol=OPT, quantity=-2, price=4, net_amount=798.0),
                T(date="2025-05-01", date_settle="2025-05-02", symbol=OPT, quantity=2, price=1, net_amount=202.0)]
        self.assertAlmostEqual(sum(g for _, g in run(same, **GRANT)[0]), sum(g for _, g in run(same)[0]))

    def test_assignment_folds_and_emits_no_grant_record(self):
        book = [T(date="2025-12-15", date_settle="2025-12-16", symbol=PUT, quantity=-1, price=3, net_amount=299.0),
                TaxTransaction(action="ASSIGN", date="2026-01-16", date_settle="2026-01-16", symbol=PUT, quantity=1, price=0, net_amount=0.0, currency="CAD", account="A0"),
                TaxTransaction(action="ASSIGN", date="2026-01-16", date_settle="2026-01-16", symbol="Q.TO", quantity=100, price=50, net_amount=5000.0, currency="CAD", account="A0"),
                T(date="2026-03-01", date_settle="2026-03-02", symbol="Q.TO", quantity=-100, price=55, net_amount=5500.0)]
        self.assertEqual(run(book, **GRANT)[0], [("2026-03-01", 799.0)])   # 5500 - (5000 - 299)

    def test_partial_assignment_recognises_only_the_unassigned_unit(self):
        book = [T(date="2025-12-15", date_settle="2025-12-16", symbol=PUT, quantity=-2, price=3, net_amount=598.0),
                TaxTransaction(action="ASSIGN", date="2026-01-16", date_settle="2026-01-16", symbol=PUT, quantity=1, price=0, net_amount=0.0, currency="CAD", account="A0"),
                TaxTransaction(action="ASSIGN", date="2026-01-16", date_settle="2026-01-16", symbol="Q.TO", quantity=100, price=50, net_amount=5000.0, currency="CAD", account="A0"),
                T(date="2026-01-10", date_settle="2026-01-12", symbol=PUT, quantity=1, price=1, net_amount=101.0),
                T(date="2026-03-01", date_settle="2026-03-02", symbol="Q.TO", quantity=-100, price=55, net_amount=5500.0)]
        self.assertEqual(run(book, **GRANT)[0], [("2025-12-15", 299.0), ("2026-01-10", -101.0), ("2026-03-01", 799.0)])

    def test_since_year_keeps_older_contracts_on_close_timing(self):
        book = [T(date="2024-12-15", date_settle="2024-12-16", symbol=OPT, quantity=-1, price=4, net_amount=399.0),
                T(date="2025-01-10", date_settle="2025-01-13", symbol=OPT, quantity=1, price=1, net_amount=101.0),
                T(date="2025-12-15", date_settle="2025-12-16", symbol=OPT, quantity=-1, price=4, net_amount=399.0),
                T(date="2026-01-10", date_settle="2026-01-12", symbol=OPT, quantity=1, price=1, net_amount=101.0)]
        self.assertEqual(run(book, **GRANT)[0], [("2025-01-10", 298.0), ("2025-12-15", 399.0), ("2026-01-10", -101.0)])
        mixed = [T(date="2024-12-15", date_settle="2024-12-16", symbol=OPT, quantity=-1, price=4, net_amount=399.0),
                 T(date="2025-12-15", date_settle="2025-12-16", symbol=OPT, quantity=-1, price=6, net_amount=599.0),
                 T(date="2026-01-10", date_settle="2026-01-12", symbol=OPT, quantity=2, price=1, net_amount=202.0)]
        self.assertEqual(run(mixed, **GRANT)[0], [("2025-12-15", 599.0), ("2026-01-10", 197.0)])

    def test_full_history_totals_invariant_under_timing(self):
        """Random written-option books: the lifetime total is identical
        under grant and close timing; only the year attribution moves."""
        rng = random.Random(7)
        for seed in range(150):
            book = []; pos = 0
            for i in range(rng.randint(2, 8)):
                d = f"{rng.choice([2024, 2025, 2026])}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
                if pos <= 0 and rng.random() < 0.6:
                    q = rng.choice([1, 2, 3]); book.append(T(date=d, date_settle=d, symbol=OPT, quantity=-q, price=3, net_amount=299.0 * q)); pos -= q
                elif pos < 0:
                    q = rng.randint(1, -pos); px = rng.choice([0.0, 1.0, 5.0])
                    book.append(T(date=d, date_settle=d, symbol=OPT, quantity=q, price=px, net_amount=px * 100 * q + (1.0 if px else 0.0))); pos += q
            if pos < 0:
                book.append(T(date="2027-01-15", date_settle="2027-01-15", symbol=OPT, quantity=-pos, price=0, net_amount=0.0))
            book.sort(key=lambda t: t.date)
            a = sum(g for _, g in run(book, **GRANT)[0]); b = sum(g for _, g in run(book)[0])
            self.assertAlmostEqual(a, b, places=4, msg=f"seed {seed}")

    def test_full_history_totals_invariant_with_assignments_and_since(self):
        """Lifetime invariance with stock-settled ASSIGNs, cross-zero
        sells and every `since` between (and around) the write years
        (2026-09 engine audit: an ASSIGN consuming a pre-since lot or a
        cross-zero leftover double-counted the premium). A `since`
        after every write must also reproduce close timing YEAR BY
        YEAR — the lot carve-out may not move close-timing premiums."""
        def by_year(r):
            out = {}
            for g in r["transactions"]:
                if "gain" in g:
                    y = (g.get("date_settle") or g["date"])[:4]
                    out[y] = out.get(y, 0.0) + float(g["gain"])
            return out
        rng = random.Random(49)
        for seed in range(250):
            book = []; pos = 0; shares = 0; seq = 0
            days = sorted({(rng.choice([2024, 2025, 2026]), rng.randint(1, 12), rng.randint(1, 28)) for _ in range(rng.randint(3, 9))})
            for (y, m, dd) in days:
                d = f"{y}-{m:02d}-{dd:02d}"; seq += 1; tm = f"10:{seq:02d}:00"
                u = rng.random()
                if pos >= 0 and u < 0.15:                    # long leg (sets up a cross-zero sell)
                    q = rng.choice([1, 2]); book.append(T(date=d, time=tm, date_settle=d, symbol=PUT, quantity=q, price=1, net_amount=101.0 * q)); pos += q
                elif pos >= 0 or u < 0.55:                  # write (crosses zero when long)
                    q = rng.choice([1, 2, 3]) + max(pos, 0)
                    px = rng.choice([2.0, 3.0, 4.5])
                    book.append(T(date=d, time=tm, date_settle=d, symbol=PUT, quantity=-q, price=px, net_amount=px * 100 * q - 1.0)); pos -= q
                elif u < 0.8:                               # stock-settled assignment
                    q = rng.randint(1, -pos)
                    book.append(TaxTransaction(action="ASSIGN", date=d, time="16:00:00", date_settle=d, symbol=PUT, quantity=q, price=0, net_amount=0.0, currency="CAD", account="A0"))
                    book.append(TaxTransaction(action="ASSIGN", date=d, time="16:00:00", date_settle=d, symbol="Q.TO", quantity=100 * q, price=50, net_amount=5000.0 * q, currency="CAD", account="A0"))
                    pos += q; shares += 100 * q
                else:                                       # buy-back / expiry
                    q = rng.randint(1, -pos); px = rng.choice([0.0, 1.0, 5.0])
                    book.append(T(date=d, time=tm, date_settle=d, symbol=PUT, quantity=q, price=px, net_amount=px * 100 * q + (1.0 if px else 0.0))); pos += q
            if pos < 0:
                book.append(T(date="2027-01-15", date_settle="2027-01-15", symbol=PUT, quantity=-pos, price=0, net_amount=0.0))
            elif pos > 0:
                book.append(T(date="2027-01-15", date_settle="2027-01-15", symbol=PUT, quantity=-pos, price=0.5, net_amount=50.0 * pos))
            if shares:
                book.append(T(date="2027-03-01", date_settle="2027-03-02", symbol="Q.TO", quantity=-shares, price=52, net_amount=5200.0 * shares / 100))
            _, close_r = run(book)
            cy = by_year(close_r); b = sum(cy.values())
            for since in (None, 2024, 2025, 2026, 2027):
                _, r = run(book, option_premium_timing="grant", option_grant_since=since)
                self.assertAlmostEqual(sum(by_year(r).values()), b, places=3, msg=f"seed {seed} since {since}")
            _, late = run(book, option_premium_timing="grant", option_grant_since=2030)
            ly = by_year(late)
            self.assertEqual(sorted(ly), sorted(cy), msg=f"seed {seed}")
            for y in cy:
                self.assertAlmostEqual(ly[y], cy[y], places=3, msg=f"seed {seed} {y}")

    def test_assign_consuming_pre_since_lot_folds_it_once(self):
        """Audit repro r01: a 2024 write (close timing under since=2025)
        is assigned FIFO; the 2025 write is bought back. The ASSIGN
        folds the 2024 premium (300) into the shares; the 2025 grant
        (200) stands and its buy-back is the loss of the 50 paid.
        Before: 650 in total under grant timing against 450 closed."""
        P = "ZZZ250117P00010000.US"
        book = [T(date="2024-11-01", date_settle="2024-11-01", symbol=P, quantity=-1, net_amount=300.0, currency="USD"),
                T(date="2025-01-10", date_settle="2025-01-10", symbol=P, quantity=-1, net_amount=200.0, currency="USD"),
                TaxTransaction(action="ASSIGN", date="2025-01-15", time="16:00:00", date_settle="2025-01-15", symbol=P, quantity=1, net_amount=0.0, currency="USD", account="A0"),
                TaxTransaction(action="ASSIGN", date="2025-01-15", time="16:00:00", date_settle="2025-01-15", symbol="ZZZ.US", quantity=100, net_amount=1000.0, currency="USD", account="A0"),
                T(date="2025-01-16", date_settle="2025-01-16", symbol=P, quantity=1, net_amount=50.0, currency="USD"),
                T(date="2025-03-01", date_settle="2025-03-01", symbol="ZZZ.US", quantity=-100, net_amount=1000.0, currency="USD")]
        self.assertEqual(sum(g for _, g in run(book)[0]), 450.0)
        recs = run(book, option_premium_timing="grant", option_grant_since=2025)[0]
        self.assertEqual(recs, [("2025-01-10", 200.0), ("2025-01-16", -50.0), ("2025-03-01", 300.0)])

    def test_assign_consuming_cross_zero_leftover_folds_it_once(self):
        """Audit repro r02: a sell that crosses zero opens a short
        leftover; the ASSIGN consumes that leftover FIFO. Its grant
        record is retracted (s.49(4)) and the premium folds once.
        Before: 550 under grant timing against 400 closed."""
        P = "ZZZ250117P00010000.US"
        book = [T(date="2025-01-02", date_settle="2025-01-02", symbol=P, quantity=1, net_amount=100.0, currency="USD"),
                T(date="2025-01-03", date_settle="2025-01-03", symbol=P, quantity=-2, net_amount=400.0, currency="USD"),
                T(date="2025-01-06", date_settle="2025-01-06", symbol=P, quantity=-1, net_amount=150.0, currency="USD"),
                TaxTransaction(action="ASSIGN", date="2025-01-15", time="16:00:00", date_settle="2025-01-15", symbol=P, quantity=1, net_amount=0.0, currency="USD", account="A0"),
                TaxTransaction(action="ASSIGN", date="2025-01-15", time="16:00:00", date_settle="2025-01-15", symbol="ZZZ.US", quantity=100, net_amount=1000.0, currency="USD", account="A0"),
                T(date="2025-01-16", date_settle="2025-01-16", symbol=P, quantity=1, net_amount=50.0, currency="USD"),
                T(date="2025-03-01", date_settle="2025-03-01", symbol="ZZZ.US", quantity=-100, net_amount=1000.0, currency="USD")]
        self.assertEqual(sum(g for _, g in run(book)[0]), 400.0)
        recs = run(book, option_premium_timing="grant")[0]
        self.assertAlmostEqual(sum(g for _, g in recs), 400.0, places=6)
        self.assertEqual(recs, [("2025-01-03", 100.0), ("2025-01-06", 150.0), ("2025-01-16", -50.0), ("2025-03-01", 200.0)])

    def test_close_timing_lot_is_consumed_first_by_write_date(self):
        """Audit repro r03 / design doc: a close consumes lots FIFO by
        write date, so the 2025 buy-back closes the 2024 close-timing
        write (300 - 50 = 250 in 2025) and the 2026 expiry closes the
        already-recognised 2025 write (nothing). Before: grant units
        went first — 2025 = 200, 2026 = 250."""
        P = "ZZZ260116C00050000.US"
        book = [T(date="2024-11-01", date_settle="2024-11-01", symbol=P, quantity=-1, net_amount=300.0, currency="USD"),
                T(date="2025-02-10", date_settle="2025-02-10", symbol=P, quantity=-1, net_amount=200.0, currency="USD"),
                T(date="2025-03-16", date_settle="2025-03-16", symbol=P, quantity=1, net_amount=50.0, currency="USD"),
                T(date="2026-01-16", date_settle="2026-01-16", symbol=P, quantity=1, net_amount=0.0, currency="USD")]
        recs = run(book, option_premium_timing="grant", option_grant_since=2025)[0]
        self.assertEqual(recs, [("2025-02-10", 200.0), ("2025-03-16", 250.0)])

    def test_buy_back_of_a_grant_lot_is_the_amount_paid_whatever_other_lots_cost(self):
        """Two grant lots at different premiums: the buy-back of the
        first is a loss of what was paid, not of the pool average."""
        book = [T(date="2025-03-01", date_settle="2025-03-01", symbol=OPT, quantity=-1, net_amount=300.0),
                T(date="2025-04-01", date_settle="2025-04-01", symbol=OPT, quantity=-1, net_amount=100.0),
                T(date="2026-01-05", date_settle="2026-01-05", symbol=OPT, quantity=1, net_amount=50.0),
                T(date="2026-01-16", date_settle="2026-01-16", symbol=OPT, quantity=1, net_amount=0.0)]
        recs = run(book, **GRANT)[0]
        self.assertEqual(recs, [("2025-03-01", 300.0), ("2025-04-01", 100.0), ("2026-01-05", -50.0)])


class TestBuybackLossSuperficialSwitch(unittest.TestCase):
    """A written option bought back within a minute while a registered
    account holds the same series: default — the buy-back loss stands
    (a closing purchase is not a disposition s.54 reaches); opt-in — it
    is denied, permanently, by the registered acquisition."""
    SYM = "TLT280121C00075000.US"

    def _run(self, strict):
        book = [T(date="2025-12-12", time="12:05:14", date_settle="2025-12-15", symbol=self.SYM, quantity=-10, price=13.38, net_amount=13373.33),
                T(date="2025-12-12", time="12:05:42", date_settle="2025-12-15", symbol=self.SYM, quantity=10, price=13.445, net_amount=13449.89)]
        shel = [T(date="2025-12-08", date_settle="2025-12-09", symbol=self.SYM, quantity=5, price=13.0, net_amount=6500.0, account="lira")]
        with redirect_stderr(io.StringIO()):
            r = get_tax_rules("canada").compute_gains(book, sheltered_transactions=shel, option_premium_timing="grant",
                                                       option_grant_since=2025, option_buyback_loss_superficial=strict)
        return [(g["date"], round(float(g["raw_gain"]), 2), g.get("disallowed_amount"), g.get("permanently_disallowed")) for g in r["transactions"] if "gain" in g]

    def test_default_allows_the_buyback_loss(self):
        self.assertEqual(self._run(False), [("2025-12-12", 13373.33, 0.0, 0.0), ("2025-12-12", -13449.89, 0.0, 0.0)])

    def test_strict_reading_denies_it_permanently(self):
        recs = self._run(True)
        # 10 bought back, the LIRA holds 5 at day 30: 5 units denied, permanently.
        self.assertAlmostEqual(recs[1][2], 13449.89 / 2, places=2)
        self.assertAlmostEqual(recs[1][3], 13449.89 / 2, places=2)


class TestDeemedGainNegativeAcb(unittest.TestCase):
    def test_roc_beyond_acb_is_a_gain_in_the_roc_year(self):
        book = [T(date="2025-01-06", date_settle="2025-01-06", symbol="X.TO", quantity=100, price=10.0, net_amount=1000.0),
                TaxTransaction(action="ADJUST", date="2025-06-30", date_settle="2025-06-30", symbol="X.TO", currency="CAD", account="A0", quantity=0, net_amount=-1500.0, description="ROC"),
                T(date="2026-03-02", date_settle="2026-03-02", symbol="X.TO", quantity=-100, price=10.0, net_amount=1000.0)]
        recs, r = run(book)
        self.assertEqual(recs, [("2025-06-30", 500.0), ("2026-03-02", 1000.0)])
        deemed = [g for g in r["transactions"] if "gain" in g and g["date"] == "2025-06-30"][0]
        self.assertEqual(deemed["qty"], 0.0)
        self.assertIn("s.40(3)", deemed.get("note", ""))


class TestShortSaleWashCorrection(unittest.TestCase):
    def _book(self, third_qty):
        return [T(date="2026-01-06", date_settle="2026-01-06", symbol="X.TO", quantity=-100, price=100.0, net_amount=10000.0),
                T(date="2026-01-16", date_settle="2026-01-16", symbol="X.TO", quantity=100, price=110.0, net_amount=11000.0),
                T(date="2026-01-21", date_settle="2026-01-21", symbol="X.TO", quantity=third_qty, price=105.0, net_amount=10500.0)]

    def test_re_short_is_not_an_acquisition(self):
        _, r = run(self._book(-100))
        self.assertEqual([g.get("disallowed_amount") for g in r["transactions"] if "gain" in g and g.get("qty")], [0.0])

    def test_long_rebuy_after_cover_loss_is_denied(self):
        _, r = run(self._book(100))
        self.assertEqual([g.get("disallowed_amount") for g in r["transactions"] if "gain" in g and g.get("qty")], [1000.0])


class TestCliThreading(unittest.TestCase):
    def test_gains_cli_flags_reach_the_engine_and_the_summary(self):
        rows = [{"action": "BUYSELL", "date": "2025-12-15", "time": "09:30:00", "date_settle": "2025-12-16", "symbol": OPT, "quantity": -1, "price": 4.0, "net_amount": 399.0, "currency": "CAD", "account": "A0"},
                {"action": "BUYSELL", "date": "2026-01-10", "time": "09:30:00", "date_settle": "2026-01-12", "symbol": OPT, "quantity": 1, "price": 1.0, "net_amount": 101.0, "currency": "CAD", "account": "A0"}]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base.json"; base.write_text(json.dumps({"transactions": rows}))
            def gains(*flags):
                r = subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_gains", "--country", "canada", "--year", "2025", "--taxable", *flags, str(base)],
                                   cwd=REPO_ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL)
                self.assertEqual(r.returncode, 0, r.stderr); return json.loads(r.stdout)
            g = gains("--option-premium-timing", "grant", "--option-grant-since", "2025")
            self.assertEqual(g["summary"]["option_premium_timing"], "grant")
            self.assertAlmostEqual(g["summary"]["total_gain"], 399.0, places=2)
            c = gains("--option-premium-timing", "close")
            self.assertAlmostEqual(c["summary"]["total_gain"], 0.0, places=2)   # nets in 2026


if __name__ == "__main__":
    unittest.main()
