"""Re-audit-2 fixes, filing-reports list (helper C): the taxjson_run.py
query views — income dating in the `divs` / `roc` / `events` row views,
missing-book notices, the roc view's [[distributions]] rows, `leaps`
inputs, the `ccd-sum` class-share heading, the Kraken withdrawal fee in
`transfers`, and the income roll-ups outside a project.

All data is synthetic (fake account ids, invented tickers); every test
builds work/ by hand or drives a parser directly — no FX fetch.

Formerly: test_fix_a2_filrep_c
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _paths


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"

REPO_ROOT = _paths.REPO

_CA = """\
[settings]
local_timezone = "America/Toronto"
year = 2025
country = "canada"
base_currency = "CAD"
source_currencies = []

[accounts.margin]
type = "taxable"
"""


def _cli(root, *args):
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True, env=e,
        stdin=subprocess.DEVNULL)


def _project(tmp, config, native=None, base=None, inputs=("margin",)):
    """A project folder: taxjson.toml, an input file per account in
    `inputs` (so the account 'has inputs'), and work/<acct>_raw.json /
    work/<acct>_base.json from `native` / `base` ({acct: [rows]})."""
    root = Path(tmp)
    root.mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(config)
    for a in inputs:
        d = root / "inputs" / a
        d.mkdir(parents=True, exist_ok=True)
        (d / "rows.tt").write_text("# synthetic\n")
    w = root / "work"
    w.mkdir(exist_ok=True)
    for a, rows in (native or {}).items():
        (w / f"{a}_raw.json").write_text(json.dumps({"transactions": rows}))
    for a, rows in (base or {}).items():
        (w / f"{a}_base.json").write_text(json.dumps({"transactions": rows}))
    return root


def _row(action, date, symbol, net, cur="CAD", **kw):
    r = {"action": action, "date": date, "time": "00:00:00",
         "date_settle": date, "symbol": symbol, "quantity": 0.0,
         "price": 0.0, "net_amount": float(net), "currency": cur,
         "account": "margin"}
    r.update(kw)
    return r


# A Canadian trust's December-record distribution and ROC, paid in
# January (CA-INC-DATE-TRUST / CA-INC-DATE-ROC-TRUST), plus an ordinary
# corporate dividend paid in the year.
_TRUST_BOOK = [
    _row("DIVIDEND", "2026-01-15", "ZQT.UN.TO", 50.0, record_date="2025-12-31",
         income_label="distribution", gross_amount=50.0,
         description="ZQT REIT DIST ON 100 SHS REC 12/31/25 PAY 01/15/26"),
    _row("ADJUST", "2026-01-15", "ZQT.UN.TO", -20.0, record_date="2025-12-31",
         type="roc",
         description="ZQT REIT RETURN OF CAPITAL REC 12/31/25 PAY 01/15/26"),
    _row("DIVIDEND", "2025-06-02", "ZQC.TO", 10.0, gross_amount=10.0),
]


def _json(r):
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


class TestIncomeSumsNeedProject(unittest.TestCase):
    """A2-1110: divs-sum / roc-sum / dil-sum outside a configured project
    refuse like `divs` (the year, account types and income-dating rules
    are unknown).

    Formerly: test_fix_a2_filrep_c.TestIncomeSumsNeedProject"""

    def test_refused_without_country(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "work" / "margin_raw.json").write_text(json.dumps(
                {"transactions": _TRUST_BOOK}))
            for view in ("divs-sum", "roc-sum", "dil-sum"):
                r = _cli(root, view)
                self.assertEqual(r.returncode, 1, view)
                # No taxjson.toml: no project (audit 2026-10-22, item 15).
                self.assertIn("not a taxjson project", r.stderr, view)


_LEAP = "AAQ270618C00045000.TO"


def _leaps_gains(sym, gain):
    return {"transactions": [{"symbol": sym, "date": "2025-06-10",
                              "date_settle": "2025-06-11", "qty": 1,
                              "cost": 500.0, "proceeds": 500.0 + gain,
                              "gain": gain, "currency": "CAD"}],
            "summary": {"tax_date_basis": "settle"}}


def _leaps_native(sym):
    return [dict(_row("BUYSELL", "2025-01-10", sym, -500.0), quantity=1,
                 price=5.0),
            dict(_row("BUYSELL", "2025-06-10", sym, 600.0), quantity=-1,
                 price=6.0)]


class TestLeapsInputs(unittest.TestCase):
    """A2-0117, A2-0329, A2-1126: leaps / leaps-sum stop instead of
    dropping a LEAPS account or a renamed LEAPS.

    Formerly: test_fix_a2_filrep_c.TestLeapsInputs"""

    def test_missing_native_book_stops(self):
        cfg = _CA + '\n[accounts.tfsa]\ntype = "sheltered"\n'
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, cfg,
                            native={"tfsa": _leaps_native("BBQ270618C00045000.TO")},
                            inputs=("margin", "tfsa"))
            w = root / "work"
            (w / "margin_gains.json").write_text(json.dumps(
                _leaps_gains(_LEAP, 100.0)))
            (w / "tfsa_gains.json").write_text(json.dumps(
                _leaps_gains("BBQ270618C00045000.TO", 200.0)))
            for args in (("leaps-sum",), ("leaps",), ("leaps-sum", "margin")):
                r = _cli(root, *args)
                self.assertNotEqual(r.returncode, 0, args)
                self.assertIn("margin", r.stderr, args)
                self.assertIn("native", r.stderr, args)

    def test_unreadable_ticker_map_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _CA, native={"margin": _leaps_native(_LEAP)})
            (root / "work" / "margin_gains.json").write_text(json.dumps(
                _leaps_gains(_LEAP, 100.0)))
            (root / "ticker.map").write_bytes(b"TOBASE AAQ.US AAQ.TO \xe9\n")
            for view in ("leaps", "leaps-sum"):
                r = _cli(root, view)
                self.assertNotEqual(r.returncode, 0, view)
                self.assertIn("ticker.map", r.stderr, view)


class TestCcdSumClassShare(unittest.TestCase):
    """A2-1115: ccd-sum heads a call on a class share (root RCQ) under
    the held class share RCQ.B.TO even when no share was sold in the
    year.

    Formerly: test_fix_a2_filrep_c.TestCcdSumClassShare"""

    def test_heading_uses_held_class_share(self):
        call = "RCQ250620C00055000.TO"
        gains = {"transactions": [
            {"symbol": call, "date": "2025-05-01", "date_settle": "2025-05-02",
             "qty": 1, "cost": -200.0, "proceeds": -50.0, "gain": 150.0,
             "direction": "SHORT", "currency": "CAD"}],
            "summary": {"tax_date_basis": "settle"}}
        native = [dict(_row("BUYSELL", "2025-02-03", "RCQ.B.TO", -10000.0),
                       quantity=200, price=50.0),
                  dict(_row("BUYSELL", "2025-03-03", call, 200.0),
                       quantity=-1, price=2.0),
                  dict(_row("BUYSELL", "2025-05-01", call, -50.0),
                       quantity=1, price=0.5)]
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _CA, native={"margin": native})
            (root / "work" / "margin_gains.json").write_text(json.dumps(gains))
            doc = _json(_cli(root, "ccd-sum", "--json"))
            unds = [r.get("underlying") for r in doc["rows"]]
            self.assertEqual(unds, ["RCQ.B.TO"])


if __name__ == "__main__":
    unittest.main()
