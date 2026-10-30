"""Fix round 7 (2026-10-29), group "reconcile": `tjs reconcile-slips`
headers, quantities, option premiums and short sales (backlog TJ-0890,
TJ-0891, TJ-0892, TJ-0902, TJ-0906, TJ-0779, TJ-0780, TJ-0781,
TJ-0782). Synthetic data only."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from taxjson.bin.taxjson_reconcile_slips import (
    AmbiguousHeader, load_computed, load_slip, main, reconcile,
    slip_symbol)

SYN = "SYN.TO"
OPT = "SYN251219C00001000.TO"


def _run(td, slip_text, entries=None, doc=None, country="canada",
         year=2025):
    """main() on a slip CSV and one gains file: (exit code, report)."""
    slip = Path(td) / "slip.csv"
    slip.write_text(slip_text)
    g = Path(td) / "margin_gains.json"
    g.write_text(json.dumps(doc if doc is not None
                            else {"transactions": entries or []}))
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = main([str(slip), "--gains", str(g), "--country", country,
                   "--year", str(year), "--json", "--tolerance", "0.01"])
    try:
        rep = json.loads(out.getvalue())
    except ValueError:
        rep = None
    return rc, rep, err.getvalue()


def _sale(qty=10, proceeds=120.0, cost=100.0, symbol=SYN):
    return {"date": "2025-03-03", "date_settle": "2025-03-04",
            "symbol": symbol, "qty": -qty, "proceeds": proceeds,
            "cost": cost, "gain": proceeds - cost, "commission": 0.0,
            "fee": 0.0, "direction": "LONG", "account": "margin",
            "currency": "CAD"}


HDR = "symbol,quantity,proceeds,cost,currency\n"


class DuplicateHeaderTJ0890(unittest.TestCase):
    def test_two_proceeds_columns_are_refused(self):
        with tempfile.TemporaryDirectory() as td:
            rc, rep, err = _run(
                td, "symbol,quantity,proceeds,proceeds,cost,currency\n"
                    "SYN.TO,10,999,120,100,CAD\n", [_sale()])
        self.assertEqual(rc, 2)
        self.assertIsNone(rep)
        self.assertIn("ambiguous header", err)
        self.assertIn("'proceeds'", err)

    def test_order_swapped_refused_too(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s.csv"
            p.write_text("Symbol,Proceeds,Quantity, proceeds\n"
                         "SYN.TO,120,10,999\n")
            with self.assertRaises(AmbiguousHeader):
                load_slip(p)

    def test_duplicate_unused_column_is_fine(self):
        with tempfile.TemporaryDirectory() as td:
            rc, rep, _ = _run(
                td, "symbol,quantity,proceeds,cost,currency,note,note\n"
                    "SYN.TO,10,120,100,CAD,a,b\n", [_sale()])
        self.assertEqual(rc, 0, rep)
        self.assertTrue(rep["clean"])

    def test_one_column_control_reconciles(self):
        with tempfile.TemporaryDirectory() as td:
            rc, rep, _ = _run(td, HDR + "SYN.TO,10,120,100,CAD\n",
                              [_sale()])
        self.assertEqual(rc, 0)
        self.assertEqual(rep["rows"][0]["status"], "OK")


class ExplicitZeroQuantityTJ0891(unittest.TestCase):
    def test_stated_zero_disagrees_with_ten(self):
        with tempfile.TemporaryDirectory() as td:
            rc, rep, _ = _run(td, HDR + "SYN.TO,0,120,100,CAD\n",
                              [_sale()])
        self.assertEqual(rc, 1)
        row = rep["rows"][0]
        self.assertEqual(row["status"], "MISMATCH")
        self.assertIn("quantity off by -10.0000", row["detail"])

    def test_blank_quantity_is_still_not_compared(self):
        with tempfile.TemporaryDirectory() as td:
            rc, rep, _ = _run(td, HDR + "SYN.TO,,120,100,CAD\n",
                              [_sale()])
        self.assertEqual(rc, 0, rep)
        self.assertTrue(rep["clean"])

    def test_dict_without_row_counts_keeps_old_reading(self):
        # An older caller's slip dict (no qty_rows): 0 is "no quantity".
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "g.json"
            g.write_text(json.dumps({"transactions": [_sale()]}))
            computed = load_computed([g], 2025)
        rep = reconcile({"SYN": {"qty": 0.0, "proceeds": 120.0,
                                 "cost": None, "rows": 1}}, computed, 0.01)
        self.assertTrue(rep["clean"])


class PartialCostTJ0892(unittest.TestCase):
    def test_partly_blank_cost_is_not_a_difference(self):
        with tempfile.TemporaryDirectory() as td:
            rc, rep, _ = _run(td, HDR + "SYN.TO,5,60,50,CAD\n"
                                        "SYN.TO,5,60,,CAD\n", [_sale()])
        self.assertEqual(rc, 0, rep)
        d = rep["rows"][0]["detail"]
        self.assertNotIn("differs by", d)
        self.assertIn("cost not compared: 1 of 2 slip row(s) state no "
                      "cost", d)

    def test_complete_cost_still_compared(self):
        with tempfile.TemporaryDirectory() as td:
            rc, rep, _ = _run(td, HDR + "SYN.TO,5,60,50,CAD\n"
                                        "SYN.TO,5,60,20,CAD\n", [_sale()])
        self.assertEqual(rc, 0)
        self.assertIn("slip cost differs by -30.00",
                      rep["rows"][0]["detail"])

    def test_two_slip_files_keep_the_count(self):
        with tempfile.TemporaryDirectory() as td:
            a, b = Path(td) / "a.csv", Path(td) / "b.csv"
            a.write_text(HDR + "SYN.TO,5,60,50,CAD\n")
            b.write_text(HDR + "SYN.TO,5,60,,CAD\n")
            g = Path(td) / "g.json"
            g.write_text(json.dumps({"transactions": [_sale()]}))
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                rc = main([str(a), str(b), "--gains", str(g), "--country",
                           "canada", "--year", "2025", "--json"])
        rep = json.loads(out.getvalue())
        self.assertEqual(rc, 0)
        self.assertIn("cost not compared", rep["rows"][0]["detail"])


class OptionStrikePrecisionTJ0902(unittest.TestCase):
    def test_four_decimal_strike_is_not_rounded_into_a_match(self):
        sell = {"date": "2025-03-03", "date_settle": "2025-03-04",
                "symbol": OPT, "qty": -1, "proceeds": 20.0, "cost": 10.0,
                "gain": 10.0, "commission": 0.0, "fee": 0.0,
                "direction": "LONG", "account": "margin",
                "currency": "CAD"}
        with tempfile.TemporaryDirectory() as td:
            rc, rep, err = _run(td, HDR + "SYN 19DEC25 1.0004 C,1,20,10,"
                                          "CAD\n", [sell])
        self.assertEqual(rc, 1)
        self.assertFalse(rep["clean"])
        self.assertEqual(rep.get("unreadable_rows"), 1)
        self.assertIn("OCC symbol cannot hold its strike 1.0004", err)

    def test_representable_strikes_still_convert(self):
        self.assertEqual(slip_symbol("SYN 19DEC25 1.000 C"),
                         "SYN251219C00001000")
        self.assertEqual(slip_symbol("SYN 19DEC25 1.001 C"),
                         "SYN251219C00001001")
        self.assertEqual(slip_symbol("CALL SYN12/19/25 2.5"),
                         "SYN251219C00002500")
        # 0.1 * 1000 in floats; exact in decimals.
        self.assertEqual(slip_symbol("SYN 19DEC25 0.3 P"),
                         "SYN251219P00000300")

    def test_non_strict_keeps_the_cell(self):
        self.assertEqual(slip_symbol("syn 19dec25 1.0004 c"),
                         "SYN 19DEC25 1.0004 C")


def _short(symbol=OPT, qty=1, proceeds=-0.0, cost=-10.0, date="2025-06-20",
           **extra):
    """An engine SHORT row: `cost` the negated short-sale (write)
    proceeds, `proceeds` the negated cover cost."""
    e = {"date": date, "date_settle": date, "symbol": symbol, "qty": qty,
         "proceeds": proceeds, "cost": cost, "gain": proceeds - cost,
         "commission": 0.0, "fee": 0.0, "direction": "SHORT",
         "is_option": True, "account": "margin", "currency": "CAD"}
    e.update(extra)
    return e


class NetDebitWriteTJ0780(unittest.TestCase):
    """Close timing: a call written for gross 3 with commission 10 (net
    -7) expires; the engine's close row has cost +7."""

    def _rep(self, slip_proceeds, cost=7.0):
        with tempfile.TemporaryDirectory() as td:
            return _run(td, "symbol,quantity,proceeds,currency\n"
                            f"{OPT},1,{slip_proceeds},CAD\n",
                        [_short(cost=cost)])

    def test_net_debit_slip_reconciles(self):
        rc, rep, _ = self._rep(-7)
        self.assertEqual(rc, 0, rep)
        self.assertEqual(rep["rows"][0]["status"], "OK")

    def test_wrong_signed_slip_fails(self):
        rc, rep, _ = self._rep(7)
        self.assertEqual(rc, 1)
        self.assertIn("proceeds off by +14.00", rep["rows"][0]["detail"])

    def test_fee_free_control(self):
        rc, rep, _ = self._rep(3, cost=-3.0)
        self.assertEqual(rc, 0, rep)

    def test_cover_cost_keeps_its_sign(self):
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "g.json"
            g.write_text(json.dumps({"transactions": [
                _short(proceeds=-5.0, cost=-10.0)]}))
            rec = load_computed([g], 2025)["SYN251219C00001000"]
        self.assertEqual((rec["proceeds_net"], rec["cost"]), (10.0, 5.0))

    def test_grant_write_for_a_net_debit_keeps_its_gross(self):
        # Grant timing: the write row itself (gross 3, commission 10),
        # open at the year end (it expires in 2026).
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "g.json"
            g.write_text(json.dumps({"transactions": [
                _short(symbol="SYN260116C00001000.TO", date="2025-01-02",
                       cost=7.0, commission=10.0, grant=True)]}))
            rec = load_computed([g], 2025)["SYN260116C00001000"]
        self.assertEqual((rec["open_grant"], rec["open_grant_net"]),
                         (3.0, -7.0))



