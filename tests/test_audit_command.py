"""taxjson-audit — the authoritative per-disposition justification.

The audit's value is its claims: every disposition joins to a parsed
broker row, the FX recomputation ties to the base books, and the
engine re-run ties to the pipeline's saved gains. These tests build a
tiny two-currency project end to end with the REAL tools (parse-less:
hand-written rows through merge2 and gains), then check both the happy
path and — more importantly — that the audit FAILS LOUDLY when the
saved gains or the base books disagree with what it derives.
"""
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal
from pathlib import Path

from taxjson.bin.taxjson_audit import (
    build_check_index, build_source_index, main as audit_main,
    rate_with_provenance, _map_note)


def _tx(**kw):
    from taxjson.lib.core import TaxTransaction
    base = dict(action="BUYSELL", date="2026-01-05", symbol="AAPL.US",
                quantity=10.0, currency="USD", price=100.0,
                net_amount=1000.0, account="margin",
                date_settle="2026-01-06")
    base.update(kw)
    return TaxTransaction(**base).to_dict()


def _write(dirp: Path, name: str, txs) -> Path:
    p = dirp / name
    p.write_text(json.dumps({"transactions": txs,
                             "metadata": {"input_files": [f"{name}.csv"]}}),
                 encoding="utf-8")
    return p


class _Project:
    """Source (USD) + base (CAD) + gains files for a buy/sell pair."""

    def __init__(self, tmp: Path, sell_price=120.0, rate=1.4):
        self.tmp = tmp
        buy = _tx(quantity=10.0, price=100.0, net_amount=1000.0,
                  date="2026-01-05", date_settle="2026-01-06")
        sell = _tx(quantity=-10.0, price=sell_price,
                   net_amount=10 * sell_price,
                   date="2026-02-10", date_settle="2026-02-11")
        self.source = _write(tmp, "src.json", [buy, sell])
        # Converted twins: same ids, CAD amounts (mirrors merge2).
        conv = []
        for t in (buy, sell):
            c = dict(t)
            for f in ("price", "net_amount", "proceeds", "gross_amount"):
                c[f] = float(c.get(f) or 0.0) * rate
            c["currency"] = "CAD"
            conv.append(c)
        self.base = _write(tmp, "base.json", conv)
        self.rates = tmp / "rates.csv"
        lines = []
        from datetime import date, timedelta
        d = date(2026, 1, 1)
        while d < date(2026, 4, 1):
            lines.append(f"{d.isoformat()} 12:00:00 USD CAD {rate}")
            d += timedelta(days=1)
        self.rates.write_text("\n".join(lines), encoding="utf-8")
        # The pipeline's saved gains: run the real engine on the base.
        from taxjson.lib.core import get_tax_rules, load_transactions
        rules = get_tax_rules("canada")
        res = rules.compute_gains(load_transactions(self.base))
        self.gains = tmp / "gains.json"
        self.gains.write_text(json.dumps(
            {"transactions": res["transactions"]}, default=str),
            encoding="utf-8")

    def run(self, extra=(), expect=0):
        argv = ["--country", "canada", "--year", "2026",
                "--base", str(self.base), "--rates", str(self.rates),
                "--base-currency", "CAD",
                "--source", str(self.source),
                "--check", str(self.gains)] + list(extra)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = audit_main(argv)
        text = out.getvalue()
        assert rc == expect, (rc, text, err.getvalue())
        return text


