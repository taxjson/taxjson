"""An RBC Direct Investing options account's figures, computed by hand from
the rows of the rbc-options golden (tests/golden/rbc-options/project/
README.md says what each group of rows tests; backlog TJ-5365).

The book is copied and run (`tjs run --no-input`), and run again as tax
year 2024; its figures are read from the work/ gains files, `tjs sum
--json`, `tjs form-export --json`, `tjs option-yield 2025 --json` and `tjs
sanity --json`, and compared with the hand computation, not with the
recorded expected/ (which pins what the code does, right or wrong):
- each 2025 disposition's gain, and each closed option round trip's
  (grant timing: a write is a gain in its year, a buy-back a loss, an
  expiry nothing, a bought option's cost a loss on its expiry; USD at the
  synthetic 1.35);
- the calls assigned (s.49(3)), one of them written in December 2024
  (s.49(4): its premium leaves 2024); the put assigned (its premium lowers
  the shares' ACB); a buy-back settling across the year end;
- a superficial share loss, denied and added to the new shares' ACB;
- the 2025 Schedule 3 lines and total, the income, FX on cash;
- 2024's figures and the TFSA's;
- the same-day write and buy-back of one series listed OPEN above CLOSE
  (TJ-5348): read in the broker's open/close order, said once in the
  .sum; the two rolls (two series each) need no reordering;
- nothing open past its expiry; no ATTENTION; the overlapping summer
  download skipped by the parser; the Holdings Exports' quantities agree;
- the checklist's inputs-frozen done (the exports reach past January);
  the fees report's MEDIAN, one row's fee.

No money amount of the book sits on a half cent: every CAD amount is a
quarter and every USD one a multiple of 5.00 (a quarter in CAD at 1.35), so
each Schedule 3 row foots on its exact gain, the day trade's too (the
half-cent case of backlog TJ-5056 is pinned by its own repro). Two averages
the margin .sum prints are exact half cents (project/README.md); the golden
pins them."""
import contextlib
import json
import re
import shutil
import tempfile
import unittest
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import _golden

BOOK = "rbc-options"

# Each 2025 disposition of the margin account, by hand: (symbol, settlement
# date, gain in CAD). Commissions (made up): a CAD option order 6.25 + 1.50
# a contract on its first fill, 1.50 a contract on a later one (a 1 + 1
# fill: 7.75, then 1.50); a USD option order 5.00; a share order 8.00; an
# assignment's share leg 55.00.
DISPOSITIONS = [
    # The Jan 13 calls written 2024-12-23 (30.00 - 9.25 = 20.75, a 2024
    # gain), bought back 2024-12-31 at 0.04 (8.00 + 9.25): settled 2025-01-02.
    ("ZZQV250117C00013000.TO", "2025-01-02", "-17.25"),
    # The Jan 12.50 call written 2024-12-23 (31.00 - 7.75 = 23.25), assigned
    # 2025-01-17: 100 ZZQV at 12.50 (1250.00 - 55.00) + 23.25, less 100 x 12.12.
    ("ZZQV.TO", "2025-01-21", "6.25"),
    # ZZQV Feb 13 calls: written 1 + 1 at 0.25 (25.00 - 7.75, 25.00 - 1.50),
    # bought back 1 + 1 at 0.54 in the first roll (54.00 + 7.75, 54.00 + 1.50).
    ("ZZQV250221C00013000.TO", "2025-01-21", "17.25"),
    ("ZZQV250221C00013000.TO", "2025-01-21", "23.50"),
    ("ZZQV250221C00013000.TO", "2025-02-19", "-61.75"),
    ("ZZQV250221C00013000.TO", "2025-02-19", "-55.50"),
    # ZZQV Mar 13.50 calls: written at 0.36 in the first roll (72.00 - 9.25),
    # bought back at 0.14 in the second (28.00 + 9.25).
    ("ZZQV250321C00013500.TO", "2025-02-19", "62.75"),
    ("ZZQV250321C00013500.TO", "2025-03-18", "-37.25"),
    # The May 13.50 calls assigned: 200 ZZQV at 13.50 (2700.00 - 55.00) + the
    # writes' 25.25 + 31.50, less 200 x 12.12.
    ("ZZQV.TO", "2025-05-21", "277.75"),
    # 100 ZZQV bought 06-10 at 14.03 (1411.00), sold 11-03 at 13.21 (1313.00):
    # -98.00, all of it denied (100 bought back 11-17, held at the end).
    ("ZZQV.TO", "2025-11-04", "0.00"),
    # A long call: 2 bought at 0.34 (68.00 + 9.25), sold at 0.69 (138.00 - 9.25).
    ("ZZQV251017C00014500.TO", "2025-09-16", "51.50"),
    # ZZRW 21.50 calls written monthly, each expired: (price x 100 - 5.00) x 1.35.
    ("ZZRW250718C00021500.US", "2025-06-24", "47.25"),
    ("ZZRW250815C00021500.US", "2025-07-22", "33.75"),
    ("ZZRW250919C00021500.US", "2025-08-19", "60.75"),
    ("ZZRW251017C00021500.US", "2025-09-23", "47.25"),
    ("ZZRW251121C00021500.US", "2025-10-21", "33.75"),
    ("ZZRW251219C00021500.US", "2025-11-25", "74.25"),
    # A long put: 4 bought at 0.05 (20.00 + 5.00 USD), expired worthless:
    # the loss on the expiry date, 09-19 (its EXP row is posted 09-22).
    ("ZZRW250919P00019000.US", "2025-09-19", "-33.75"),
    # The LEAPS call open at the end: 1.10 (110.00 - 5.00) x 1.35.
    ("ZZRW280121C00025000.US", "2025-12-23", "141.75"),
    # The day trade: 2 written at 0.60 (120.00 - 5.00 USD), 2 bought back at
    # 0.40 (80.00 + 5.00 USD).
    ("ZZRW260116P00020000.US", "2025-12-30", "155.25"),
    ("ZZRW260116P00020000.US", "2025-12-30", "-114.75"),
]

