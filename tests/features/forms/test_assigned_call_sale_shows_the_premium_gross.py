"""A written option's premium on Schedule 3: gross, the write's commission
an outlay, under every premium timing (tax-logic CA-OPT-06, CA-DISP-06;
backlog TJ-5381 and TJ-5460, the owner's ruling of 2026-11-27: gross, one
convention on the form). An assigned call's: the shares' proceeds hold the
premium received and the write's commission is among the sale's outlays. A
write bought back or expired: line 15199 shows the premium received and the
write's commission an outlay, the buy-back's cost the ACB, under close
timing as under grant timing. The gain is the same as it was with the
premium taken net: only the proceeds and the outlays move, by the write's
commission. Each gains row keeps its own commission and fee; the write's is
`write_outlay` (an assigned call's shares and a close-timing write's close
alike; the proceeds stay net), which lib/tax_forms `schedule3_disposition`
adds, for `tjs sum`, `tjs form-export`, `tjs gains-cra` and `tjs
reconcile-slips` (backlog TJ-5462) alike. A call assigned into a short of
the shares stays netted, as a short sale's opening commission does.

One synthetic project (made-up tickers), Canada, tax year 2025, under the
three premium timings: grant timing from 2024 (a 2025 write's premium taxed
when written; the December 2024 write's year amended, s.49(4)), grant
timing from 2025 (that 2024 write kept on close timing) and close timing
(every premium deferred to its close). A call written in 2025 and assigned
(ZZCA), one written in December 2024 and assigned in January (ZZCB), two
calls written in one row, one bought back and one assigned (ZZCC), two
writes assigned into one sale (ZZCD), a USD write (ZZCU: the premium and
its commission at the write's rate, the shares at the sale's), a write
whose commission was partly refunded (ZZCR) and one with none (ZZCZ), one
whose assignment row carries a fee of its own (ZZCE: an outlay too), and
a written put assigned (ZZCP: its premium lowers the shares' cost, net or
gross the same). On line 15199: a call written and expired (ZZCX), two
written in one row and bought back (ZZCY), two writes bought back in one
row (ZZCK: close timing's average), a write for a net debit, expired
(ZZCN), and a USD write bought back (ZZCW: the premium and its commission
at the write's rate, the buy-back at its own). A second project: a call
assigned into a short of the shares (ZZNA) and into a part-held position
(ZZNB: the short part netted), a write with a rebate (ZZRB) and one
assignment delivered in two share legs (ZZSP). A third: a 2025 closed on
books without `write_outlay`, then re-run: `tjs check-filed`. Every amount
of 100.00 or more is a quarter, so each row foots on its exact gain.
"""
import datetime
import json
import re
import unittest
from decimal import Decimal

from _planted_rates import plant
from _support.filing_mixed_currency_project import Project
from taxjson.engine.ca.pool import AcbPool
from taxjson.engine.premium_ledger import _AssignPremiumLedger
from taxjson.lib import json_input as JI
from taxjson.lib import option_symbols as OS
from taxjson.lib import tax_forms as TF
from taxjson.model.result import ResultShapeError
from taxjson.model.transaction import TaxTransaction
from tax_rules import rule

from taxjson.rules import ids as R

_HEAD = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
         'country = "canada"\nbase_currency = "CAD"\nsource_currencies = ["USD"]\n')
_ACCOUNT = '[accounts.margin]\ntype = "taxable"\n'