class UnknownShortProceedsTJ0906(unittest.TestCase):
    """The engine's manual row for covering an opening short of unknown
    proceeds: the cover cost (500) is known, the short sale's is not
    (no `cost`)."""

    MANUAL = {"date": "2025-02-03", "date_settle": "2025-02-03",
              "symbol": "SYN.US", "qty": 10, "proceeds": -500.0,
              "raw_gain": -500.0, "disallowed_amount": 0.0,
              "acquired_date": "2024-01-01", "account": "a",
              "currency": "USD", "commission": 0.0, "fee": 0.0,
              "direction": "SHORT", "term": "SHORT_TERM"}

    def _rep(self, rows):
        doc = {"transactions": [],
               "manual_reporting_required": [dict(self.MANUAL)]}
        with tempfile.TemporaryDirectory() as td:
            return _run(td, HDR + rows, doc=doc, country="usa")

    def test_empty_slip_is_missing_not_an_expiry(self):
        rc, rep, _ = self._rep("")
        self.assertEqual(rc, 1)
        row = rep["rows"][0]
        self.assertEqual(row["status"], "MISSING_FROM_SLIP")
        self.assertNotIn("expired", row["detail"])
        self.assertIn("proceeds unknown", row["detail"])

    def test_zero_proceeds_slip_is_not_certified(self):
        rc, rep, _ = self._rep("SYN.US,10,0,500,USD\n")
        self.assertEqual(rc, 1)
        self.assertFalse(rep["clean"])
        self.assertEqual(rep["rows"][0]["status"], "UNKNOWN_PROCEEDS")
        self.assertEqual(rep["counts"]["unknown_proceeds"], 1)

    def test_real_slip_is_uncheckable_not_off_by(self):
        rc, rep, _ = self._rep("SYN.US,10,600,500,USD\n")
        self.assertEqual(rc, 1)
        d = rep["rows"][0]["detail"]
        self.assertEqual(rep["rows"][0]["status"], "UNKNOWN_PROCEEDS")
        self.assertNotIn("off by", d)
        self.assertIn("proceeds not compared: 1 short cover(s)", d)
        self.assertNotIn("unknown cost", d)

    def test_quantity_still_compared(self):
        rc, rep, _ = self._rep("SYN.US,4,600,500,USD\n")
        self.assertEqual(rep["rows"][0]["status"], "MISMATCH")
        self.assertIn("quantity off by", rep["rows"][0]["detail"])

    def test_unknown_cost_long_row_still_reconciles(self):
        doc = {"transactions": [], "manual_reporting_required": [
            {"date": "2025-02-03", "date_settle": "2025-02-04",
             "symbol": "SYN.TO", "qty": -10, "proceeds": 120.0,
             "account": "margin", "currency": "CAD",
             "commission": 0.0, "fee": 0.0}]}
        with tempfile.TemporaryDirectory() as td:
            rc, rep, _ = _run(td, HDR + "SYN.TO,10,120,,CAD\n", doc=doc)
        self.assertEqual(rc, 0, rep)
        self.assertIn("unknown cost", rep["rows"][0]["detail"])

    def test_brief_view_and_checklist_name_it(self):
        from taxjson.bin.taxjson_reconcile_slips import render_brief
        from taxjson.lib.checklist import _slip_mismatch_summary
        rc, rep, _ = self._rep("SYN.US,10,0,500,USD\n")
        text = " ".join(render_brief(rep, 0.01, "usa").split())
        self.assertIn("1 unknown proceeds", text)
        self.assertIn("Explain or fix 1 difference(s)", text)
        summ = _slip_mismatch_summary(rc, json.dumps(rep), "")
        self.assertIn("1 with unknown short-sale proceeds", summ)
        self.assertIn("SYN.US UNKNOWN_PROCEEDS", summ)