# Each closed 2025 option round trip, by hand (an expired write is its write
# alone; an assigned write's premium went to the shares).
ROUND_TRIPS = {
    "ZZQV250117C00013000.TO": "-17.25",
    "ZZQV250221C00013000.TO": "-76.50",
    "ZZQV250321C00013500.TO": "25.50",
    "ZZQV251017C00014500.TO": "51.50",
    "ZZRW250919P00019000.US": "-33.75",
    "ZZRW260116P00020000.US": "40.50",
}

OPEN_CLOSE_NOTE = ("note: rbc_2025.csv: rows of ZZRW260116P00020000.US on 2025-12-29 put in the "
                   "broker's open/close order (RBC writes no time): the OPEN CONTRACT sale of 2 "
                   "before the CLOSE CONTRACT purchase of 2 (the rows end 2025-12-29; taken as "
                   "holding everything to its as-of date 2026-02-03: if the export was asked for "
                   "a shorter range, add the rest)")

OVERLAP_NOTE = ("note: rbc_2025_partial.csv: 7 row(s) dated 2025-06-20..2025-08-06 are already "
                "in rbc_2025.csv (an overlapping download of the same RBC account 99***) — "
                "skipped, not booked twice")


def _d(x):
    return Decimal(str(x)).quantize(Decimal("0.0001"))


def _json(argv, root, env):
    r = _golden._tjs(argv, root, env)
    assert r.returncode == 0, (argv, r.stdout[-800:], r.stderr[-800:])
    return json.loads(r.stdout)


