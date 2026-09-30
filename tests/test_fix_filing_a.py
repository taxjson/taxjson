"""Regression tests for the filing-a medium findings (filed-year lock,
reconcile-slips, config normalization, filing checklist). Synthetic
data only."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _write_toml(root: Path, settings: str, accounts: str) -> None:
    (root / "taxjson.toml").write_text(
        "[settings]\n" + settings + "\n" + accounts, encoding="utf-8")


_AGG = {"realized": 500.0, "disallowed": 0.0, "dispositions": 1,
        "income": 0.0, "tainted": 0, "proceeds": 1500.0, "st_gain": 0.0,
        "lt_gain": 0.0}


def _lock(root: Path, year: int, accounts, **extra) -> Path:
    d = root / "filed"
    d.mkdir(exist_ok=True)
    doc = {"schema_version": 1, "year": year, "country": "canada",
           "basis": "wash-adjusted", "accounts": accounts,
           "totals": {}, "tainted_counts_manual": True}
    doc.update(extra)
    p = d / f"{year}.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


class TestLockOptionTiming(unittest.TestCase):
    """R1-188: a locked year is recomputed with the option timing its
    lock recorded, not the current project's default."""

    def test_recompute_uses_lock_timing(self):
        from taxjson.bin import taxjson_filed as F
        seen = []

        def fake_run(cmd, outp):
            seen.append(list(cmd))
            Path(outp).write_text('{"transactions": []}')

        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "work"
            cache.mkdir()
            (cache / "margin_base.json").write_text('{"transactions": []}')
            settings = {"year": 2026, "country": "canada"}   # since unset
            F.recompute_accounts(
                cache, ["margin"], [], 2025, settings, "wash-adjusted",
                fake_run,
                option_timing={"option_premium_timing": "grant",
                               "option_grant_since": 2025,
                               "option_buyback_loss_superficial": False})
        cmd = seen[0]
        i = cmd.index("--option-grant-since")
        self.assertEqual(cmd[i + 1], "2025", cmd)

    def test_check_filed_passes_lock_timing(self):
        from taxjson.bin import taxjson_run as R
        from taxjson.bin import taxjson_filed as F
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_toml(root, 'year = 2026\ncountry = "canada"',
                        '[accounts.margin]\ntype = "taxable"\n')
            (root / "work").mkdir()
            (root / "work" / "margin_base.json").write_text("{}")
            timing = {"option_premium_timing": "grant",
                      "option_grant_since": 2025}
            _lock(root, 2025, {"margin": dict(_AGG)}, option_timing=timing)
            got = {}

            def fake(cache, eq, cr, year, settings, basis, run, **kw):
                got.update(kw)
                return {"margin": dict(_AGG)}
            with mock.patch.object(F, "recompute_accounts", fake), \
                    contextlib.redirect_stdout(io.StringIO()):
                n = R._check_filed_years(root, root / "work",
                                         {"year": 2026,
                                          "country": "canada"},
                                         strict=False)
        self.assertEqual(n, 0)
        self.assertEqual(got.get("option_timing"), timing)