OPT25 = "SYN250620C00001000.TO"


def _write(date, premium, units=1, commission=0.0, symbol=OPT25):
    """A grant-timing WRITE record (engine shape: cost -net premium)."""
    return _short(symbol=symbol, qty=units, proceeds=-0.0,
                  cost=-(premium - commission), date=date, grant=True,
                  commission=commission)


def _buyback(date, paid, closed, units=1, symbol=OPT25):
    """A buy-back row closing grant lots: `closed` {write year:
    premium} (the engine's grant_closed)."""
    return _short(symbol=symbol, qty=units, proceeds=-paid, cost=-0.0,
                  date=date, grant=False,
                  grant_closed={y: {"premium": p, "units": units}
                                for y, p in closed.items()})


class PriorYearPremiumMissingSlipTJ0782(unittest.TestCase):
    """Written in 2024 for 10 (grant timing since 2024), bought back for
    1 in 2025: the broker's 2025 slip reports proceeds 10."""

    def _rep(self, rows, entries):
        with tempfile.TemporaryDirectory() as td:
            return _run(td, "symbol,quantity,proceeds,currency\n" + rows,
                        entries)

    def test_absent_slip_row_is_missing(self):
        rc, rep, _ = self._rep("", [_buyback("2025-06-02", 1.0,
                                             {"2024": 10.0})])
        self.assertEqual(rc, 1)
        row = rep["rows"][0]
        self.assertEqual(row["status"], "MISSING_FROM_SLIP")
        self.assertNotIn("expired", row["detail"])
        self.assertIn("would report 10.00", row["detail"])

    def test_present_slip_row_still_reconciles(self):
        rc, rep, _ = self._rep(f"{OPT25},1,10,CAD\n",
                               [_buyback("2025-06-02", 1.0,
                                         {"2024": 10.0})])
        self.assertEqual(rc, 0, rep)
        self.assertIn("earlier-year write", rep["rows"][0]["detail"])

    def test_current_year_control(self):
        rc, rep, _ = self._rep("", [_write("2025-01-02", 10.0),
                                    _buyback("2025-06-02", 1.0,
                                             {"2025": 10.0})])
        self.assertEqual(rc, 1)
        self.assertEqual(rep["rows"][0]["status"], "MISSING_FROM_SLIP")

    def test_long_option_expiry_still_expected_without_slip(self):
        e = {"date": "2025-06-20", "date_settle": "2025-06-20",
             "symbol": OPT25, "qty": -1, "proceeds": 0.0, "cost": 5.0,
             "gain": -5.0, "direction": "LONG", "account": "margin",
             "currency": "CAD"}
        rc, rep, _ = self._rep("", [e])
        self.assertEqual(rc, 0)
        self.assertEqual(rep["rows"][0]["status"], "NO_SLIP_EXPECTED")