MARGIN_TT = """\
BUYSELL 2025-01-06 10:00:00 ZZCA.TO 100 CAD 12.00 1208.00 8.00
BUYSELL 2025-02-03 10:00:00 ZZCA250321C00012500.TO -1 CAD 0.31 23.25 7.75
ASSIGN 2025-03-21 16:00:00 ZZCA250321C00012500.TO 1 CAD 0 0
ASSIGN 2025-03-21 16:01:00 ZZCA.TO -100 CAD 12.50 1195.00 55.00
BUYSELL 2024-07-02 10:00:00 ZZCB.TO 100 CAD 12.00 1208.00 8.00
BUYSELL 2024-12-23 10:00:00 ZZCB250117C00012500.TO -1 CAD 0.31 23.25 7.75
ASSIGN 2025-01-17 16:00:00 ZZCB250117C00012500.TO 1 CAD 0 0
ASSIGN 2025-01-17 16:01:00 ZZCB.TO -100 CAD 12.50 1195.00 55.00
BUYSELL 2025-01-06 10:00:00 ZZCC.TO 100 CAD 12.00 1208.00 8.00
BUYSELL 2025-04-01 10:00:00 ZZCC250516C00012500.TO -2 CAD 0.31 52.50 9.50
BUYSELL 2025-05-12 10:00:00 ZZCC250516C00012500.TO 1 CAD 0.05 9.75 4.75
ASSIGN 2025-05-16 16:00:00 ZZCC250516C00012500.TO 1 CAD 0 0
ASSIGN 2025-05-16 16:01:00 ZZCC.TO -100 CAD 12.50 1195.00 55.00
BUYSELL 2025-01-06 10:00:00 ZZCD.TO 200 CAD 12.00 2408.00 8.00
BUYSELL 2025-06-02 10:00:00 ZZCD250718C00013000.TO -1 CAD 0.40 32.25 7.75
BUYSELL 2025-06-09 10:00:00 ZZCD250718C00013000.TO -1 CAD 0.40 38.50 1.50
ASSIGN 2025-07-18 16:00:00 ZZCD250718C00013000.TO 2 CAD 0 0
ASSIGN 2025-07-18 16:01:00 ZZCD.TO -200 CAD 13.00 2545.00 55.00
BUYSELL 2025-01-10 10:00:00 ZZCU.US 100 USD 20.00 2005.00 5.00
BUYSELL 2025-08-04 10:00:00 ZZCU250919C00021000.US -1 USD 0.40 35.00 5.00
ASSIGN 2025-09-19 16:00:00 ZZCU250919C00021000.US 1 USD 0 0
ASSIGN 2025-09-19 16:01:00 ZZCU.US -100 USD 21.00 2045.00 55.00
BUYSELL 2025-01-06 10:00:00 ZZCR.TO 100 CAD 12.00 1208.00 8.00
BUYSELL 2025-08-05 10:00:00 ZZCR250919C00012500.TO -1 CAD 0.31 25.25 5.75 refunds=2025-09-02:2.00
ASSIGN 2025-09-19 16:00:00 ZZCR250919C00012500.TO 1 CAD 0 0
ASSIGN 2025-09-19 16:01:00 ZZCR.TO -100 CAD 12.50 1195.00 55.00
BUYSELL 2025-01-06 10:00:00 ZZCZ.TO 100 CAD 12.00 1208.00 8.00
BUYSELL 2025-08-05 10:00:00 ZZCZ250919C00012500.TO -1 CAD 0.30 30.00 0
ASSIGN 2025-09-19 16:00:00 ZZCZ250919C00012500.TO 1 CAD 0 0
ASSIGN 2025-09-19 16:01:00 ZZCZ.TO -100 CAD 12.50 1195.00 55.00
BUYSELL 2025-01-06 10:00:00 ZZCE.TO 100 CAD 12.00 1208.00 8.00
BUYSELL 2025-08-05 10:00:00 ZZCE250919C00012500.TO -1 CAD 0.31 23.25 7.75
ASSIGN 2025-09-19 16:00:00 ZZCE250919C00012500.TO 1 CAD 0 5.00 5.00
ASSIGN 2025-09-19 16:01:00 ZZCE.TO -100 CAD 12.50 1195.00 55.00
BUYSELL 2025-08-05 10:00:00 ZZCP250919P00012000.TO -1 CAD 0.40 32.25 7.75
ASSIGN 2025-09-19 16:00:00 ZZCP250919P00012000.TO 1 CAD 0 0
ASSIGN 2025-09-19 16:01:00 ZZCP.TO 100 CAD 12.00 1255.00 55.00
BUYSELL 2025-06-02 10:00:00 ZZCX250718C00014000.TO -1 CAD 0.40 32.25 7.75
BUYSELL 2025-07-18 16:00:00 ZZCX250718C00014000.TO 1 CAD 0 0
BUYSELL 2025-06-02 10:00:00 ZZCY250718C00014000.TO -2 CAD 0.31 52.50 9.50
BUYSELL 2025-07-07 10:00:00 ZZCY250718C00014000.TO 2 CAD 0.05 14.75 4.75
BUYSELL 2025-06-02 10:00:00 ZZCK250718C00014000.TO -1 CAD 0.40 32.25 7.75
BUYSELL 2025-06-09 10:00:00 ZZCK250718C00014000.TO -1 CAD 0.40 38.50 1.50
BUYSELL 2025-07-07 10:00:00 ZZCK250718C00014000.TO 2 CAD 0.10 24.75 4.75
BUYSELL 2025-06-02 10:00:00 ZZCN250718C00014000.TO -1 CAD 0.05 -2.75 7.75
BUYSELL 2025-07-18 16:00:00 ZZCN250718C00014000.TO 1 CAD 0 0
BUYSELL 2025-08-04 10:00:00 ZZCW250919C00022000.US -1 USD 0.50 45.00 5.00
BUYSELL 2025-09-19 10:00:00 ZZCW250919C00022000.US 1 USD 0.10 15.00 5.00
"""

