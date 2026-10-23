"""Batch C fixes (opening, sanity, holdings TOML, split view, export
coverage, period parsing): review findings 39, 44, 45, 50, 58, 63, 68.
Synthetic data and made-up tickers only."""
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from tax_rules.dual import cli

from taxjson.lib import export_coverage as EC
from taxjson.lib.corp_views import splits
from taxjson.lib.positions_reports import (PositionsReportError,
                                           read_positions)
from _style import CapturedWidth

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


REPO_ROOT = Path(__file__).resolve().parent.parent


def _row(action, day, sym, qty, **kw):
    d = {"action": action, "date": day, "time": "09:30:00", "symbol": sym,
         "quantity": qty, "net_amount": 0.0, "source": "U_2025.csv"}
    d.update(kw)
    return d


def _toml(path: Path, holdings, meta=None):
    out = ["[meta]", 'account = "U1234567"']
    for k, v in (meta or {}).items():
        out.append(f"{k} = {json.dumps(v)}")
    for h in holdings:
        out.append("\n[[holding]]")
        for k, v in h.items():
            out.append(f"{k} = {json.dumps(v)}")
    path.write_text("\n".join(out) + "\n")
    return path


class TestClosedLaterSplit(unittest.TestCase):
    """#39: a split in the gap scales the units left; a partial sale of
    the post-split shares is not a complete exit."""

    def test_split_then_partial_sale_still_open(self):
        later = [_row("SPLIT", "2025-02-03", "QQA.TO", 2.0),
                 _row("BUYSELL", "2025-03-03", "QQA.TO", -10)]
        self.assertEqual(EC.closed_later([("QQA.TO", 10.0)], later),
                         ([("QQA.TO", 10.0)], [], []))

    def test_split_then_full_sale_closed(self):
        later = [_row("SPLIT", "2025-02-03", "QQA.TO", 2.0),
                 _row("BUYSELL", "2025-03-03", "QQA.TO", -20)]
        self.assertEqual(EC.closed_later([("QQA.TO", 10.0)], later),
                         ([], [], ["QQA.TO"]))

    def test_rename_follows_the_new_symbol(self):
        later = [_row("SPLIT", "2025-02-03", "QQB.TO", 1.0,
                      symbol_new="QQC.TO"),
                 _row("BUYSELL", "2025-03-03", "QQC.TO", -10,
                      source="fix.tt")]
        self.assertEqual(EC.closed_later([("QQB.TO", 10.0)], later),
                         ([], ["QQB.TO"], []))


class TestOpeningQuantityExact(unittest.TestCase):
    """#44: an OPENING line carries the quantity exactly (no 8-digit
    rounding, no exponent)."""

    def test_large_and_fractional_quantities(self):
        from taxjson.bin.taxjson_run import _opening_lines
        with tempfile.TemporaryDirectory() as td:
            f = _toml(Path(td) / "pos.toml", [
                {"symbol": "QQD.TO", "quantity": 123456789,
                 "currency": "CAD", "total_cost": 100.0},
                {"symbol": "QQE.TO", "quantity": 12345.678912,
                 "currency": "CAD", "total_cost": 200.0}],
                meta={"as_of": "2024-12-31"})
            rep = read_positions(f)
        lines, skipped, _notes = _opening_lines(
            rep, country="canada", base_currency="CAD",
            snapshot="2024-12-31")
        self.assertEqual(skipped, [])
        self.assertEqual(lines, [
            "OPENING 2024-12-31 QQD.TO 123456789 CAD 100.00",
            "OPENING 2024-12-31 QQE.TO 12345.678912 CAD 200.00"])


class TestHoldingsTomlDates(unittest.TestCase):
    """#45: a digit-shaped date that is not a calendar day is refused,
    naming the field."""

    def test_impossible_as_of(self):
        with tempfile.TemporaryDirectory() as td:
            f = _toml(Path(td) / "pos.toml", [
                {"symbol": "QQF.TO", "quantity": 10}],
                meta={"as_of": "2025-02-30"})
            with self.assertRaises(PositionsReportError) as cm:
                read_positions(f)
            self.assertIn("as_of", str(cm.exception))
            self.assertIn("2025-02-30", str(cm.exception))

    def test_impossible_acquired(self):
        with tempfile.TemporaryDirectory() as td:
            f = _toml(Path(td) / "pos.toml", [
                {"symbol": "QQF.TO", "quantity": 10,
                 "acquired": "2024-13-99"}],
                meta={"as_of": "2025-02-28"})
            with self.assertRaises(PositionsReportError) as cm:
                read_positions(f)
            self.assertIn("acquired", str(cm.exception))

    def test_a_real_date_still_reads(self):
        with tempfile.TemporaryDirectory() as td:
            f = _toml(Path(td) / "pos.toml", [
                {"symbol": "QQF.TO", "quantity": 10,
                 "acquired": "2024-02-29"}],
                meta={"as_of": "2025-02-28"})
            rep = read_positions(f)
        self.assertEqual(rep.as_of, "2025-02-28")
        self.assertEqual(rep.rows[0].lot_date, "2024-02-29")

    def test_generated_at_free_text_is_ignored_but_an_impossible_day_refused(self):
        # generated_at only notes when the file was made: text that is no
        # date is ignored as before; a date-shaped impossible day is not.
        with tempfile.TemporaryDirectory() as td:
            f = _toml(Path(td) / "pos.toml", [
                {"symbol": "QQF.TO", "quantity": 10}],
                meta={"generated_at": "by hand"})
            self.assertEqual(read_positions(f).as_of, "")
            f = _toml(Path(td) / "pos2.toml", [
                {"symbol": "QQF.TO", "quantity": 10}],
                meta={"generated_at": "2025-02-30 10:00:00"})
            with self.assertRaises(PositionsReportError) as cm:
                read_positions(f)
            self.assertIn("generated_at", str(cm.exception))
            f = _toml(Path(td) / "pos3.toml", [
                {"symbol": "QQF.TO", "quantity": 10}],
                meta={"as_of": "soon"})
            with self.assertRaises(PositionsReportError):
                read_positions(f)


