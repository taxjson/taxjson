"""`tjs fetch` reads the project's year from the config the core hands it.

The core's loaded config holds `[settings]` as its typed settings object,
which is read by attribute and is no mapping. The plugin read it as a dict
(`.get("year")`), so every real `tjs fetch` of a Questrade or IB account ended
in a traceback while the tests, which passed a plain dict, stayed green
(backlog TJ-4972). Every broker response is mocked; nothing touches the
network. Synthetic numbers, account ids and tickers (SYN) only.
"""
# First: puts the plugin, the core and its test helpers on sys.path.
import _bootstrap
import contextlib
import io
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from taxjson_fetch import api
from taxjson_fetch import command as fc
from taxjson.pipeline.config import load_config
from taxjson.project.settings import Settings

from _questrade_fetch import NUMBER, SESSION, _act, _qt_request

assert _bootstrap.PLUGIN_ROOT.is_dir()

_TOML = f"""[settings]
year = 2025
country = "canada"

[accounts.margin]
type = "taxable"
brokerage = "questrade"
account = "{NUMBER}"
"""


def _loaded(root):
    (root / "taxjson.toml").write_text(_TOML)
    with contextlib.redirect_stderr(io.StringIO()):
        cfg = load_config(root)
    return cfg


class TestTheLoadedConfigIsWhatThePluginReads(unittest.TestCase):

    def test_the_core_hands_over_the_typed_object(self):
        # What the test below stands on: were this a dict again, the
        # plugin's tests with plain dicts would be enough.
        with tempfile.TemporaryDirectory() as td:
            self.assertIsInstance(_loaded(Path(td))["settings"], Settings)

    def test_project_year_of_each_shape(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(fc._project_year(_loaded(Path(td))), 2025)
        self.assertEqual(fc._project_year({"settings": {"year": 2024}}), 2024)
        self.assertIsNone(fc._project_year({"settings": {}}))
        self.assertIsNone(fc._project_year({}))

    def test_a_questrade_fetch_runs_on_the_loaded_config(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "inputs" / "margin").mkdir(parents=True)
            req, _said = _qt_request(root, from_date=None)
            req.config = _loaded(root)
            seen = {}

            def window(days, from_date, year=None):
                seen["year"] = year
                return date(2025, 6, 1), date(2025, 6, 30)
            acts = [_act("2025-06-02", "2025-06-03", 10, 10)]
            with patch.object(fc, "_qt_open_session",
                              return_value=(dict(SESSION), "x")), \
                    patch.object(api, "qt_activities", return_value=acts), \
                    patch.object(api, "qt_window", side_effect=window), \
                    contextlib.redirect_stderr(io.StringIO()):
                res = fc.run(req)
            self.assertEqual(seen, {"year": 2025})
            self.assertIn("margin", res)

    def test_the_positions_fetch_runs_on_the_loaded_config(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            cfg = _loaded(root)
            with patch.object(fc, "_qt_open_session",
                              return_value=(dict(SESSION), "x")), \
                    patch.object(api, "qt_positions", return_value=[]), \
                    contextlib.redirect_stderr(io.StringIO()):
                out = fc._qt_live_holdings(root, root / "work", cfg,
                                           ["margin"], None, [].append)
            self.assertIn("margin", out)


if __name__ == "__main__":
    unittest.main()