# The USD/CAD rate: 1.35, but 1.30 on the shares' purchase, 1.25 on the
# writes and 1.40 on the assignment and the buy-back.
_RATES = {"2025-01-10": "1.3000", "2025-08-04": "1.2500", "2025-09-19": "1.4000"}


def _rates() -> str:
    day, out = datetime.date(2024, 6, 3), []
    while day <= datetime.date(2026, 1, 9):
        if day.weekday() < 5:
            d = day.isoformat()
            out.append(f"{d} 12:00:00 USD CAD {_RATES.get(d, '1.3500')} boc\n")
        day += datetime.timedelta(days=1)
    return "".join(out)


# Each 2025 share sale an assignment made, by hand: (shares, gain, Schedule
# 3's proceeds, ACB and outlays). Proceeds: the strike's gross and the
# premium received (31.00 a contract written at 0.31; 40.00 at 0.40);
# outlays: the share leg's 55.00 and the write's commission.
SALES = {
    # 1195.00 + 23.25 - 1208.00; 1250.00 + 31.00; 55.00 + 7.75.
    "ZZCA.TO": (100.0, "10.25", "1281.00", "1208.00", "62.75"),
    "ZZCB.TO": (100.0, "10.25", "1281.00", "1208.00", "62.75"),
    # One of two contracts written in one row for 9.50: half of it, 4.75.
    "ZZCC.TO": (100.0, "13.25", "1281.00", "1208.00", "59.75"),
    # Two writes, 32.25 and 38.50 net, 7.75 and 1.50 commissions:
    # 2545.00 + 70.75 - 2408.00; 2600.00 + 80.00; 55.00 + 9.25.
    "ZZCD.TO": (200.0, "207.75", "2680.00", "2408.00", "64.25"),
    # USD: the shares 2005.00 x 1.30; the write 35.00 net (5.00 commission)
    # x 1.25, 50.00 gross and 6.25; the sale 2045.00 net (55.00) x 1.40.
    # 2863.00 + 43.75 - 2606.50; 2940.00 + 50.00; 77.00 + 6.25.
    "ZZCU.US": (100.0, "300.25", "2990.00", "2606.50", "83.25"),
    # A commission of 7.75 of which 2.00 was refunded: 5.75 paid.
    "ZZCR.TO": (100.0, "12.25", "1281.00", "1208.00", "60.75"),
    # No commission on the write: nothing moves.
    "ZZCZ.TO": (100.0, "17.00", "1280.00", "1208.00", "55.00"),
    # The assignment row's 5.00 fee: 1195.00 + 23.25 - 5.00 - 1208.00;
    # 1250.00 + 31.00; 55.00 + 7.75 + 5.00.
    "ZZCE.TO": (100.0, "5.25", "1281.00", "1208.00", "67.75"),
}


# The share leg's own outlay (its commission and fee; the USD sale's 55.00 x
# 1.40): a work row's commission and fee, the write's apart (`write_outlay`).
OWN = {"ZZCU.US": "77.00"}

# A broker slip's proceeds, as Schedule 3 shows them: the shares' with the
# premium gross, a written option's premium gross (reconcile-slips, TJ-5462).
SLIP = ("Security,Quantity,Proceeds\n"
        "ZZCA.TO,100,1281.00\n"
        "ZZCX250718C00014000.TO,1,40.00\n"
        "ZZCY250718C00014000.TO,2,62.00\n")