class TestTheRbcOptionsAccount(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._stack = contextlib.ExitStack()
        m = _golden.meta(BOOK)
        root, env, cls.ran, _b = cls._stack.enter_context(_golden.project_run(BOOK, m))
        cls.root = root
        work = root / "work"
        cls.gains = json.loads((work / "margin_gains_wash.json").read_text(encoding="utf-8"))
        cls.tfsa = json.loads((work / "tfsa_gains.json").read_text(encoding="utf-8"))
        cls.reports = {p.name: p.read_text(encoding="utf-8") for p in (root / "reports").glob("*.sum")}
        cls.sum = _json(["sum", "--json"], root, env)
        cls.form = _json(["form-export", "--json"], root, env)
        cls.yield_2025 = _json(["option-yield", "2025", "--json"], root, env)
        cls.sanity = _json(["sanity", "--json"], root, env)
        # The same inputs as tax year 2024: the history's own figures.
        tmp = Path(cls._stack.enter_context(tempfile.TemporaryDirectory(prefix="golden-2024-")))
        y24 = tmp / "p"
        shutil.copytree(_golden.GOLDEN / BOOK / "project", y24,
                        ignore=shutil.ignore_patterns(*_golden._GENERATED))
        toml = y24 / "taxjson.toml"
        text = toml.read_text(encoding="utf-8")
        assert text.count("= 2025\n") == 1, text
        toml.write_text(text.replace("= 2025\n", "= 2024\n"), encoding="utf-8")
        _golden._make_private(y24)
        r = _golden._tjs(["run", "--no-input"], y24, env)
        assert r.returncode == 0, (r.stdout[-800:], r.stderr[-800:])
        cls.sum_2024 = _json(["sum", "--json"], y24, env)

    @classmethod
    def tearDownClass(cls):
        cls._stack.close()

    def _rows(self):
        return [t for t in self.gains["transactions"]
                if t["account"] == "margin" and t.get("action") != "DIVIDEND"]

    def _row(self, symbol, settled):
        return next(t for t in self._rows() if t["symbol"] == symbol and t["date_settle"] == settled)

    def test_each_disposition_is_the_hand_figure(self):
        got = sorted((t["symbol"], t["date_settle"], _d(t["gain"])) for t in self._rows())
        self.assertEqual(got, sorted((s, d, _d(g)) for s, d, g in DISPOSITIONS))

    def test_each_closed_round_trip_is_the_hand_figure(self):
        by = defaultdict(Decimal)
        for t in self._rows():
            by[t["symbol"]] += _d(t["gain"])
        for symbol, gain in ROUND_TRIPS.items():
            with self.subTest(symbol=symbol):
                self.assertEqual(by[symbol], _d(gain))

    def test_the_assigned_calls_add_their_premium_to_the_shares_proceeds(self):
        # The write's net premium joins the work file's proceeds, which are
        # net of the outlays: the share leg's 55.00 (its `commission`) and the
        # write's (its `write_outlay`; Schedule 3 adds it: test_the_schedule_3_lines).
        jan, may = self._row("ZZQV.TO", "2025-01-21"), self._row("ZZQV.TO", "2025-05-21")
        self.assertEqual([(t["qty"], _d(t["proceeds"]), _d(t["cost"]), _d(t["gain"])) for t in (jan, may)],
                         [(100.0, _d("1218.25"), _d("1212.00"), _d("6.25")),
                          (200.0, _d("2701.75"), _d("2424.00"), _d("277.75"))])
        self.assertFalse({"ZZQV250117C00012500.TO", "ZZQV250516C00013500.TO"}
                         & {t["symbol"] for t in self._rows()},
                         "an assigned write is no disposition of its own")

    def test_the_december_write_assigned_in_january_leaves_2024(self):
        """s.49(4): the 12.50 call written 2024-12-23 and assigned 2025-01-17
        is no 2024 disposition. 2024: the seven two-contract writes' net
        premiums, 26.75 + 34.75 + 22.75 + 32.75 + 30.75 + 18.75 + 20.75; the
        fees, 8.00 (the shares) + 7 x 9.25 + 7.75 (the 12.50 call)."""
        f = self.sum_2024["filing"]
        self.assertEqual([(ln["gain_code"], ln["proceeds"], ln["acb"], ln["outlays"], ln["gain"],
                           ln["dispositions"]) for ln in f["lines"]],
                         [("15300", 252.0, 0.0, 64.75, 187.25, 7)])
        self.assertEqual(f["totals"]["gain"], 187.25)
        self.assertEqual([(a["account"], a["stock"], a["option"], a["fees"]) for a in self.sum_2024["accounts"]],
                         [("margin", 0.0, 187.25, 80.5), ("tfsa", 0.0, 0.0, 0.0)])

    def test_the_assigned_put_lowers_the_shares_acb(self):
        held = {h["symbol"]: h for h in self.gains["inventory"]}
        # 2000.00 + 55.00 - (40.00 - 5.00) = 2020.00 USD, x 1.35.
        self.assertEqual((held["ZZRW.US"]["qty"], _d(held["ZZRW.US"]["total_cost"])),
                         (100.0, _d("2727.00")))
        self.assertFalse(any(t["symbol"] == "ZZRW250620P00020000.US" for t in self._rows()))

    def test_the_superficial_loss_is_denied_and_moves_to_the_new_shares(self):
        sale = self._row("ZZQV.TO", "2025-11-04")
        self.assertEqual((_d(sale["proceeds"]), _d(sale["cost"]), _d(sale["disallowed_amount"])),
                         (_d("1313.00"), _d("1411.00"), _d("98.00")))
        self.assertEqual([t["symbol"] for t in self._rows() if t["disallowed_amount"]], ["ZZQV.TO"],
                         "a buy-back loss is not a superficial loss")
        held = {h["symbol"]: h for h in self.gains["inventory"]}
        # 100 bought back 11-17 at 13.07 (1315.00) + the denied 98.00.
        self.assertEqual((held["ZZQV.TO"]["qty"], _d(held["ZZQV.TO"]["total_cost"])),
                         (100.0, _d("1413.00")))

    def test_the_schedule_3_lines(self):
        filing = self.sum["filing"]
        lines = {ln["gain_code"]: ln for ln in filing["lines"]}
        self.assertEqual(set(lines), {"13200", "15300"})
        shares = lines["13200"]
        # The assigned calls' premiums gross, their writes' commissions
        # outlays (CA-OPT-06, the owner's ruling, backlog TJ-5381): proceeds
        # (1250.00 + 31.00) + (2700.00 + 33.00 + 33.00) + 1321.00; outlays
        # (55.00 + 7.75) + (55.00 + 7.75 + 1.50) + 8.00; ACB 1212.00 +
        # 2424.00 + (1411.00 - 98.00 denied).
        self.assertEqual((shares["proceeds"], shares["acb"], shares["outlays"], shares["gain"],
                          shares["denied"]),
                         (5368.0, 4949.0, 135.0, 284.0, 98.0))
        other = lines["15300"]
        # Proceeds: the writes' gross premiums and the long call's sale;
        # ACB: the buy-backs and the bought options' costs; outlays: the
        # writes' and the sale's commissions.
        self.assertEqual((other["proceeds"], other["acb"], other["outlays"], other["gain"],
                          other["dispositions"]),
                         (908.0, 397.5, 81.75, 428.75, 18))
        exact = sum((_d(t["gain"]) for t in self._rows()), Decimal(0))
        self.assertEqual(exact, _d("712.75"))
        self.assertEqual((filing["totals"]["gain"], filing["engine_gain_unrounded"]), (712.75, 712.75))
        self.assertEqual([a["account"] for a in filing["accounts"]], ["margin"],
                         "the TFSA is sheltered: out of the filing totals")

    def test_the_income_and_fx_on_cash(self):
        text = self.reports["margin.sum"]
        # 35.00 USD gross, 5.00 USD withheld, x 1.35; interest 4.24 CAD.
        self.assertIsNotNone(re.search(r"^ZZRW\.US +47\.25 +0\.00 +6\.75 +40\.50$", text, re.M), text)
        self.assertIsNotNone(re.search(r"^CASH INTEREST +4\.24$", text, re.M))
        fx = self.sum["filing"]["fx_cash"]
        # Every USD flow, the conversion too, at 1.35.
        self.assertEqual((fx["ledger"], fx["status"], fx["net_gain"], fx["reportable"], fx["problems"]),
                         ("v2", "computed", 0.0, 0.0, []))

    def test_the_tfsa(self):
        self.assertEqual([(t["symbol"], t["date_settle"], _d(t["gain"])) for t in self.tfsa["transactions"]],
                         [("ZZQV250321C00013500.TO", "2025-02-04", _d("21.25"))])
        self.assertEqual([(h["symbol"], h["qty"], _d(h["total_cost"])) for h in self.tfsa["inventory"]],
                         [("ZZQV.TO", 100.0, _d("1282.00"))])
        tfsa = next(a for a in self.sum["accounts"] if a["account"] == "tfsa")
        self.assertEqual((tfsa["type"], tfsa["option"], tfsa["fees"]), ("sheltered", 21.25, 15.75))

    def test_the_day_trade_is_read_in_the_brokers_open_close_order(self):
        text = self.reports["margin.sum"]
        self.assertIn(OPEN_CLOSE_NOTE, text)
        self.assertEqual(text.count("put in the broker's open/close order"), 1,
                         "the rolls span two series each: nothing of theirs is reordered")

    def test_nothing_open_past_its_expiry(self):
        opened = self.yield_2025["open"]
        self.assertEqual([(o["symbol"], o["expired"]) for o in opened],
                         [("ZZRW280121C00025000.US", False)])
        held = {h["symbol"]: h["qty"] for h in self.gains["inventory"]}
        self.assertEqual(held, {"ZZQV.TO": 100.0, "ZZRW.US": 100.0, "ZZRW280121C00025000.US": -1.0})

    def test_no_attention_and_the_summer_download_skipped_by_the_parser(self):
        for name, text in [*self.reports.items(), ("tjs run", self.ran.stdout + self.ran.stderr)]:
            with self.subTest(report=name):
                self.assertEqual([ln for ln in text.splitlines()
                                  if "ATTENTION" in ln or "Warning:" in ln], [])
        self.assertIn(OVERLAP_NOTE, self.reports["margin.sum"])
        self.assertNotIn("--dedup removed", self.reports["margin.sum"])

    def test_the_exports_reach_past_january(self):
        # The 2025 exports were taken 2026-02-03 and rbc_2026.csv holds a
        # 2026-02-02 row: the checklist's inputs-frozen is done (an RBC
        # as-of date past Jan 31 alone does not answer it: TJ-5388).
        from datetime import date
        from taxjson.lib import checklist as cl
        from taxjson.pipeline.config import load_config

        def no_command(*_a, **_k):
            raise AssertionError("inputs-frozen runs no command")
        r = cl.d_inputs_frozen(cl.Ctx(root=self.root, cfg=load_config(self.root), year=2025,
                                      today=date(2026, 3, 1), run_sub=no_command))
        self.assertEqual((r.status, r.detail), ("done", "latest activity 2026-02-02"))

    def test_the_fees_report_median_is_one_rows_fee(self):
        # 29 fee rows by trade date, both accounts, in CAD (three orders
        # filled as two, 7.75 and 1.50): 1.50 x3, 6.75 (5.00 USD) x11, 7.75
        # x4, 8.00 x4, 9.25 x4, 55.00 x2, 74.25 (55.00 USD): the 15th is
        # 7.75, one row's fee (an even count would average two). TOTAL
        # 214.50 CAD + 110.00 USD x 1.35 = 363.00; MEAN 363.00 / 29, 12.52.
        text = (self.root / "reports" / "fees.rpt").read_text(encoding="utf-8")
        total = next(ln for ln in text.splitlines() if ln.startswith("TOTAL "))
        self.assertEqual(total.split()[2:6], ["29", "363.00", "12.52", "7.75"])

    def test_the_holdings_exports_agree(self):
        s = self.sanity
        self.assertTrue(s["clean"])
        self.assertEqual(s["discrepancies"], [])
        cost = {r["symbol"]: (r["broker_cost"], round(r["books_cost"], 2), round(r["diff"], 2))
                for r in s["cost"]["rows"]}
        # RBC's book costs: ZZQV without the denied loss, ZZRW without the
        # put's premium; the LEAPS's premium the same.
        self.assertEqual(cost, {"ZZQV.TO": (1315.0, 1413.0, 98.0), "ZZRW.US": (2055.0, 2020.0, -35.0),
                                "ZZRW280121C00025000.US": (-105.0, -105.0, 0.0)})
        zzqv = next(r for r in s["cost"]["rows"] if r["symbol"] == "ZZQV.TO")
        self.assertEqual((zzqv["reasons"], zzqv["explained"]), (["superficial-loss"], True))

    def test_the_day_trade_row_foots_on_its_exact_gain(self):
        """The day trade's Schedule 3 row: proceeds 2 x 0.60 x 100 = 120.00
        USD, ACB the buy-back's 85.00 USD, outlays the write's 5.00 USD, x
        1.35. Every cell a quarter: the cells foot on the exact gain."""
        row = next(r for r in self.form["rows"] if r["symbol"] == "ZZRW260116P00020000.US")
        self.assertEqual((row["proceeds"], row["acb"], row["outlays"], row["gain"]),
                         (162.0, 114.75, 6.75, 40.5))
        self.assertEqual(self.form["totals"]["gain_15300"], 428.75)