class TestBadLock(unittest.TestCase):
    """R1-189: one unreadable lock is reported by name, the other locks
    are still checked, and --strict fails."""

    def _setup(self, root: Path, bad: str):
        _write_toml(root, 'year = 2025\ncountry = "canada"',
                    '[accounts.margin]\ntype = "taxable"\n')
        (root / "work").mkdir()
        (root / "work" / "margin_base.json").write_text("{}")
        _lock(root, 2025, {"margin": dict(_AGG)})
        (root / "filed" / "2021.json").write_text(bad, encoding="utf-8")

    def _check(self, root, strict):
        from taxjson.bin import taxjson_run as R
        from taxjson.bin import taxjson_filed as F
        drifted = dict(_AGG, realized=700.0)

        def fake(cache, eq, cr, year, settings, basis, run, **kw):
            return {"margin": dict(drifted)}
        err, out = io.StringIO(), io.StringIO()
        with mock.patch.object(F, "recompute_accounts", fake), \
                contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(out):
            try:
                n = R._check_filed_years(root, root / "work",
                                         {"year": 2025,
                                          "country": "canada"},
                                         strict=strict)
                code = None
            except SystemExit as e:
                n, code = None, e.code
        return n, code, err.getvalue(), out.getvalue()

    def test_missing_key_lock_does_not_hide_drift(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._setup(root, json.dumps({
                "year": 2021, "accounts": {"margin": {"realized": 1.0}}}))
            n, code, err, out = self._check(root, strict=True)
        self.assertIsNotNone(code, "strict must fail")
        self.assertIn("DRIFTED", err)
        self.assertIn("+200.00", err)

    def test_corrupt_json_lock_named_and_strict_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._setup(root, "{bad json")
            n, code, err, out = self._check(root, strict=True)
        self.assertIsNotNone(code)
        self.assertIn("2021.json", err)
        self.assertIn("DRIFTED", err)

    def test_diff_snapshot_missing_count_key_no_keyerror(self):
        from taxjson.bin.taxjson_filed import diff_snapshot
        lines = diff_snapshot({"accounts": {"m": {"realized": 500.0}}},
                              {"m": dict(_AGG)})
        self.assertEqual(lines, [])


class TestRenamedAccount(unittest.TestCase):
    """S002-06: a lock account that is no longer configured is not
    recomputed from its orphan work/<label>_base.json."""

    def test_orphan_book_not_blended(self):
        from taxjson.bin import taxjson_run as R
        from taxjson.bin import taxjson_filed as F
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_toml(root, 'year = 2025\ncountry = "canada"',
                        '[accounts.acctA]\ntype = "taxable"\n'
                        '[accounts.acctC]\ntype = "taxable"\n')
            w = root / "work"
            w.mkdir()
            for a in ("acctA", "acctB", "acctC"):
                (w / f"{a}_base.json").write_text("{}")
            _lock(root, 2025, {"acctA": dict(_AGG), "acctB": dict(_AGG)})
            seen = {}

            def fake(cache, eq, cr, year, settings, basis, run, **kw):
                seen["eq"] = list(eq)
                return {a: dict(_AGG) for a in eq}
            err = io.StringIO()
            with mock.patch.object(F, "recompute_accounts", fake), \
                    contextlib.redirect_stderr(err), \
                    contextlib.redirect_stdout(io.StringIO()):
                n = R._check_filed_years(root, w, {"year": 2025,
                                                   "country": "canada"},
                                         strict=False)
        self.assertNotIn("acctB", seen["eq"])
        self.assertEqual(n, 1)
        self.assertIn("acctB", err.getvalue())
        self.assertIn("taxjson.toml", err.getvalue())


class TestIncomeSplit(unittest.TestCase):
    """S032-06: a dividend reclassified as a payment in lieu drifts."""

    def test_dividend_pil_split(self):
        from taxjson.bin.taxjson_filed import (aggregates_from_gains,
                                               diff_snapshot)
        div = aggregates_from_gains({"transactions": [
            {"action": "DIVIDEND", "dividend": 2000.0, "pil": 0.0}]})
        pil = aggregates_from_gains({"transactions": [
            {"action": "DIVIDEND_IN_LIEU", "dividend": 0.0,
             "pil": 2000.0}]})
        self.assertEqual(div["income"], pil["income"])
        lines = diff_snapshot({"accounts": {"m": div}}, {"m": pil})
        self.assertTrue(any("pil" in l or "dividend" in l for l in lines),
                        lines)

    def test_old_lock_without_split_stays_clean(self):
        from taxjson.bin.taxjson_filed import (aggregates_from_gains,
                                               diff_snapshot)
        cur = aggregates_from_gains({"transactions": [
            {"action": "DIVIDEND", "dividend": 2000.0}]})
        old = {k: v for k, v in cur.items()
               if k not in ("dividend", "pil")}
        self.assertEqual(diff_snapshot({"accounts": {"m": old}},
                                       {"m": cur}), [])


class TestConfigNormalization(unittest.TestCase):
    """S031-21 / S031-24: tax_date and country are normalized (or
    refused) for every command, not only `run`."""

    def test_filed_recompute_normalizes_country_and_tax_date(self):
        from taxjson.bin import taxjson_filed as F
        seen = []

        def fake_run(cmd, outp):
            seen.append(list(cmd))
            Path(outp).write_text('{"transactions": []}')
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            (cache / "c_base.json").write_text('{"transactions": []}')
            F.recompute_year(cache, "c", 2025,
                             {"country": " Canada", "tax_date": "Settle",
                              "year": 2025}, "", fake_run)
        cmd = seen[0]
        self.assertEqual(cmd[cmd.index("--country") + 1], "canada")
        self.assertEqual(cmd[cmd.index("--tax-date") + 1], "settle")

    def test_load_config_canonical_country(self):
        from taxjson.bin import taxjson_run as R
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_toml(root, 'year = 2025\ncountry = "CA"',
                        '[accounts.m]\ntype = "taxable"\n')
            cfg = R.load_config(root)
        self.assertEqual(cfg["settings"]["country"], "canada")

    def test_load_config_refuses_bad_tax_date(self):
        from taxjson.bin import taxjson_run as R
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_toml(root, 'year = 2025\ntax_date = "Setle"',
                        '[accounts.m]\ntype = "taxable"\n')
            with self.assertRaises(SystemExit) as cm:
                R.load_config(root)
        self.assertIn("tax_date", str(cm.exception.code))

    def test_web_context_country(self):
        from taxjson.web.context import ProjectContext
        ctx = ProjectContext(root=Path("."), settings={"country": " CA "},
                             accounts=[])
        self.assertEqual(ctx.country, "canada")


class TestReconcileManualRows(unittest.TestCase):
    """R1-206: reconcile-slips folds phantom-basis (manual) sales back
    in instead of reporting them MISSING_FROM_COMPUTED."""

    def test_manual_rows_counted(self):
        from taxjson.bin.taxjson_reconcile_slips import load_computed
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "g.json"
            p.write_text(json.dumps({
                "transactions": [],
                "manual_reporting_required": [
                    {"symbol": "ZZZ.TO", "qty": -100, "proceeds": 500.0,
                     "date": "2025-06-20", "date_settle": "2025-06-23",
                     "account": "margin"}]}))
            out = load_computed([p], 2025)
        self.assertIn("ZZZ", " ".join(out))
        rec = next(v for k, v in out.items() if "ZZZ" in k)
        self.assertEqual(rec["tainted_rows"], 1)
        self.assertAlmostEqual(rec["proceeds_net"], 500.0)


if __name__ == "__main__":
    unittest.main()