# Each written option's Schedule 3 row on line 15199, by hand, the same under
# every timing (grant timing: the write's row and the close's, summed):
# (proceeds, ACB, outlays, gain); the write's commission (`write_outlay`).
WRITTEN = {
    # The other of ZZCC's two contracts (9.50 for both), bought back at 0.05
    # (5.00 + 4.75): 31.00 gross, 26.25 net.
    "ZZCC250516C00012500.TO": ((31.0, 9.75, 4.75, 16.5), "4.75"),
    # Written at 0.40 (32.25 net), expired.
    "ZZCX250718C00014000.TO": ((40.0, 0.0, 7.75, 32.25), "7.75"),
    # Two at 0.31 (52.50 net), bought back at 0.05 (10.00 + 4.75).
    "ZZCY250718C00014000.TO": ((62.0, 14.75, 9.5, 37.75), "9.50"),
    # Two writes (32.25 and 38.50 net), bought back in one row (20.00 + 4.75).
    "ZZCK250718C00014000.TO": ((80.0, 24.75, 9.25, 46.0), "9.25"),
    # 5.00 received, 7.75 paid: a net debit of 2.75, expired.
    "ZZCN250718C00014000.TO": ((5.0, 0.0, 7.75, -2.75), "7.75"),
    # USD: written 45.00 net (5.00 commission) x 1.25, 62.50 gross and
    # 6.25; bought back 15.00 (10.00 + 5.00) x 1.40.
    "ZZCW250919C00022000.US": ((62.5, 21.0, 6.25, 35.25), "6.25"),
}


def _d(x):
    return Decimal(str(x)).quantize(Decimal("0.01"))


