"""Fix round 2026-10-29 (round 4), group views: derived views and
reports.

TJ-0279  <account>.sum asked for a `.tt OPENING cost=unknown` line
         (MANUAL REPORTING REQUIRED) for a sale of units moved in from
         another of your accounts: their cost is in the blended figures
         (<account>_wash.sum, `tjs sum`). They are a block of their
         own now, and its holdings table says "unknown", not 0.00.
TJ-0598  reports/<account>_holdings.toml: units moved in from another
         of your accounts take the cost the run has after the passes
         across accounts (`cost_source = "blended"`), where round 3
         left it unknown.
TJ-0629  `tjs stats` / `win-rates` counted a registered account's
         withdrawal in kind (a TRANSFER out, `transfers = true`, which
         the books book as a sale at its value) as a closed trade, with
         a win or a loss. It is no trade: its P/L is counted apart.
TJ-0634  The US option-replacement warning's covered_shares carried
         float noise (9.999999999999998) into the gains JSON: the loss's
         open part is a money ratio's, and quantities are saved
         unrounded. Share counts are now at the engine's share
         precision (six decimals).

Every fixture is synthetic: invented tickers, amounts and account ids.

Formerly: test_fix_1029_r4_views
"""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
from _style import CapturedWidth

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO_ROOT = _paths.REPO
SRC = REPO_ROOT / "src"
_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=300)


def _flat(text: str) -> str:
    return " ".join(text.split())


# --------------------------------------------------------------------
# The per-account .sum and units moved in from your own account
# (TJ-0279)
# --------------------------------------------------------------------

_QH = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Activity Type,Account #,Account Type\n")


def _qt(acct_no: str, cur: str, *rows) -> str:
    """A Questrade activity export: rows of (date, action, description,
    quantity, price, net amount, activity type)."""
    out = _QH
    for d, act, desc, q, px, net, kind in rows:
        out += (f"{d},{d},{act},ZZQ,{desc},{q},{px:.2f},{net:.2f},0.00,"
                f"{net:.2f},{cur},{kind},{acct_no},Margin\n")
    return out


def _moved_project(root: Path, country: str, *qb_rows) -> Path:
    """qa buys 100 ZZQ at 2.00 and moves them out on 2024-03-01; qb
    receives them the next day (a move of your own), then `qb_rows`."""
    from tax_rules.dual import settings_for
    cur = "CAD" if country == "canada" else "USD"
    accounts = {
        "qa": _qt("99900001", cur,
                  ("2024-01-10", "Buy", "ZZQ CORP", 100, 2.0, -200.0,
                   "Trades"),
                  ("2024-03-01", "TF6", "ZZQ CORP TRANSFER OUT", -100,
                   0.0, 0.0, "Transfers")),
        "qb": _qt("99900002", cur,
                  ("2024-03-02", "TF6", "ZZQ CORP TRANSFER IN", 100, 0.0,
                   0.0, "Transfers"), *qb_rows)}
    root.mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        settings_for(country, year=2024, source_currencies=[])
        + "".join(f'[accounts.{n}]\ntype = "taxable"\n' for n in accounts))
    for n, text in accounts.items():
        (root / "inputs" / n).mkdir(parents=True)
        (root / "inputs" / n / "questrade_2024.csv").write_text(text)
    return root


_SALE = ("2024-06-03", "Sell", "ZZQ CORP", -40, 3.0, 120.0, "Trades")


# --------------------------------------------------------------------
# The holdings file: moved-in units at the cost the run has (TJ-0598)
# --------------------------------------------------------------------

def _holdings(root: Path, account: str):
    import tomllib
    text = (root / "reports" / f"{account}_holdings.toml").read_text()
    return tomllib.loads(text).get("holding") or [], text


def _comments(text: str) -> str:
    """The comment lines' text, "# " dropped, whitespace collapsed."""
    return " ".join(" ".join(ln[2:] for ln in text.splitlines()
                             if ln.startswith("# ")).split())