class UnequalWritesPartialCloseTJ0779(unittest.TestCase):
    """Two identical calls written for 10 (Jan) and 30 (Feb), the
    January one bought back for 5 in March, February's open at the year
    end: the close-year slip shows quantity 1, proceeds 10, cost 5."""

    OPT = "SYN260116C00001000.TO"

    def _entries(self, second):
        return [_write("2025-01-02", 10.0, symbol=self.OPT),
                _write("2025-02-03", second, symbol=self.OPT),
                _buyback("2025-03-03", 5.0, {"2025": 10.0},
                         symbol=self.OPT)]

    def test_open_premium_is_the_open_write(self):
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / "g.json"
            g.write_text(json.dumps({"transactions": self._entries(30.0)}))
            rec = load_computed([g], 2025)["SYN260116C00001000"]
        self.assertEqual(rec["open_grant_qty"], 1.0)
        self.assertAlmostEqual(rec["open_grant"], 30.0)
        self.assertAlmostEqual(rec["open_grant_net"], 30.0)

    def test_correct_slip_reconciles(self):
        for second in (10.0, 30.0):
            with self.subTest(second=second), \
                    tempfile.TemporaryDirectory() as td:
                rc, rep, _ = _run(td, HDR + f"{self.OPT},1,10,5,CAD\n",
                                  self._entries(second))
                self.assertEqual(rc, 0, rep)
                self.assertIn(f"{second:,.2f} premium of written option(s) "
                              f"still open", rep["rows"][0]["detail"])

    def test_writes_out_of_order_in_the_file(self):
        ents = self._entries(30.0)
        ents = [ents[1], ents[0], ents[2]]
        with tempfile.TemporaryDirectory() as td:
            rc, rep, _ = _run(td, HDR + f"{self.OPT},1,10,5,CAD\n", ents)
        self.assertEqual(rc, 0, rep)