class _Book:
    """The project under one premium timing (TIMING: its [settings] lines)."""

    TIMING = ""

    @classmethod
    def setUpClass(cls):
        cls.p = Project(files={"margin": MARGIN_TT}, toml=_HEAD + cls.TIMING + _ACCOUNT,
                        planted=False)
        plant(cls.p.root / "work" / "to_base.csv", _rates(), cls.p.home)
        cls.p.run()
        work = cls.p.root / "work"
        name = next(n for n in ("margin_gains_wash.json", "margin_gains.json")
                    if (work / n).is_file())
        cls.gains = json.loads((work / name).read_text(encoding="utf-8"))
        cls.sum = cls.p.json("sum")
        cls.form = cls.p.json("form-export")
        cls.cra = cls.p.json("gains-cra")
        slip = cls.p.root.parent / "t5008.csv"
        slip.write_text(SLIP, encoding="utf-8")
        # (exit 1: the book's other sales are on no slip)
        cls.slips = json.loads(cls.p.tjs("reconcile-slips", str(slip), "--json").stdout)

    def _sale(self, symbol):
        (row,) = [t for t in self.gains["transactions"] if t["symbol"] == symbol]
        return row

    def test_each_sale_keeps_its_gain_and_its_proceeds_net_of_its_outlays(self):
        for symbol, (qty, gain, proceeds, acb, outlays) in SALES.items():
            with self.subTest(symbol=symbol):
                t = self._sale(symbol)
                self.assertEqual((t["qty"], _d(t["gain"]), _d(t["cost"])),
                                 (qty, _d(gain), _d(acb)))
                # The work file's proceeds are net of the outlays, its own
                # commission and fee (as the trade's) and the write's apart.
                own = OWN.get(symbol, "55.00")
                self.assertEqual(_d(t["commission"]) + _d(t["fee"]), _d(own))
                self.assertEqual(_d(t.get("write_outlay", 0)), _d(outlays) - _d(own))
                self.assertEqual(_d(t["proceeds"]), _d(proceeds) - _d(outlays))

    def test_form_export_shows_the_premium_gross_and_the_commission_an_outlay(self):
        rows = {r["symbol"]: r for r in self.form["rows"]}
        for symbol, (_q, gain, proceeds, acb, outlays) in SALES.items():
            with self.subTest(symbol=symbol):
                r = rows[symbol]
                self.assertEqual((r["proceeds"], r["acb"], r["outlays"], r["gain"]),
                                 (float(proceeds), float(acb), float(outlays), float(gain)))

    def test_sum_for_the_return_adds_them(self):
        lines = {ln["gain_code"]: ln for ln in self.sum["filing"]["lines"]}
        shares = lines["13200"]
        # The eight sales: proceeds 13355.00 (13300.75 with the premiums
        # net), outlays 516.25 (462.00), ACB 12262.50, gain 576.25.
        self.assertEqual((shares["proceeds"], shares["acb"], shares["outlays"], shares["gain"],
                          shares["dispositions"]),
                         (13355.0, 12262.5, 516.25, 576.25, 8))
        self.assertEqual(self.form["totals"]["proceeds_13199"], 13355.0)
        self.assertEqual(self.form["totals"]["gain_13200"], 576.25)

    def test_the_assigned_put_lowers_the_shares_cost(self):
        # 1255.00 - 32.25, net or gross the same (CA-OPT-06, s.49(3.1)).
        held = {h["symbol"]: h for h in self.gains["inventory"]}
        self.assertEqual((held["ZZCP.TO"]["qty"], _d(held["ZZCP.TO"]["total_cost"])),
                         (100.0, _d("1222.75")))
        self.assertNotIn("ZZCP.TO", {t["symbol"] for t in self.gains["transactions"]})

    def test_no_assigned_write_is_a_disposition_of_its_own(self):
        written = {t["symbol"] for t in self.gains["transactions"] if "C000" in t["symbol"]}
        self.assertEqual(written, set(WRITTEN))

    def test_a_written_options_row_shows_its_premium_gross_and_its_commission_an_outlay(self):
        rows = {r["symbol"]: r for r in self.form["rows"]}
        for symbol, (cells, _wo) in WRITTEN.items():
            with self.subTest(symbol=symbol):
                r = rows[symbol]
                self.assertEqual((r["proceeds"], r["acb"], r["outlays"], r["gain"]), cells)

    def test_reconcile_slips_reads_the_gross_form_export_shows(self):
        status = {r["symbol"]: r["status"] for r in self.slips["rows"]}
        self.assertEqual({s: status[s] for s in ("ZZCA.TO", "ZZCX250718C00014000.TO",
                                                  "ZZCY250718C00014000.TO")},
                         dict.fromkeys(("ZZCA.TO", "ZZCX250718C00014000.TO",
                                        "ZZCY250718C00014000.TO"), "OK"))

    def test_gains_cra_says_why_an_assigned_usd_calls_fx_is_off(self):
        rows = {r["symbol"]: r for r in self.cra["rows"]}
        # 2140.00 USD x 1.40 is 2996.00, not 2990.00: the 40.00 premium is
        # at the write's 1.25 (CA-OPT-06).
        self.assertEqual(rows["ZZCU.US"]["note_kinds"], ["fx_check", "assigned_premium_fx"])

    def test_line_15199_adds_them(self):
        # 31.00 + 40.00 + 62.00 + 80.00 + 5.00 + 62.50; ACB 9.75 + 14.75 +
        # 24.75 + 21.00; outlays 4.75 + 7.75 + 9.50 + 9.25 + 7.75 + 6.25.
        lines = {ln["gain_code"]: ln for ln in self.sum["filing"]["lines"]}
        options = lines["15300"]
        self.assertEqual((options["proceeds"], options["acb"], options["outlays"],
                          options["gain"]), (280.5, 70.25, 45.25, 165.0))


@rule(R.CA_OPT_06, R.CA_DISP_06)
class TestGrantTimingWithTheDecemberWrite(_Book, unittest.TestCase):
    """Every write taxed when written; the December 2024 write's premium
    leaves 2024 (s.49(4)). ZZCC's other contract stays a write (its gross
    31.00, its 4.75 an outlay) and its buy-back a loss."""

    TIMING = "option_grant_timing_since = 2024\n"


@rule(R.CA_OPT_06, R.CA_DISP_06)
class TestGrantTimingAfterTheDecemberWrite(_Book, unittest.TestCase):
    """The 2024 write is a transition contract on close timing: its premium
    is deferred to the assignment, and folds into the shares the same."""

    TIMING = "option_grant_timing_since = 2025\n"


