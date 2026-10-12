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
            _write_toml(root, 'year = 2025\ncountry = "canada"\n'
                              'tax_date = "Setle"',
                        '[accounts.m]\ntype = "taxable"\n')
            with self.assertRaises(SystemExit) as cm:
                R.load_config(root)
        self.assertIn("tax_date", str(cm.exception.code))


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


# ------------------------------------------------------------ checklist
from datetime import date as _date  # noqa: E402


class _Sub:
    def __init__(self, table=None):
        self.table = table or {}
        self.calls = []

    def __call__(self, argv, timeout=900):
        self.calls.append(list(argv))
        return self.table.get(argv[0], (0, "", ""))


_CL_TOML = ('[settings]\nyear = 2026\ncountry = "canada"\n'
            '[accounts.margin]\ntype = "taxable"\n'
            '[accounts.rrsp]\ntype = "sheltered"\n')


def _cl_project(root: Path, toml: str = _CL_TOML):
    from taxjson.bin import taxjson_run as R
    (root / "taxjson.toml").write_text(toml)
    for a in ("margin", "rrsp"):
        (root / "inputs" / a).mkdir(parents=True)
        (root / "inputs" / a / f"{a}.csv").write_text("x\n")
    (root / "work").mkdir()
    (root / "reports").mkdir()
    for a in ("margin", "rrsp"):
        (root / "reports" / f"{a}.sum").write_text(
            "DIAGNOSTICS\nvalidation: 0 error(s)\n")
    return R.load_config(root)


def _cl_ctx(root, cfg, table=None, year=2026):
    from taxjson.lib import checklist as cl
    return cl.Ctx(root=root, cfg=cfg, year=year, today=_date(2026, 9, 29),
                  run_sub=_Sub(table))


class TestChecklistWash(unittest.TestCase):
    """R1-158: wash-reviewed reads the year on the settle basis."""

    def test_december_trade_january_settle_denial(self):
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = _cl_project(root)
            (root / "work" / "margin_gains_wash.json").write_text(json.dumps(
                {"transactions": [{"date": "2025-12-31",
                                   "date_settle": "2026-01-02",
                                   "disallowed_amount": 1000.0,
                                   "permanently_disallowed": 1000.0}]}))
            r = cl.d_wash_reviewed(_cl_ctx(root, cfg))
        self.assertEqual(r.status, "manual", r.detail)
        self.assertIn("1,000.00 permanently denied", r.detail)


class TestChecklistRunClean(unittest.TestCase):
    """R1-249 / S067-07 / S018-02: run-clean sees root inputs, content
    changes, deletions and accounts the last run did not finish."""

    def _clean(self, root, cfg):
        from taxjson.lib import checklist as cl
        import os
        import time
        # reports newer than every input
        t = time.time() + 5
        for s in (root / "reports").glob("*.sum"):
            os.utime(s, (t, t))
        return cl.d_run_clean(_cl_ctx(root, cfg))

    def test_baseline_done(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = _cl_project(root)
            self.assertEqual(self._clean(root, cfg).status, "done")

    def test_distributions_map_added(self):
        from taxjson.lib import checklist as cl
        import os
        import time
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = _cl_project(root)
            self._clean(root, cfg)
            # [[distributions]] in taxjson.toml (once distributions.map)
            # are a run input.
            m = root / "taxjson.toml"
            with m.open("a") as f:
                f.write('\n[[distributions]]\nsymbol = "XYZ.TO"\n'
                        'record_date = 2026-03-01\nper_share = -1.00\n')
            t = time.time() + 60
            os.utime(m, (t, t))
            r = cl.d_run_clean(_cl_ctx(root, cfg))
        self.assertEqual(r.status, "attention", r.detail)

    def test_fingerprint_sees_deletion_and_old_mtime_copy(self):
        from taxjson.lib import checklist as cl
        import os
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = _cl_project(root)
            (root / "inputs" / "margin" / "extra.csv").write_text("y\n")
            cl.record_input_fingerprint(root, cfg)
            self.assertEqual(self._clean(root, cfg).status, "done")
            (root / "inputs" / "margin" / "extra.csv").unlink()
            r = cl.d_run_clean(_cl_ctx(root, cfg))
            self.assertEqual(r.status, "attention", r.detail)
            self.assertIn("extra.csv", r.detail)
            (root / "inputs" / "margin" / "extra.csv").write_text("y\n")
            self.assertEqual(self._clean(root, cfg).status, "done")
            f = root / "inputs" / "margin" / "margin.csv"
            f.write_text("corrected\n")
            os.utime(f, (1_000_000_000, 1_000_000_000))   # cp -p, old mtime
            r = cl.d_run_clean(_cl_ctx(root, cfg))
            self.assertEqual(r.status, "attention", r.detail)
            self.assertIn("margin.csv", r.detail)

    def test_account_without_report(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = _cl_project(root)
            (root / "reports" / "rrsp.sum").unlink()
            r = self._clean(root, cfg)
        self.assertEqual(r.status, "attention", r.detail)
        self.assertIn("rrsp", r.detail)


class TestChecklistState(unittest.TestCase):
    def test_year_bump_does_not_resurrect_marks(self):
        """R1-254."""
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cl.set_override(root, 2024, "t5008", "skipped", "2024 slips")
            cl.set_override(root, 2024, "t5-t3", "done")
            cl.set_override(root, 2025, "noa", "done")
            st = json.loads((root / cl.STATE_FILE).read_text())
        self.assertEqual(st["year"], 2025)
        self.assertEqual(sorted(st["overrides"]), ["noa"])

    def test_corrupt_state_is_not_overwritten(self):
        """S068-07."""
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cl.set_override(root, 2025, "noa", "done", "NOA ok")
            p = root / cl.STATE_FILE
            p.write_text(p.read_text().replace("{", "{,", 1))
            before = p.read_text()
            with self.assertRaises(cl.StateFileError) as cm:
                cl.set_override(root, 2025, "estimate", "done")
            self.assertIn(cl.STATE_FILE, str(cm.exception))
            self.assertEqual(p.read_text(), before)
            with self.assertRaises(cl.StateFileError):
                cl.load_state(root)

    def test_blocked_outranks_done_mark(self):
        """S068-13."""
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = _cl_project(root)
            cl.set_override(root, 2026, "audit", "done", "ok")
            ctx = _cl_ctx(root, cfg, {"audit": (1, "", "no gains files")})
            res = cl.evaluate(ctx, only=["audit"])
        r = res[0]
        self.assertEqual(r.status, "blocked")
        self.assertEqual(r.effective, "blocked")
        self.assertFalse(r.passed)
        self.assertTrue(r.finding)
        self.assertFalse(cl.to_json(res, 2026, "canada")["all_passed"])


class TestChecklistSlips(unittest.TestCase):
    def test_upper_case_slip_found(self):
        """S067-21."""
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "inputs" / "slips").mkdir(parents=True)
            (root / "inputs" / "slips" / "t5008_a.csv").write_text("x")
            (root / "inputs" / "slips" / "T5008_B.CSV").write_text("x")
            names = [p.name for p in cl.slip_files(root)]
        self.assertEqual(sorted(names), ["T5008_B.CSV", "t5008_a.csv"])

    def test_account_export_with_1099_in_name_is_not_a_slip(self):
        """S067-23."""
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "margin" /
             "questrade_acct_55510990.csv").write_text("x")   # pii-ok
            self.assertEqual(cl.slip_files(root), [])

    def test_all_slip_files_reconciled_together(self):
        """R1-207: one reconcile-slips call over every slip file."""
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = _cl_project(root)
            (root / "inputs" / "slips").mkdir()
            for n in ("ib_t5008.csv", "rbc_t5008.csv"):
                (root / "inputs" / "slips" / n).write_text("x")
            ctx = _cl_ctx(root, cfg)
            r = cl.d_t5008(ctx)
            calls = [c for c in ctx.run_sub.calls
                     if c[0] == "reconcile-slips"]
        self.assertEqual(len(calls), 1, calls)
        self.assertTrue(any(c.endswith("ib_t5008.csv") for c in calls[0]))
        self.assertTrue(any(c.endswith("rbc_t5008.csv") for c in calls[0]))
        self.assertEqual(r.status, "done")