def _sanity_project(td: Path) -> Path:
    root = td / "p"
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        'option_grant_timing_since = 2025\n'
        '[accounts.margin]\ntype = "taxable"\n')
    (root / "inputs" / "margin" / "h.tt").write_text(
        "OPENING 2025-03-03 QQG.TO 10 CAD 100.00\n"
        "BUYSELL 2025-04-01 09:30:00 QQG.TO -10 CAD 12 120 0\n")
    r = cli(root, "run", "--no-input")
    assert r.returncode == 0, r.stderr[-2000:]
    return root


class TestSanityTomlDate(unittest.TestCase):
    """#45 / #50 end to end: the sanity date is the file's own, an
    impossible one is refused, and a malformed cost leaves the date."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.td = Path(cls._td.name)
        cls.root = _sanity_project(cls.td)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_impossible_as_of_refused(self):
        f = _toml(self.td / "bad_date.toml", [
            {"symbol": "QQG.TO", "quantity": 10, "currency": "CAD",
             "total_cost": 100.0}], meta={"as_of": "2025-03-32"})
        r = cli(self.root, "sanity", f"margin={f}", "--json")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("as_of", r.stderr)
        self.assertIn("2025-03-32", r.stderr)

    def test_malformed_cost_keeps_the_date(self):
        f = _toml(self.td / "bad_cost.toml", [
            {"symbol": "QQG.TO", "quantity": 10, "currency": "CAD",
             "total_cost": "oops"}], meta={"as_of": "2025-03-15"})
        r = cli(self.root, "sanity", f"margin={f}", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["groups"][0]["as_of"], "2025-03-15")
        self.assertTrue(doc["clean"])
        self.assertIn("total_cost", r.stderr)
        self.assertIn("cost is not compared", r.stderr)


class TestSplitViewRename(unittest.TestCase):
    """#58: shares received through a ticker change are held before the
    new symbol's next split."""

    def test_rename_then_split(self):
        rows = [_row("BUYSELL", "2024-01-02", "QQH.TO", 10),
                _row("SPLIT", "2024-03-01", "QQH.TO", 1.0,
                     symbol_new="QQI.TO"),
                _row("SPLIT", "2024-06-03", "QQI.TO", 2.0)]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            (root / "work" / "m_base.json").write_text(json.dumps(rows))
            cfg = {"settings": {"base_currency": "CAD",
                                "country": "canada"},
                   "accounts": {"m": {"type": "taxable"}}}
            items = splits(root, cfg)
        rename, split = items
        self.assertEqual((rename["held_before"], rename["held_after"]),
                         (10, 10))
        self.assertEqual((split["symbol"], split["held_before"],
                          split["held_after"]), ("QQI.TO", 10, 20))
        self.assertNotIn("nothing held", " ".join(split["why"]))


@unittest.skipIf(tomllib is None,
                 "no TOML reader (Python < 3.11 without `tomli`)")
class TestHoldingsTomlMultiplier(unittest.TestCase):
    """#63: a holdings TOML read back keeps its contract_multiplier."""

    TOML = ('[[holding]]\nsymbol = "QQJ250620C00010000.US"\n'
            'quantity = 1.0\ncurrency = "USD"\ntotal_cost = 20.0\n'
            'contract_multiplier = 10\n')

    def test_adapter_carries_it(self):
        from taxjson.bin.taxjson_export import (
            _holdings_toml_to_inventory, contract_multiplier_of)
        inv = _holdings_toml_to_inventory(tomllib.loads(self.TOML))
        item, = inv["inventory"]
        self.assertEqual(item["multiplier"], 10)
        self.assertEqual(contract_multiplier_of(item["symbol"], item), 10.0)

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "h.toml"
            p.write_text(self.TOML)
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_export",
                 "--holdings-toml", str(p)],
                cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        h, = tomllib.loads(r.stdout)["holding"]
        self.assertEqual(h["contract_multiplier"], 10)


class TestPeriodCutoffClamp(unittest.TestCase):
    """#68: a look-back reaching before year 1 means all history."""

    def test_months_and_years_clamp(self):
        from taxjson.bin.taxjson_run import _tx_period_cutoff
        y = date.today().year
        self.assertEqual(_tx_period_cutoff(f"{y * 12 - 1}m"), date.min)
        self.assertEqual(_tx_period_cutoff(f"{y * 12 + 500}m"), date.min)
        self.assertEqual(_tx_period_cutoff(f"{y}y"), date.min)
        self.assertEqual(_tx_period_cutoff("12m").year, y - 1)


if __name__ == "__main__":
    unittest.main()