@rule(R.CA_OPT_06, R.CA_DISP_06)
class TestCloseTiming(_Book, unittest.TestCase):
    """Every premium deferred to its close: the writes' lots are one
    average, and a close takes their average commission as it takes their
    average premium (ZZCD's and ZZCK's two writes: 4.625 each, 9.25
    together). The close's row is the write's disposition: its commission
    and fee its own, the write's in `write_outlay` (backlog TJ-5460)."""

    TIMING = 'option_premium_timing = "close"\n'

    def test_the_close_carries_the_writes_commission(self):
        for symbol, ((_p, acb, _o, gain), wo) in WRITTEN.items():
            with self.subTest(symbol=symbol):
                (t,) = [t for t in self.gains["transactions"] if t["symbol"] == symbol]
                self.assertEqual(_d(t["write_outlay"]), _d(wo))
                # The close's own fee, in the ACB (an expiry has none).
                self.assertEqual(_d(t["commission"]) + _d(t["fee"]),
                                 {"ZZCX250718C00014000.TO": _d(0), "ZZCN250718C00014000.TO": _d(0),
                                  "ZZCW250919C00022000.US": _d("7.00")}.get(symbol, _d("4.75")))
                self.assertEqual(_d(t["gain"]), _d(gain))

    def test_gains_cra_gives_a_bought_back_usd_write_its_own_note(self):
        # The close's row carries `write_outlay` too: its FX is the write's,
        # weighted, and the assigned call's note is not its own.
        rows = {r["symbol"]: r for r in self.cra["rows"]}
        kinds = rows["ZZCW250919C00022000.US"]["note_kinds"]
        self.assertIn("weighted_fx", kinds)
        self.assertNotIn("assigned_premium_fx", kinds)


MORE_TT = """\
BUYSELL 2025-02-03 10:00:00 ZZNA250321C00012500.TO -1 CAD 0.31 23.25 7.75
ASSIGN 2025-03-21 16:00:00 ZZNA250321C00012500.TO 1 CAD 0 0
ASSIGN 2025-03-21 16:01:00 ZZNA.TO -100 CAD 12.50 1195.00 55.00
BUYSELL 2025-04-01 10:00:00 ZZNA.TO 100 CAD 11.00 1108.00 8.00
BUYSELL 2025-01-06 10:00:00 ZZNB.TO 50 CAD 12.00 608.00 8.00
BUYSELL 2025-02-03 10:00:00 ZZNB250321C00012500.TO -1 CAD 0.31 23.50 7.50
ASSIGN 2025-03-21 16:00:00 ZZNB250321C00012500.TO 1 CAD 0 0
ASSIGN 2025-03-21 16:01:00 ZZNB.TO -100 CAD 12.50 1195.00 55.00
BUYSELL 2025-04-01 10:00:00 ZZNB.TO 50 CAD 11.00 558.00 8.00
BUYSELL 2025-01-06 10:00:00 ZZRB.TO 100 CAD 12.00 1208.00 8.00
BUYSELL 2025-02-03 10:00:00 ZZRB250321C00012500.TO -1 CAD 0.31 32.00 -1.00
ASSIGN 2025-03-21 16:00:00 ZZRB250321C00012500.TO 1 CAD 0 0
ASSIGN 2025-03-21 16:01:00 ZZRB.TO -100 CAD 12.50 1195.00 55.00
BUYSELL 2025-01-06 10:00:00 ZZSP.TO 200 CAD 12.00 2408.00 8.00
BUYSELL 2025-02-03 10:00:00 ZZSP250321C00012500.TO -2 CAD 0.31 52.50 9.50
ASSIGN 2025-03-21 16:00:00 ZZSP250321C00012500.TO 2 CAD 0 0
ASSIGN 2025-03-21 16:01:00 ZZSP.TO -100 CAD 12.50 1195.00 55.00
ASSIGN 2025-03-21 16:02:00 ZZSP.TO -100 CAD 12.50 1195.00 55.00
"""

# Each security's Schedule 3 row (proceeds, ACB, outlays, gain), by hand, the
# same under every timing; and each work row's `write_outlay`, by date.
MORE = {
    # Assigned with no shares held: a short sale of 100 (1195.00 + 23.25
    # net, its commissions netted, as a short sale's opening commission),
    # covered for 1108.00.
    "ZZNA.TO": ((1218.25, 1108.0, 0.0, 110.25), [None]),
    # 50 held, 100 delivered: the 50 sold, 597.50 + 11.75 (half the 23.50
    # premium) less 608.00, its outlays half the 55.00 and half the write's
    # 7.50; the other 50 a short sale, 609.25 netted, covered for 558.00.
    "ZZNB.TO": ((1249.75, 1166.0, 31.25, 52.5), [3.75, None]),
    # The write's commission a 1.00 rebate: no outlay, it stays netted.
    "ZZRB.TO": ((1282.0, 1208.0, 55.0, 19.0), [None]),
    # Two legs of 100: each half the premium (26.25) and of the 9.50.
    "ZZSP.TO": ((2562.0, 2408.0, 119.5, 34.5), [4.75, 4.75]),
}