class TestChecklistFormExportTolerance(unittest.TestCase):
    """R1-280: per-row rounding over many Schedule 3 rows is not a
    finding; a real gap still is."""

    def _run(self, gain, realized, rows):
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = _cl_project(root)
            fe = {"totals": {"proceeds_all": 1000.0, "gain_all": gain},
                  "rows": [{}] * rows}
            sm = {"filing": {"totals": {"proceeds": 1000.0, "gain": gain}},
                  "accounts": [{"account": "margin", "realized": realized}]}
            ctx = _cl_ctx(root, cfg, {"form-export": (0, json.dumps(fe), ""),
                                      "sum": (0, json.dumps(sm), "")})
            return cl.d_form_export(ctx)

    def test_rounding_residual_many_rows(self):
        self.assertEqual(self._run(403224.55, 403224.61, 831).status, "done")

    def test_real_gap(self):
        self.assertEqual(self._run(403224.55, 403229.61, 831).status,
                         "attention")

    def test_small_book_keeps_tight_tolerance(self):
        self.assertEqual(self._run(100.00, 100.06, 1).status, "attention")


class TestChecklistWording(unittest.TestCase):
    def test_fees_step_does_not_send_commissions_to_22100(self):
        """S023-21 / S066-14 / S078-00."""
        from taxjson.lib import checklist as cl
        sid, _st, title, cmd, why = cl.step_meta("fees", "canada")
        self.assertNotIn("data subscriptions are deductible", why)
        self.assertIn("commission", why.lower())
        self.assertIn("interest", (title + why).lower())
        self.assertNotEqual(cmd.strip(), "taxjson fees")

    def test_carryover_units(self):
        """R1-203 / S066-16."""
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = _cl_project(root)
            r = cl.d_carryover(_cl_ctx(root, cfg))
            self.assertIn("100%", r.detail)
            us = dict(cfg, settings=dict(cfg["settings"], country="usa"))
            r = cl.d_carryover(_cl_ctx(root, us))
            self.assertNotIn("25300", r.detail)
            self.assertIn("line 21", r.detail)
        title = cl.step_meta("carryover", "usa")[2]
        self.assertNotIn("lines 6 / 14", title)


class TestAccountTypeValidatedForChecklist(unittest.TestCase):
    """S018-01 (already fixed on main by R1-268): the checklist's config
    reader refuses an invalid account type instead of dropping it."""

    def test_taxable_capitalized_refused(self):
        from taxjson.bin import taxjson_run as R
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                '[accounts.cash]\ntype = "Taxable"\n')
            with self.assertRaises(SystemExit):
                R.load_config(root)


if __name__ == "__main__":
    unittest.main()
