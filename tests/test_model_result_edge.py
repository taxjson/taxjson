"""Step 6c(4): edge_cases reads gains rows through typed views.

lib/edge_cases (`tjs edge-cases`) reads its gains rows' units and money
through model/result views over the dicts its Book keeps (`GainRow(g)`,
a year-end pool's `Holding(h)`). Pinned as 6c(3) pinned the export
group (test_model_result_export): `analyze` runs twice over each golden
book's recorded work/ files, and over runs of the golden and fuzz books
written as a golden project's gains file, with the views and with a raw
reader in their place, so a typed read gives what the raw read gave; which key a
site reads is held by the golden books' edge-cases views. The golden
and fuzz projects list no loss, so the lock runs the feature tests'
projects too (test_edge_cases, rules.ca.rpt.test_edge_cases_deferred_losses_into_next_year). `_raw_gain`
keeps the old read's rule: `raw_gain`, else (no key) `gain`, a null
`raw_gain` 0; the listed loss is that figure (TJ-2922). The two other
row lists the report reads go through the shared row checks too
(json_input read_work_doc), so a view never sees an integer too large
for a float.

missing_history's gains-row reads copy a value into its own records as
held (`'qty': t.get('qty')`): a typed read would write an int as a
float, so they stay."""
import json
import re
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from taxjson.lib import edge_cases as EC
from taxjson.model.result import GainRow
from taxjson.pipeline.config import load_config
from _support.ca_edge_cases import DENIED, _edge
from _support.view_lock import _both, _docs, _main_year
import _paths

GOLDEN = _paths.TESTS / "golden"
BOOKS = (("demo", "2024"), ("style-canada", None), ("style-usa", None))


def _project(td: str, book: str, run_dir):
    """A golden book's project with its recorded work/ beside it."""
    root = Path(td) / "p"
    shutil.copytree(GOLDEN / book / "project", root,
                    ignore=shutil.ignore_patterns("work", "reports", "exports"))
    year = root / run_dir if run_dir else root
    exp = GOLDEN / book / "expected" / (run_dir or "") / "work"
    shutil.copytree(exp, year / "work")
    return year


class TestAnalyze(unittest.TestCase):
    def test_the_golden_books(self):
        n = 0
        for book, run_dir in BOOKS:
            with tempfile.TemporaryDirectory() as td:
                year = _project(td, book, run_dir)
                cfg = load_config(year)
                n += len(EC.Book(year, cfg).gains)
                for margin in (0, 3, 30):
                    with self.subTest(book, margin=margin):
                        new, old = _both(EC, ["GainRow", "Holding"],
                                         lambda: EC.analyze(year, cfg, margin=margin))
                        self.assertEqual(new[0], "value", new)
                        self.assertEqual(new, old)
                        def text(margin=margin):
                            return EC.render_text(EC.analyze(year, cfg, margin=margin))
                        self.assertEqual(*_both(EC, ["GainRow", "Holding"], text))
        self.assertGreater(n, 30)

    def test_runs_of_the_golden_and_fuzz_books(self):
        """Each run as the taxable `margin` account's gains file of a golden
        project of its country, the project's year the rows' own."""
        n = 0
        for template, country in (("style-canada", "canada"), ("style-usa", "usa")):
            runs = [(name, doc) for name, doc in _docs()
                    if not name.startswith("golden/") and name.endswith(f" {country}")]
            self.assertGreater(len(runs), 40)
            with tempfile.TemporaryDirectory() as td:
                year = _project(td, template, None)
                toml = (year / "taxjson.toml").read_text(encoding="utf-8")
                for name, doc in runs:
                    y = _main_year(doc)
                    if y is None:
                        continue
                    (year / "taxjson.toml").write_text(
                        re.sub(r"(?m)^year(\s*)= \d{4}", rf"year\g<1>= {y}", toml, count=1), encoding="utf-8")
                    for f in (year / "work").glob("*_gains*.json"):
                        f.unlink()
                    (year / "work" / "margin_gains.json").write_text(json.dumps(doc), encoding="utf-8")
                    cfg = load_config(year)
                    n += len(EC.Book(year, cfg).gains)
                    with self.subTest(name):
                        new, old = _both(EC, ["GainRow", "Holding"], lambda: EC.analyze(year, cfg, margin=5))
                        self.assertEqual(new[0], "value", new)
                        self.assertEqual(new, old)
        self.assertGreater(n, 500)