class ExpiredGrantWriteTJ0781(unittest.TestCase):
    """A call written in 2025 for 10 (grant timing) expires worthless on
    2025-06-20: the engine writes no close row for a nil expiry, and the
    broker's 2025 slip reports the contract with proceeds 10."""

    def _rep(self, rows):
        with tempfile.TemporaryDirectory() as td:
            return _run(td, "symbol,quantity,proceeds,currency\n" + rows,
                        [_write("2025-01-02", 10.0)])

    def test_empty_slip_is_missing_not_still_open(self):
        rc, rep, _ = self._rep("")
        self.assertEqual(rc, 1)
        row = rep["rows"][0]
        self.assertEqual(row["status"], "MISSING_FROM_SLIP")
        self.assertNotIn("still open", row["detail"])

    def test_correct_slip_reconciles_without_open_note(self):
        rc, rep, _ = self._rep(f"{OPT25},1,10,CAD\n")
        self.assertEqual(rc, 0, rep)
        self.assertEqual(rep["rows"][0]["detail"], "")

    def test_wrong_quantity_still_caught(self):
        rc, rep, _ = self._rep(f"{OPT25},2,10,CAD\n")
        self.assertEqual(rc, 1)
        self.assertIn("quantity off by +1.0000", rep["rows"][0]["detail"])

    def test_later_expiry_is_still_open(self):
        later = "SYN260116C00001000.TO"
        with tempfile.TemporaryDirectory() as td:
            rc, rep, _ = _run(td, "symbol,quantity,proceeds,currency\n",
                              [_write("2025-01-02", 10.0, symbol=later)])
        self.assertEqual(rc, 0, rep)
        self.assertEqual(rep["rows"][0]["status"], "NO_SLIP_EXPECTED")

    def test_partly_bought_back_rest_expired(self):
        ents = [_write("2025-01-02", 10.0), _write("2025-02-03", 30.0),
                _buyback("2025-03-03", 5.0, {"2025": 10.0})]
        with tempfile.TemporaryDirectory() as td:
            rc, rep, _ = _run(td, HDR + f"{OPT25},2,40,5,CAD\n", ents)
        self.assertEqual(rc, 0, rep)


if __name__ == "__main__":
    unittest.main()
