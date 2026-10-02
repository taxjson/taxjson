"""Re-audit-2 errors (errA-d): one-line errors instead of tracebacks in the
checklist / watch state, close-year / handoff, the web helpers, export,
some parsers and taxjson-sum-income."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENV = dict(os.environ, TAXJSON_OFFLINE="1", PYTHONPATH=str(REPO / "src"),
           NO_COLOR="1")

TOML = ('[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        'option_grant_timing_since = 2025\n'
        '[accounts.margin]\ntype = "taxable"\n')


def tj(root, *args):
    return subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run",
                           "-C", str(root), *args], capture_output=True,
                          text=True, env=ENV, stdin=subprocess.DEVNULL)


def tool(module, *args, cwd=None, env=None):
    return subprocess.run([sys.executable, "-c",
                           "import sys; from taxjson.bin._entry import "
                           f"{module} as m; sys.exit(m())", *args],
                          capture_output=True, text=True, cwd=cwd,
                          env=env or ENV, stdin=subprocess.DEVNULL)


def no_tb(tc, r):
    tc.assertNotIn("Traceback", r.stderr, r.stderr[-2000:])


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self):
        for p in self.root.rglob("*"):
            try:
                if p.is_dir() and not p.is_symlink():
                    p.chmod(0o755)
                else:
                    p.chmod(0o644)
            except OSError:
                pass
        self._td.cleanup()


# ------------------------------------------------------------- checklist
class TestChecklistState(_Tmp):
    """A2-0768, A2-0789, A2-1393, A2-1414, A2-1430 (checklist half)."""

    def project(self):
        (self.root / "taxjson.toml").write_text(TOML)
        return self.root

    def test_state_is_directory_load_refused(self):
        from taxjson.lib import checklist as cl
        root = self.project()
        (root / cl.STATE_FILE).mkdir()
        with self.assertRaises(cl.StateFileError):
            cl.load_state(root)

    def test_state_symlink_loop_load_refused(self):
        from taxjson.lib import checklist as cl
        root = self.project()
        os.symlink(cl.STATE_FILE, root / cl.STATE_FILE)
        with self.assertRaises(cl.StateFileError):
            cl.load_state(root)

    def test_wrong_shape_override_entry_refused(self):
        from taxjson.lib import checklist as cl
        root = self.project()
        for bad in ('"x"', "7", "[1]", '{"status": 5}'):
            (root / cl.STATE_FILE).write_text(
                '{"overrides": {"elections": %s}}' % bad)
            with self.assertRaises(cl.StateFileError) as cm:
                cl.load_state(root)
            self.assertIn("elections", str(cm.exception))

    def test_bom_state_loads(self):
        """A2-0776: a hand-edited checklist.json saved with a BOM."""
        from taxjson.lib import checklist as cl
        root = self.project()
        (root / cl.STATE_FILE).write_bytes(
            b'\xef\xbb\xbf{"overrides": {"elections": {"status": "done"}}}')
        self.assertEqual(cl.load_state(root)["overrides"]["elections"]
                         ["status"], "done")

    def test_done_on_directory_one_line(self):
        from taxjson.lib import checklist as cl
        root = self.project()
        (root / cl.STATE_FILE).mkdir()
        r = tj(root, "checklist", "--done", "elections")
        no_tb(self, r)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("checklist.json", r.stderr)

    def test_reset_on_directory_one_line(self):
        from taxjson.lib import checklist as cl
        root = self.project()
        (root / cl.STATE_FILE).mkdir()
        r = tj(root, "checklist", "--reset")
        no_tb(self, r)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("checklist.json", r.stderr)

    @unittest.skipIf(os.geteuid() == 0, "root ignores permissions")
    def test_done_read_only_file_one_line_and_kept(self):
        from taxjson.lib import checklist as cl
        root = self.project()
        st = root / cl.STATE_FILE
        st.write_text('{"overrides": {}}\n')
        root.chmod(0o555)
        try:
            r = tj(root, "checklist", "--done", "elections")
        finally:
            root.chmod(0o755)
        no_tb(self, r)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("cannot write", r.stderr)
        self.assertEqual(st.read_text(), '{"overrides": {}}\n')
        self.assertEqual([p.name for p in root.iterdir()
                          if p.name.endswith(".part")], [])

    def test_save_failure_leaves_old_file_and_no_part(self):
        from taxjson.lib import checklist as cl
        root = self.project()
        st = root / cl.STATE_FILE
        st.write_text('{"overrides": {}}\n')
        state = cl.load_state(root)
        state["bad"] = object()          # json.dumps fails part-way
        with self.assertRaises((cl.StateFileError, TypeError)):
            cl.save_state(root, state, 2025)
        self.assertEqual(st.read_text(), '{"overrides": {}}\n')
        self.assertEqual([p.name for p in root.iterdir()
                          if p.name.endswith(".part")], [])


class TestWatchStateInner(unittest.TestCase):
    """A2-1430 (watch half): wrong-shape inner entries re-baseline."""

    def test_bad_inner_rebaselines(self):
        from taxjson.bin import taxjson_watch as w
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".watch_state.json"
            for radar, hn in (([1], None), ({"SHOP.TO": "x"}, None),
                              ({"SHOP.TO": {"category": 5}}, None),
                              ({}, "abc"), ({}, True)):
                doc = {"schema_version": w.STATE_VERSION, "as_of": "x",
                       "radar": radar}
                if hn is not None:
                    doc["harvest_now"] = hn
                p.write_text(json.dumps(doc))
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertIsNone(w.load_state(p), (radar, hn))
                self.assertIn("NEW baseline", err.getvalue())

    def test_good_state_kept(self):
        from taxjson.bin import taxjson_watch as w
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".watch_state.json"
            doc = {"schema_version": w.STATE_VERSION, "as_of": "x",
                   "radar": {"SHOP.TO": {"category": "LOCKED",
                                         "advisory": "a",
                                         "clears_at": "2025-01-01"}},
                   "harvest_now": -12.5}
            p.write_text(json.dumps(doc))
            self.assertEqual(w.load_state(p)["harvest_now"], -12.5)


# --------------------------------------------------- handoff / close-year
GOOD_LOCK = {
    "schema_version": 2, "record_version": 2, "year": 2024,
    "country": "canada", "date_basis": "settle", "closed_at": "2025-04-01",
    "dispositions": [{"account": "margin", "symbol": "XYZ.TO",
                      "date": "2024-05-05", "date_settle": "2024-05-06",
                      "qty": 10.0, "proceeds": 120.0, "cost": 100.0,
                      "gain": 20.0, "denied": 0.0}],
    "settle_next_year": [{"group": "equity", "account": "margin",
                          "symbol": "XYZ.TO", "date": "2024-12-31",
                          "date_settle": "2025-01-02", "qty": 5.0,
                          "net": -50.0}],
    "year_end": {"equity": {"XYZ.TO": {"qty": 5.0, "acb": 50.0,
                                       "deferred": 0.0}}},
    "boundary_rows": [],
}


class TestHandoffRecord(_Tmp):
    """A2-0803: field-level damage in the prior-year lock is one line."""

    def test_good_lock_validates(self):
        from taxjson.lib import handoff
        handoff.validate_record(json.loads(json.dumps(GOOD_LOCK)), "f")

    def test_wrong_field_shapes_refused(self):
        from taxjson.lib import handoff
        cases = [("dispositions", "x"), ("dispositions", 7),
                 ("dispositions", {"a": 1}), ("schema_version", "x"),
                 ("schema_version", [1]), ("settle_next_year", "x"),
                 ("settle_next_year", 7), ("year_end", [1]),
                 ("year", "x")]
        for k, v in cases:
            rec = json.loads(json.dumps(GOOD_LOCK))
            rec[k] = v
            with self.assertRaises(handoff.RecordError, msg=(k, v)) as cm:
                handoff.validate_record(rec, "filed/2024.json")
            self.assertIn("filed/2024.json", str(cm.exception))
        for lst, field in (("dispositions", None),
                           ("dispositions", "gain"),
                           ("dispositions", "symbol"),
                           ("settle_next_year", None),
                           ("settle_next_year", "symbol"),
                           ("settle_next_year", "qty")):
            for v in ("x", 7, None, [1], {"a": 1}, {}):
                rec = json.loads(json.dumps(GOOD_LOCK))
                if field is None:
                    if v == {}:
                        continue
                    rec[lst][0] = v
                else:
                    if field != "symbol" and v == 7:
                        continue
                    if field == "symbol" and v == "x":
                        continue
                    rec[lst][0][field] = v
                with self.assertRaises(handoff.RecordError,
                                       msg=(lst, field, v)):
                    handoff.validate_record(rec, "f")

    def test_bom_lock_loads(self):
        from taxjson.lib import handoff
        p = self.root / "2024.json"
        p.write_bytes(b"\xef\xbb\xbf" + json.dumps(GOOD_LOCK).encode())
        self.assertEqual(handoff.load_record(p)["year"], 2024)

    def test_handoff_cli_one_line_exit_2(self):
        (self.root / "taxjson.toml").write_text(TOML)
        (self.root / "filed").mkdir()
        rec = json.loads(json.dumps(GOOD_LOCK))
        rec["dispositions"] = "x"
        (self.root / "filed" / "2024.json").write_text(json.dumps(rec))
        r = tj(self.root, "handoff")
        no_tb(self, r)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("dispositions", r.stderr)


class TestFiledDispositionsCsv(_Tmp):
    """A2-0769, A2-1397: short rows, blank symbols, a directory."""

    def load(self, text):
        from taxjson.lib import handoff
        p = self.root / "filed.csv"
        p.write_text("symbol,date,qty,proceeds,cost,gain\n" + text)
        return handoff.load_filed_dispositions(p)

    def test_short_row(self):
        with self.assertRaisesRegex(ValueError, r"filed.csv:2: bad row"):
            self.load("AAPL.US,2024-05-14\n")

    def test_blank_symbol(self):
        with self.assertRaisesRegex(ValueError, r"filed.csv:2: .*blank"):
            self.load(",2024-05-14,1,2,3,4\n")

    def test_non_finite(self):
        with self.assertRaisesRegex(ValueError, r"filed.csv:2"):
            self.load("AAPL.US,2024-05-14,nan,nan,inf,-inf\n")

    def test_directory(self):
        from taxjson.lib import handoff
        (self.root / "d.csv").mkdir()
        with self.assertRaises(ValueError):
            handoff.load_filed_dispositions(self.root / "d.csv")

    def test_good_row(self):
        self.assertEqual(self.load("AAPL.US,2024-05-14,1,2,3,4\n")[0]
                         ["symbol"], "AAPL.US")


class TestHandoffGainsShape(_Tmp):
    """A2-1396 / A2-0794 (handoff part): a wrong-shape gains file."""

    def test_dispositions_and_check_refuse(self):
        from taxjson.lib import handoff
        p = self.root / "brk_gains.json"
        for bad in ("[]", '{"transactions": ["x"]}',
                    '{"transactions": 5}'):
            p.write_text(bad)
            if bad == "[]":
                # a bare list is a transaction book: no sales, no crash
                self.assertEqual(handoff.dispositions({"brk": p}, 2024), [])
                continue
            with self.assertRaises(handoff.BooksError):
                handoff.dispositions({"brk": p}, 2024)

    def test_rows_wrong_row_type(self):
        from taxjson.lib import handoff
        p = self.root / "m_base.json"
        p.write_text('{"transactions": [{"symbol": "A", "quantity": "x"}]}')
        with self.assertRaises(handoff.BooksError):
            handoff._rows(p)


# ------------------------------------------------------------------ web
class TestWebShapes(_Tmp):
    """A2-0787, A2-0807."""

    def test_context_accounts_not_a_table_one_line(self):
        from taxjson.web.context import ProjectContext
        for bad in ("accounts = 5\n", 'accounts = ["margin"]\n'):
            (self.root / "taxjson.toml").write_text(
                bad + '[settings]\nyear = 2025\ncountry = "canada"\n')
            with self.assertRaises(ValueError) as cm:
                ProjectContext.load(self.root)
            self.assertIn("[accounts] must be a table", str(cm.exception))

    def test_radar_staleness_dangling_book(self):
        from taxjson.web import data
        from taxjson.web.context import ProjectContext
        (self.root / "taxjson.toml").write_text(TOML)
        ctx = ProjectContext.load(self.root)
        ctx.reports.mkdir()
        ctx.cache.mkdir()
        (ctx.reports / "wash_radar_margin.json").write_text("{}")
        os.symlink("missing.json", ctx.cache / "old_base.json")
        self.assertIsNone(data.radar_staleness(ctx, "margin"))

    def test_freshness_dangling_sum(self):
        from taxjson.web import data
        from taxjson.web.context import ProjectContext
        (self.root / "taxjson.toml").write_text(TOML)
        ctx = ProjectContext.load(self.root)
        ctx.reports.mkdir()
        os.symlink("missing.sum", ctx.reports / "old.sum")
        data.freshness(ctx)


# --------------------------------------------------------------- export
def export(*args, cwd=None):
    return subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_export",
                           *args], capture_output=True, text=True, cwd=cwd,
                          env=ENV, stdin=subprocess.DEVNULL)


class TestExport(_Tmp):
    """A2-0806, A2-1410, A2-1442, A2-1443 (tv-map BOM), A2-1441."""

    def test_tv_map_bom_keeps_first_rule(self):
        g = self.root / "g.json"
        g.write_text('{"inventory":[{"symbol":"XYZ.TO","qty":10,'
                     '"total_cost":100.0,"currency":"CAD"}]}')
        m = self.root / "bom.map"
        m.write_bytes(b"\xef\xbb\xbfXYZ NEO\n")
        r = export("--tradingview", "--tv-map", str(m), str(g))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("NEO:XYZ", r.stdout)

    def test_holdings_toml_bad_quantity_refused(self):
        h = self.root / "h.toml"
        for bad in ('quantity = "abc"\ntotal_cost = 10.0',
                    'quantity = 5\ntotal_cost = "x"'):
            h.write_text('[meta]\nschema_version = 1\n[[holding]]\n'
                         'symbol = "XEI.TO"\n' + bad +
                         '\ncurrency = "CAD"\n')
            for mode in ("--report", "--seekingalpha", "--tradingview"):
                r = export(mode, str(h))
                no_tb(self, r)
                self.assertEqual(r.returncode, 2, (mode, r.stderr))
                self.assertIn("[[holding]] 1 (XEI.TO)", r.stderr)


if __name__ == "__main__":
    unittest.main()