class _More:
    TIMING = ""

    @classmethod
    def setUpClass(cls):
        cls.p = Project(files={"margin": MORE_TT}, toml=_HEAD + cls.TIMING + _ACCOUNT,
                        planted=False)
        plant(cls.p.root / "work" / "to_base.csv", _rates(), cls.p.home)
        cls.p.run()
        work = cls.p.root / "work"
        name = next(n for n in ("margin_gains_wash.json", "margin_gains.json")
                    if (work / n).is_file())
        cls.gains = json.loads((work / name).read_text(encoding="utf-8"))
        cls.form = cls.p.json("form-export")

    def test_each_security_on_schedule_3(self):
        rows = {r["symbol"]: r for r in self.form["rows"]}
        for symbol, (cells, _wo) in MORE.items():
            with self.subTest(symbol=symbol):
                r = rows[symbol]
                self.assertEqual((r["proceeds"], r["acb"], r["outlays"], r["gain"]), cells)

    def test_each_row_keeps_its_own_commission_and_the_write_s_apart(self):
        for symbol, (_cells, wos) in MORE.items():
            with self.subTest(symbol=symbol):
                rows = sorted((t for t in self.gains["transactions"] if t["symbol"] == symbol),
                              key=lambda t: (t["date"], t["direction"]))
                self.assertEqual([t.get("write_outlay") for t in rows], wos)
                # The share legs' own 55.00, or its half; a cover's 8.00.
                self.assertTrue(all(_d(t["commission"]) + _d(t["fee"])
                                    in (_d("55.00"), _d("27.50"), _d("8.00")) for t in rows))


@rule(R.CA_OPT_06, R.CA_DISP_06)
class TestMoreUnderGrantTiming(_More, unittest.TestCase):
    TIMING = "option_grant_timing_since = 2024\n"


@rule(R.CA_OPT_06, R.CA_DISP_06)
class TestMoreUnderCloseTiming(_More, unittest.TestCase):
    TIMING = 'option_premium_timing = "close"\n'

    def test_a_damaged_write_outlay_is_refused_like_the_other_money_keys(self):
        work = self.p.root / "work"
        files = sorted(set(work.glob("*gains*.json")) | set(work.glob(".*gains*.json")))
        saved = {f: f.read_bytes() for f in files}
        try:
            for bad in ("3.75", "abc"):
                for f in files:
                    doc = json.loads(saved[f])
                    for t in doc.get("transactions") or []:
                        if "write_outlay" in t:
                            t["write_outlay"] = bad
                    f.write_text(json.dumps(doc), encoding="utf-8")
                for cmd in ("sum", "form-export"):
                    r = self.p.tjs(cmd)
                    with self.subTest(value=bad, command=cmd):
                        self.assertNotEqual(r.returncode, 0)
                        self.assertIn(f"write_outlay is {bad!r}, not a number", r.stderr)
                        self.assertNotIn("Traceback", r.stdout + r.stderr)
        finally:
            for f, b in saved.items():
                f.write_bytes(b)


@rule(R.CA_DISP_06)
class TestAYearClosedBeforeTheChange(unittest.TestCase):
    """Close timing: 2025 closed on the same books with no `write_outlay` (as
    a taxjson before it wrote them), then re-run: `tjs check-filed` reports
    lines 13199 and 15199 drifted by the writes' commissions, no gain line,
    and names what refreshes the lock."""

    @classmethod
    def setUpClass(cls):
        cls.p = Project(files={"margin": MARGIN_TT},
                        toml=_HEAD + 'option_premium_timing = "close"\n' + _ACCOUNT,
                        planted=False)
        plant(cls.p.root / "work" / "to_base.csv", _rates(), cls.p.home)
        cls.p.run()
        work = cls.p.root / "work"
        for f in set(work.glob("*gains*.json")) | set(work.glob(".*gains*.json")):
            doc = json.loads(f.read_text(encoding="utf-8"))
            for t in doc.get("transactions") or []:
                t.pop("write_outlay", None)
            f.write_text(json.dumps(doc), encoding="utf-8")
        r = cls.p.tjs("close-year", "--yes")
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
        cls.p.run()
        cls.check = cls.p.tjs("check-filed")

    def test_check_filed_reports_the_proceeds_lines(self):
        out = self.check.stdout + self.check.stderr
        self.assertNotEqual(self.check.returncode, 0)
        for line, was, now in (("13199", 13300.75, 13355.0), ("15199", 238.0, 280.5)):
            with self.subTest(line=line):
                self.assertIn(f"form line {line} filed {was:,.2f} -> now {now:,.2f} "
                              f"(drift +{now - was:,.2f})", out)
        self.assertEqual(sorted(set(re.findall(r"form line (\d+) filed", out))),
                         ["13199", "15199"])
        self.assertIn("tjs close-year --force", out)