def _listed(edge):
    """The raw_loss of every loss the analysis lists."""
    out = []
    def walk(x):
        if isinstance(x, dict):
            if "raw_loss" in x:
                out.append(x["raw_loss"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(edge)
    return out


class TestTheFeatureProjects(unittest.TestCase):
    """The golden and fuzz projects list no loss (no window across a year
    end, at a window's edge or holding a call), so the lock runs the
    feature tests' projects too, which list them (TJ-2928)."""

    def test_the_synthetic_project(self):
        from _support.ca_edge_work_files import _EdgeProject
        t = _EdgeProject("setUp")
        t.setUp()
        self.addCleanup(t.tearDown)
        listed = 0
        for basis in ("settle", "trade"):
            cfg = json.loads(json.dumps(_EdgeProject.CFG))
            cfg["settings"]["tax_date"] = basis
            for margin in (0, 3, 30):
                with self.subTest(basis=basis, margin=margin):
                    def run(margin=margin, cfg=cfg):
                        edge = EC.analyze(t.root, cfg, margin=margin)
                        return edge, EC.render_text(edge, details=True)
                    new, old = _both(EC, ["GainRow", "Holding"], run)
                    self.assertEqual(new[0], "value", new)
                    self.assertEqual(new, old)
                    listed += len(_listed(EC.analyze(t.root, cfg, margin=margin)))
        self.assertGreater(listed, 6)

    def test_the_engine_backed_projects(self):
        from _support.ca_edge_cases import _CALL_BOOK, _CALL_RRSP
        cases = [({"margin": DENIED}, (), {}, {}),
                 ({"margin": _CALL_BOOK}, _CALL_RRSP,
                  {"option_premium_timing": "grant", "option_buyback_loss_superficial": True,
                   "option_grant_timing_since": 2025},
                  {"option_premium_timing": "grant", "option_buyback_loss_superficial": True,
                   "option_grant_since": 2025}),
                 ({"margin": _CALL_BOOK}, _CALL_RRSP,
                  {"option_premium_timing": "close", "option_buyback_loss_superficial": False,
                   "option_grant_timing_since": 2025},
                  {"option_premium_timing": "close", "option_buyback_loss_superficial": False})]
        listed = 0
        for i, (books, sheltered, settings, req) in enumerate(cases):
            with self.subTest(case=i):
                def run(books=books, sheltered=sheltered, settings=settings, req=req):
                    return _edge(books, sheltered, settings=settings, **req)[1:]
                new, old = _both(EC, ["GainRow", "Holding"], run)
                self.assertEqual(new[0], "value", new)
                self.assertEqual(new, old)
                listed += len(_listed(_edge(books, sheltered, settings=settings, **req)[1]))
        self.assertGreaterEqual(listed, 2)


class TestTheListedLoss(unittest.TestCase):
    """TJ-2922: a loss row with no `raw_gain` (an older or hand-written
    file) is listed with its gain, the figure that selected it, not as a
    loss of 0.00."""

    def test_a_row_without_raw_gain(self):
        from _support.ca_edge_work_files import _EdgeProject
        t = _EdgeProject("setUp")
        t.setUp()
        self.addCleanup(t.tearDown)
        f = t.root / "work" / "margin_gains_wash.json"
        doc = json.loads(f.read_text(encoding="utf-8"))
        for r in doc["transactions"]:
            r["gain"] = r.pop("raw_gain")
            r["disallowed_amount"] = r["permanently_disallowed"] = 0.0
        f.write_text(json.dumps(doc), encoding="utf-8")
        edge = EC.analyze(t.root, _EdgeProject.CFG)
        listed = {(r["symbol"], r["raw_loss"]) for r in edge["window_edges"] + edge["calls_in_windows"]}
        self.assertEqual(listed, {("ABC.TO", -300.0), ("DEF.TO", -40.0)})


class TestUsAllowedWhy(unittest.TestCase):
    """The units of each lot a US sale sold (its rows' `qty`, by
    acquisition date) against the purchases in its loss window: a wrong
    key in that sum passed every golden book."""

    class _Book:
        def __init__(self, gains):
            self.gains = gains

        def wdate(self, r):
            return date.fromisoformat(r["date"])

    def test_the_lot_sold_counts_its_units(self):
        acq = {"_acct": "a", "date": "2025-01-02", "quantity": 10.0, "id": "B1"}
        for qty, verdict in ((-10.0, "lot_sold"), (-4.0, "unknown"), (10.0, "lot_sold")):
            with self.subTest(qty=qty):
                g = {"id": "S1", "_acct": "a", "symbol": "ZZQ.US", "qty": qty, "proceeds": 100.0,
                     "acquired_date": "2025-01-02", "date": "2025-01-20"}
                self.assertEqual(EC._us_allowed_why(self._Book([g]), g, [acq])[0], verdict)


class TestRawGain(unittest.TestCase):
    def test_the_old_reads_rule(self):
        def old(g):
            return float(g.get("raw_gain", g.get("gain") or 0) or 0)
        rows = [{}, {"gain": -5.0}, {"raw_gain": -7.5, "gain": -5.0}, {"raw_gain": None, "gain": -5.0},
                {"raw_gain": 0.0, "gain": -5.0}, {"gain": None}, {"raw_gain": -1, "gain": 2},
                {"raw_gain": -0.0}, {"gain": 1e16}, {"raw_gain": 5e-324}]
        for g in rows:
            with self.subTest(g=g):
                self.assertEqual(repr(EC._raw_gain(GainRow(g))), repr(old(g)))   # -0.0 and 0.0 apart


class TestTheFunnel(unittest.TestCase):
    def test_the_other_row_lists_are_checked(self):
        row = {"symbol": "ZZQ.US", "date": "2025-03-04", "qty": 2.0, "gain": -1.0}
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a_gains.json"
            for key in ("covers_realized_next_year", "inventory_year_end"):
                for bad, why in ((10 ** 400, "qty is an integer of 401 digits"),
                                 ("abc", "qty is 'abc', not a number")):
                    with self.subTest(key=key, bad=str(bad)[:5]):
                        p.write_text(json.dumps({"transactions": [row], "inventory": [], "summary": {},
                                                 key: [dict(row, qty=bad)]}), encoding="utf-8")
                        with self.assertRaises(ValueError) as cm:
                            EC._work_doc(p)
                        self.assertIn(f'"{key}" row 0 (ZZQ.US): {why}', str(cm.exception))
                        self.assertEqual(str(cm.exception).count("re-run `tjs run`"), 1)

    def _doc(self, p, **parts):
        p.write_text(json.dumps(dict({"transactions": [], "inventory": [], "summary": {}}, **parts)),
                     encoding="utf-8")
        return EC._work_doc(p)

    def test_the_lists_are_lists_of_objects(self):
        # In the shared reader (json_input read_work_doc), so every reader
        # of the file refuses them (TJ-2910, TJ-2919, TJ-2920); an older
        # file has no year end at all.
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a_gains.json"
            for key in ("covers_realized_next_year", "inventory_year_end"):
                for bad, why in (({"not": "a list"}, ""), (5, ""), (["x"], " (row 0 is a string)")):
                    with self.subTest(key=key, bad=bad), self.assertRaises(ValueError) as cm:
                        self._doc(p, **{key: bad})
                    self.assertIn(f'"{key}" must be a list of JSON objects{why}', str(cm.exception))
                    self.assertEqual(str(cm.exception).count("re-run `tjs run`"), 1)
                self.assertIsNone(self._doc(p, **{key: None})[key])
                self.assertNotIn(key, self._doc(p))

    def test_a_year_end_deferred_wash_is_checked(self):
        row = {"symbol": "ZZQ.US", "qty": 2.0, "total_cost": 10.0}
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "a_gains.json"
            for bad, why in ((10 ** 400, "deferred_wash is an integer of 401 digits"),
                             ("abc", "deferred_wash is 'abc', not a number")):
                with self.subTest(bad=str(bad)[:5]), self.assertRaises(ValueError) as cm:
                    self._doc(p, inventory_year_end=[dict(row, deferred_wash=bad)])
                self.assertIn(f'"inventory_year_end" row 0 (ZZQ.US): {why}', str(cm.exception))


if __name__ == "__main__":
    unittest.main()