class TestMovedInUnitsTakeTheBlendedCost(unittest.TestCase):
    """TJ-0598: round 3 wrote `cost_unknown = true` for units qb got by
    a move of its own from qa. The run has their cost after the passes
    across accounts (qb_gains_wash.json, `tjs list`): the holding
    now carries it, `cost_source = "blended"`.

    Formerly: test_fix_1029_r4_views.TestMovedInUnitsTakeTheBlendedCost"""

    def _check(self, country, qb_rows, qty, cost, fig):
        cur = "CAD" if country == "canada" else "USD"
        with tempfile.TemporaryDirectory() as td:
            root = _moved_project(Path(td) / "p", country, *qb_rows)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            (h,), text = _holdings(root, "qb")
            self.assertEqual(
                {k: h.get(k) for k in (
                    "quantity", "currency", "total_cost", "cost_per_share",
                    "base_currency", "base_total_cost",
                    "base_cost_per_share", "cost_source", "cost_unknown")},
                {"quantity": qty, "currency": cur, "total_cost": cost,
                 "cost_per_share": 2.0, "base_currency": cur,
                 "base_total_cost": cost, "base_cost_per_share": 2.0,
                 "cost_source": "blended", "cost_unknown": None})
            self.assertIn(
                "cost_source = blended: 100 moved in from qa (your own "
                "account) on 2024-03-01, a cost this account's own books "
                "do not have. total_cost and base_total_cost are " + fig
                + " after the passes across accounts, as `tjs list` "
                  "gives it, not this account's own books.",
                _comments(text))
            # The figure is `tjs list`'s.
            rows = json.loads(_cli(root, "list", "--json").stdout)["rows"]
            self.assertEqual([(x["account"], x["qty"], round(x["cost"], 2))
                              for x in rows], [("qb", qty, cost)])
            # Read back by taxjson-export: the cost, not unknown.
            from taxjson.bin.taxjson_export import \
                _holdings_toml_to_inventory
            import tomllib
            (item,) = _holdings_toml_to_inventory(
                tomllib.loads(text))["inventory"]
            self.assertEqual(item["total_cost"], cost)
            self.assertFalse(item.get("unknown_cost"))

    @rule(R.CA_ACB_OWN_MOVE)
    def test_canada(self):
        self._check("canada", (), 100.0, 200.0,
                    "this account's share of the ACB pool across your "
                    "taxable accounts (s.47)")

    @rule(R.CA_ACB_OWN_MOVE)
    def test_canada_after_a_sale(self):
        self._check("canada", (_SALE,), 60.0, 120.0,
                    "this account's share of the ACB pool across your "
                    "taxable accounts (s.47)")

    @rule(R.US_BASIS_05)
    def test_usa(self):
        self._check("usa", (), 100.0, 200.0,
                    "the basis of this account's lots (the moved lots' "
                    "basis carried with them)")

    @rule(R.CA_ACB_OWN_MOVE)
    def test_one_account_run_keeps_it_unknown(self):
        # `run --account qb` runs no pass across accounts: no figure
        # to take, the holding says so as before.
        with tempfile.TemporaryDirectory() as td:
            root = _moved_project(Path(td) / "p", "canada")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            r = _cli(root, "run", "--no-input", "--account", "qb")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            (h,), text = _holdings(root, "qb")
            self.assertIs(h["cost_unknown"], True)
            self.assertNotIn("total_cost", h)
            self.assertNotIn("cost_source", h)
            # The listing's currency (a key a reader requires), from the
            # move's own rows: these books' pool has none.
            self.assertEqual(h["currency"], "CAD")

    @rule(R.CA_ACB_OWN_MOVE)
    def test_canada_crypto(self):
        from _support.transfer_cases import _crypto_move
        with tempfile.TemporaryDirectory() as td:
            root = _crypto_move(Path(td) / "p", "canada")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            (h,), _text = _holdings(root, "receiver")
            # 0.5 ETH left of the 1 bought for 100 (one pool).
            self.assertEqual((h["quantity"], h["total_cost"],
                              h["base_total_cost"], h["cost_source"]),
                             (0.5, 50.0, 50.0, "blended"))
            self.assertNotIn("cost_unknown", h)


class TestAFillThatFailsKeepsTheFile(unittest.TestCase):
    """TJ-0598: a blended file the export cannot read leaves the
    holdings file stage_account wrote, said in one warning.

    Formerly: test_fix_1029_r4_views.TestAFillThatFailsKeepsTheFile"""

    def test_damaged_blended_file(self):
        from taxjson.pipeline import diagnostics as pipeline_diagnostics
        from taxjson.pipeline import tools as pipeline_tools
        with tempfile.TemporaryDirectory() as td:
            work, reports = Path(td) / "work", Path(td) / "reports"
            work.mkdir()
            reports.mkdir()
            gains = work / "qb_raw_gains.json"
            gains.write_text(json.dumps({"inventory": [
                {"symbol": "ZZQ.TO", "qty": 100.0, "total_cost": 0.0,
                 "currency": "CAD", "unknown_cost": True}],
                "transactions": []}))
            (work / "qb_gains_wash.json").write_text("{not json")
            held = reports / "qb_holdings.toml"
            held.write_text("cost_unknown = true\n")
            cmd = pipeline_tools._cmd("taxjson-export") + [
                "--holdings-toml", "--account-name", "qb", str(gains)]
            err = io.StringIO()
            with contextlib.redirect_stderr(err), \
                    contextlib.redirect_stdout(err):
                pipeline_diagnostics.fill_moved_cost("qb", cmd, work, reports)
            self.assertEqual(held.read_text(), "cost_unknown = true\n")
            self.assertIn("qb: the holdings file keeps units moved in from "
                          "another of your accounts at an unknown cost",
                          _flat(err.getvalue()))