def _tx(action, date, symbol, qty, net, id_):
    return TaxTransaction(action=action, date=date, time="16:00:00", symbol=symbol,
                          quantity=qty, price=abs(net / qty) if qty else 0.0,
                          net_amount=net, currency="CAD", account="margin", id=id_)


@rule(R.CA_OPT_06, R.CA_DISP_06)
class TestTheEngineCarriesTheWritesCommission(unittest.TestCase):
    """The pieces: a written lot keeps its commission (`fee_per_unit`), a
    close returns its share (`close_lots`' fifth item: every lot on an
    assignment, a lot no write record showed on any close), the premium
    ledger carries an assignment's to the share legs by shares and keeps a
    net rebate netted (`stage(outlay=)`, clamped at 0), and a damaged
    `write_outlay` is refused as a damaged commission is."""

    def _pool(self, recognise):
        pool = AcbPool.new("CAD")
        pool.open(-2.0, Decimal("52.50"), date="2025-02-03", settle="2025-02-04")
        pool.open_lot(tx_id="w1", seq=1, units=2.0, premium=Decimal("52.50"),
                      recognise=recognise, date="2025-02-03", year="2025",
                      account="margin", outlay=Decimal("9.50"))
        return pool

    def test_close_lots_returns_the_write_s_commission(self):
        for recognise, bought_back in ((False, Decimal("4.75")), (True, Decimal(0))):
            with self.subTest(grant=recognise):
                pool = self._pool(recognise)
                self.assertEqual(pool.close_lots(1.0, False, "margin")[4], bought_back)
                self.assertEqual(pool.close_lots(1.0, True, "margin")[4], Decimal("4.75"))

    def test_the_ledger_shares_the_outlay_by_shares_and_keeps_a_rebate_netted(self):
        call = "ZZQA250321C00012500"
        ex = _tx("ASSIGN", "2025-03-21", call, 1, 0.0, "x1")
        legs = [_tx("BUYSELL", "2025-03-21", "ZZQA", -60, 750.0, "l1"),
                _tx("BUYSELL", "2025-03-21", "ZZQA", -40, 500.0, "l2")]

        def ledger():
            return _AssignPremiumLedger(
                [ex] + legs, lambda t: t.action in ("BUYSELL", "ASSIGN")
                and not OS.is_option_symbol(t.symbol), None)
        led = ledger()
        led.stage(ex, "ZZQA", Decimal("23.25"), outlay=Decimal("7.75"))
        self.assertEqual([led.take(t).outlay for t in legs], [Decimal("4.65"), Decimal("3.10")])
        led = ledger()
        led.stage(ex, "ZZQA", Decimal("32.00"), outlay=Decimal("-1.00"))
        self.assertEqual([led.take(t).outlay for t in legs], [Decimal(0), Decimal(0)])

    def test_a_damaged_write_outlay_is_refused(self):
        row = {"symbol": "ZZQA.TO", "qty": 100.0, "proceeds": 1218.25, "cost": 1208.0,
               "gain": 10.25, "commission": 0.0, "fee": 55.0, "direction": "LONG",
               "date": "2025-03-21", "date_settle": "2025-03-21"}
        self.assertEqual(TF.schedule3_disposition(dict(row, write_outlay=7.75), 2025)
                         ["outlays"], 62.75)
        for key in ("commission", "write_outlay"):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ResultShapeError, f'"{key}" is \'abc\''):
                    TF.schedule3_disposition(dict(row, **{key: "abc"}), 2025)
                with self.assertRaisesRegex(JI.InputFileError, f"{key} is '7.75', not a number"):
                    JI.check_row_types([dict(row, **{key: "7.75"})], "gains.json")


if __name__ == "__main__":
    unittest.main()