class TestHappyPath(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.p = _Project(Path(self._td.name))

    def tearDown(self):
        self._td.cleanup()

    def test_event_traces_source_fx_and_ties(self):
        text = self.p.run()
        self.assertIn("SOURCE", text)
        self.assertIn("src.json", text)
        self.assertIn("1,200.00 USD \u00d7 1.40000 = 1,680.00 CAD", text)
        self.assertIn("\u2713", text)
        self.assertIn("1/1 traced to a parsed broker row", text)
        self.assertIn("1 tied, 0 MISMATCHED, 0 not found", text)
        self.assertNotIn("FAILED", text)

    def test_rate_provenance_is_named(self):
        text = self.p.run()
        self.assertIn("exact rate for 2026-02-11", text)

    def test_summary_mode_is_one_line_per_event(self):
        text = self.p.run(["--summary"])
        events = [l for l in text.splitlines() if "AAPL.US" in l
                  and "gain=" in l]
        self.assertEqual(len(events), 1)

    def test_json_mode(self):
        text = self.p.run(["--json"])
        doc = json.loads(text)
        self.assertFalse(doc["failed"])
        self.assertEqual(len(doc["events"]), 1)
        ev = doc["events"][0]
        self.assertTrue(ev["fx"]["ties"])
        self.assertTrue(ev["tie_out"]["ties"])
        self.assertAlmostEqual(doc["total_gain"],
                               ev["gain"], places=2)


class TestFailuresAreLoud(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.p = _Project(Path(self._td.name))

    def tearDown(self):
        self._td.cleanup()

    def test_tampered_gains_file_fails_the_tie_out(self):
        doc = json.loads(self.p.gains.read_text())
        for t in doc["transactions"]:
            if t.get("qty") and "gain" in t:
                t["gain"] = float(t["gain"]) + 100.0
        self.p.gains.write_text(json.dumps(doc, default=str))
        text = self.p.run(expect=1)
        self.assertIn("PIPELINE TIE-OUT FAILED", text)
        self.assertIn("MISMATCHED", text)

    def test_tampered_base_book_fails_the_fx_check(self):
        # The engine consumes the tampered book too, so regenerate the
        # gains from it — isolating the FX check as the only failure.
        doc = json.loads(self.p.base.read_text())
        for t in doc["transactions"]:
            if float(t.get("quantity") or 0) < 0:
                t["net_amount"] = float(t["net_amount"]) + 7.77
        self.p.base.write_text(json.dumps(doc))
        from taxjson.lib.core import get_tax_rules, load_transactions
        res = get_tax_rules("canada").compute_gains(
            load_transactions(self.p.base))
        self.p.gains.write_text(json.dumps(
            {"transactions": res["transactions"]}, default=str))
        text = self.p.run(expect=1)
        self.assertIn("FX cross-check FAILED", text)

    def test_missing_source_row_is_warned_not_fatal(self):
        _write(Path(self._td.name), "src.json", [])   # empty the source
        text = self.p.run()
        self.assertIn("no parsed source row found", text)
        self.assertIn("0/1 traced", text)


class TestRateProvenance(unittest.TestCase):
    HIST = {"USD": {"2026-03-02": Decimal("1.41")}}

    def test_exact_carried_default(self):
        r, k, d = rate_with_provenance("USD", "2026-03-02", self.HIST,
                                       Decimal("1.35"))
        self.assertEqual((float(r), k, d), (1.41, "exact", "2026-03-02"))
        r, k, d = rate_with_provenance("USD", "2026-03-06", self.HIST,
                                       Decimal("1.35"))
        self.assertEqual((float(r), k, d), (1.41, "carried",
                                            "2026-03-02"))
        r, k, _ = rate_with_provenance("USD", "2026-03-09", self.HIST,
                                       Decimal("1.35"))
        self.assertEqual((float(r), k), (1.35, "default"))

    def test_agrees_with_the_converter_everywhere(self):
        # The audit's claim is that it re-derives the PIPELINE's rate:
        # the provenance walk must equal get_rate_for_date on every
        # date, including the fallback tails.
        from taxjson.bin.taxjson_convert_currency import get_rate_for_date
        from datetime import date, timedelta
        d = date(2026, 2, 20)
        while d < date(2026, 3, 20):
            ds = d.isoformat()
            mine, _k, _d = rate_with_provenance(
                "USD", ds, self.HIST, Decimal("1.35"))
            theirs = get_rate_for_date("USD", ds, self.HIST,
                                       Decimal("1.35"))
            self.assertEqual(mine, theirs, ds)
            d += timedelta(days=1)


class TestIndexing(unittest.TestCase):
    def test_map_note_names_the_rule(self):
        from collections import namedtuple
        TM = namedtuple("TM", ["glob", "tobase", "journal"])
        tmap = TM(glob={}, tobase={"BTG.US": "BTO.TO"}, journal={})
        self.assertIn("TOBASE", _map_note("BTG.US", "BTO.TO", tmap))
        self.assertEqual(_map_note("X.TO", "X.TO", tmap), "unchanged")
        # An unexplained rename still shows the rename.
        self.assertIn("->", _map_note("A.US", "B.TO", None))

    def test_check_index_skips_non_dispositions(self):
        with tempfile.TemporaryDirectory() as td:
            p = _write(Path(td), "g.json", [
                {"id": "a", "qty": 5, "gain": 1.0},
                {"id": "b", "action": "DIVIDEND"}])
            idx, labels = build_check_index([p])
            self.assertEqual(set(idx), {"a"})
            self.assertEqual(labels, ["g.json"])


class TestUsLotAggregation(unittest.TestCase):
    def test_multi_lot_sell_is_one_event_that_ties(self):
        # Two buy lots, one sell across both: US FIFO emits two lot
        # records with the SAME sell id; the audit must present one
        # event whose totals tie against the per-id aggregate.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            rows = [
                _tx(quantity=10.0, price=100.0, net_amount=1000.0,
                    currency="USD", date="2026-01-05",
                    date_settle="2026-01-06"),
                _tx(quantity=10.0, price=110.0, net_amount=1100.0,
                    currency="USD", date="2026-01-12",
                    date_settle="2026-01-13"),
                _tx(quantity=-20.0, price=130.0, net_amount=2600.0,
                    currency="USD", date="2026-03-10",
                    date_settle="2026-03-11"),
            ]
            source = _write(tmp, "src.json", rows)
            base = _write(tmp, "base.json", rows)   # base == USD here
            from taxjson.lib.core import get_tax_rules, load_transactions
            res = get_tax_rules("usa").compute_gains(
                load_transactions(base))
            lots = [t for t in res["transactions"]
                    if t.get("qty") and "gain" in t]
            self.assertEqual(len(lots), 2, "premise: two FIFO lots")
            self.assertEqual(len({t["id"] for t in lots}), 1)
            gains = tmp / "gains.json"
            gains.write_text(json.dumps(
                {"transactions": res["transactions"]}, default=str))
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                rc = audit_main(["--country", "usa", "--year", "2026",
                                 "--base", str(base),
                                 "--base-currency", "USD",
                                 "--source", str(source),
                                 "--check", str(gains)])
            text = out.getvalue()
            self.assertEqual(rc, 0, text)
            self.assertIn("EVENT 1/1", text)
            self.assertIn("SELL 20", text)
            self.assertIn("1 tied, 0 MISMATCHED", text)


class TestOrchestratorSourceDiscovery(unittest.TestCase):
    def test_derived_books_are_never_provenance(self):
        from taxjson.bin.taxjson_run import _audit_source_files
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            for n in ("margin_ib.json", "margin_ib_corp.json",
                      "margin_start_pos.json",
                      "margin_base.json", "margin_gains.json",
                      "margin_gains_wash.json", "margin_raw.json",
                      "margin_raw_base.json", "margin_report.json",
                      "margin_sorted.json", "margin_merged.json",
                      "margin_filled.json"):
                (cache / n).write_text("{}")
            got = {p.name for p in _audit_source_files(cache, "margin")}
            self.assertEqual(got, {"margin_ib.json",
                                   "margin_ib_corp.json",
                                   "margin_start_pos.json"})


if __name__ == "__main__":
    unittest.main()