class TestTheBlendedFillHoldsBack(unittest.TestCase):
    """TJ-0598: the figure after the passes is taken only where it is
    the holding's alone; a listing in another currency keeps its own
    cost unknown and takes the base-currency figure.

    Formerly: test_fix_1029_r4_views.TestTheBlendedFillHoldsBack"""

    def _args(self, **kw):
        from types import SimpleNamespace
        return SimpleNamespace(**dict(dict(
            account_name="qb", inputs=["qb_raw_gains.json"], crypto=False,
            symbol_suffix=None, country="canada", base_currency="CAD",
            dust_threshold=1e-3), **kw))

    def _fill(self, agg, inv, tobase=None):
        from taxjson.bin.taxjson_export import _blended_fill
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "qb_gains_wash.json"
            p.write_text(json.dumps({"inventory": inv,
                                     "transactions": []}))
            m = tobase or {}
            return _blended_fill(agg, [str(p)], "qb",
                                 lambda s: m.get(s, s), "CAD", 1e-3, {})

    _MOVED = {"qty": 100.0, "total_cost": 0.0, "currency": "",
              "position_start_date": "2024-03-01", "unknown_cost": True}

    def test_refusals(self):
        row = {"symbol": "ZZQ.TO", "qty": 100.0, "total_cost": 200.0,
               "currency": "CAD", "account": "qb"}
        self.assertEqual(self._fill({"ZZQ.TO": dict(self._MOVED)}, [row]),
                         {"ZZQ.TO": {"total_cost": 200.0,
                                     "currency": "CAD", "native": ""}})
        for why, agg, inv, tb in (
                ("another quantity", {"ZZQ.TO": dict(self._MOVED)},
                 [dict(row, qty=90.0)], None),
                ("no cost there either", {"ZZQ.TO": dict(self._MOVED)},
                 [dict(row, unknown_cost=True)], None),
                ("another account's row", {"ZZQ.TO": dict(self._MOVED)},
                 [dict(row, account="qa")], None),
                ("not the base currency", {"ZZQ.TO": dict(self._MOVED)},
                 [dict(row, currency="USD")], None),
                ("two listings, one pool",
                 {"ZZQ.TO": dict(self._MOVED),
                  "ZZQ.US": dict(self._MOVED, qty=5.0)},
                 [row], {"ZZQ.US": "ZZQ.TO"}),
                ("a known cost", {"ZZQ.TO": dict(self._MOVED,
                                                 unknown_cost=False)},
                 [row], None)):
            with self.subTest(why):
                self.assertEqual(self._fill(agg, inv, tb), {})

    @rule(R.CA_ACB_OWN_MOVE)
    def test_a_us_listing_in_a_canadian_project(self):
        from taxjson.bin.taxjson_export import render_holdings_toml
        import tomllib
        agg = {"ZZQ.US": dict(self._MOVED, currency="USD")}
        fill = self._fill(agg, [{"symbol": "ZZQ.US", "qty": 100.0,
                                 "total_cost": 270.0, "currency": "CAD",
                                 "account": "qb"}])
        self.assertEqual(fill["ZZQ.US"]["native"], "USD")
        text = "\n".join(render_holdings_toml(
            agg, self._args(), {k: dict(v) for k, v in agg.items()},
            None, {"ZZQ.US": [("2024-03-01", 100.0, "qa")]}, fill))
        (h,) = tomllib.loads(text)["holding"]
        self.assertEqual(
            {k: h.get(k) for k in ("currency", "cost_unknown",
                                   "total_cost", "base_currency",
                                   "base_total_cost", "cost_source")},
            {"currency": "USD", "cost_unknown": True, "total_cost": None,
             "base_currency": "CAD", "base_total_cost": 270.0,
             "cost_source": "blended"})
        self.assertIn("total_cost and cost_per_share (USD) are left out "
                      "(cost_unknown); base_total_cost is this account's "
                      "share of the ACB pool", _comments(text))


if __name__ == "__main__":
    unittest.main()
