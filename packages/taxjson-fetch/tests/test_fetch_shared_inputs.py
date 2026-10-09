"""taxjson fetch in a year folder whose exports are shared by every year
(`[settings] inputs_dir`): downloads go to the shared folder the run
reads (with a note), `--positions` to the year's holdings folder.
Offline: the downloads are faked."""
# First: puts the plugin, the core and its test helpers on sys.path.
import _support  # noqa: F401
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from taxjson_fetch import api as F
from taxjson_fetch import command as C

# A synthetic IB statement (section,Header/Data shape) covering 2025.
_FLEX_2025 = (
    "Statement,Header,Field Name,Field Value\n"
    "Statement,Data,Period,\"January 1, 2025 - December 31, 2025\"\n"
    "Account Information,Header,Field Name,Field Value\n"
    "Account Information,Data,Name,synthetic\n")
_FLEX_2024 = _FLEX_2025.replace("2025", "2024")


def _cfg(year=2025):
    return {"settings": {"year": year, "country": "canada"},
            "accounts": {"ib": {"type": "taxable",
                                "brokerage": "ibkr_flex",
                                "query_id": "123456"}}}


def _args(**kw):
    base = dict(days=None, year=None, from_date=None,
                flex_token="SYNTHFLEX", positions=False, dry_run=False,
                trim_overlap=False)
    base.update(kw)
    return SimpleNamespace(**base)


def _request(top: Path, year=2025, shared=True, **kw):
    root = top / str(year)
    root.mkdir(parents=True, exist_ok=True)
    said = []
    req = SimpleNamespace(
        args=_args(**kw), root=root, work=root / "work", config=_cfg(year),
        accounts=["ib"], say=said.append, json=False, dry_run=False,
        inputs=(top / "inputs") if shared else root / "inputs",
        holdings=root / "holdings", shared_inputs=shared)
    return req, said


class TestSharedInputs(unittest.TestCase):
    def test_download_goes_to_the_shared_inputs_with_a_note(self):
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            req, said = _request(top)
            with mock.patch.object(F, "flex_fetch",
                                   lambda *a, **k: _FLEX_2025.encode()), \
                    redirect_stdout(io.StringIO()), \
                    redirect_stderr(io.StringIO()):
                C.run(req)
            self.assertEqual(
                (top / "inputs" / "ib" / "ib_flex.csv").read_text(),
                _FLEX_2025)
            self.assertFalse((top / "2025" / "inputs").exists())
            self.assertTrue(any("apply to every year" in m for m in said),
                            said)

    def test_an_older_core_without_the_fields_writes_root_inputs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            req = SimpleNamespace(
                args=_args(), root=root, work=root / "work",
                config=_cfg(), accounts=["ib"], say=lambda m: None,
                json=False)
            with mock.patch.object(F, "flex_fetch",
                                   lambda *a, **k: _FLEX_2025.encode()), \
                    redirect_stdout(io.StringIO()), \
                    redirect_stderr(io.StringIO()):
                C.run(req)
            self.assertTrue((root / "inputs" / "ib" / "ib_flex.csv")
                            .is_file())

    def test_replacing_another_years_statement_is_refused(self):
        # The shared ib_flex.csv holds 2024 (read by the 2024 project):
        # the 2025 project's download covering only 2025 would delete it.
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            (top / "inputs" / "ib").mkdir(parents=True)
            out = top / "inputs" / "ib" / "ib_flex.csv"
            dated = _FLEX_2024 + "Trades,Header,Date/Time,Symbol\n" \
                "Trades,Data,2024-06-03,QZQ\n"
            out.write_text(dated)
            req, _said = _request(top)
            with mock.patch.object(F, "flex_fetch",
                                   lambda *a, **k: _FLEX_2025.encode()), \
                    redirect_stdout(io.StringIO()), \
                    redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as cm:
                C.run(req)
            self.assertIn("2024-06-03", str(cm.exception))
            self.assertEqual(out.read_text(), dated)
        # A single-folder project keeps the per-year test (unchanged).
        self.assertEqual(C._flex_lost_dates(dated, _FLEX_2025, 2025), [])
        self.assertEqual(C._flex_lost_dates(dated, _FLEX_2025, 2025,
                                            every_year=True),
                         ["2024-06-03"])


class TestPositionsIntoHoldings(unittest.TestCase):
    _POS = [{"symbol": "XEI.TO", "openQuantity": 100.0,
             "averageEntryPrice": 10.0}]

    def test_positions_land_in_the_years_holdings_and_are_claimed(self):
        from taxjson.lib.holdings_dir import discover
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.dict("os.environ", {"HOME": td}, clear=False):
            import os
            os.environ.pop("QUESTRADE_TOKEN_FILE", None)
            root = Path(td) / "2025"
            cache = root / "work"
            cache.mkdir(parents=True)
            (Path(td) / ".questrade_token").write_text("OLD\n")
            cfg = {"settings": {"year": 2025},
                   "accounts": {"lira": {"type": "sheltered",
                                         "brokerage": "questrade",
                                         "account": "55500001"}}}  # pii-ok

            def http(url):
                import json
                if "oauth2" in url:
                    return json.dumps(
                        {"api_server": "https://api01.iq.questrade.com/",
                         "access_token": "AT",
                         "refresh_token": "NEW"}).encode()
                return json.dumps({"positions": self._POS}).encode()
            said = []
            out = C._qt_live_holdings(root, cache, cfg, ["lira"], http,
                                      said.append,
                                      holdings=root / "holdings")
            self.assertEqual(out["lira"],
                             root / "holdings" / "lira_live_holdings.toml")
            self.assertTrue(out["lira"].is_file())
            self.assertFalse((cache / "lira_live_holdings.toml").exists())
            # The year folder of a past year: today's positions are said
            # to belong to the current year's project.
            self.assertTrue(any("today's positions" in m for m in said),
                            said)
            found, notes = discover(root / "holdings", cfg["accounts"])
            self.assertEqual(found, {"lira": [str(out["lira"])]})
            self.assertEqual(notes, [])


if __name__ == "__main__":
    unittest.main()
