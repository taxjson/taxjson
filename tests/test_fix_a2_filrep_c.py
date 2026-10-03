"""Re-audit-2 fixes, filing-reports list (helper C): the taxjson_run.py
query views — income dating in the `divs` / `roc` / `events` row views,
missing-book notices, the roc view's [[distributions]] rows, `leaps`
inputs, the `ccd-sum` class-share heading, the Kraken withdrawal fee in
`transfers`, and the income roll-ups outside a project.

All data is synthetic (fake account ids, invented tickers); every test
builds work/ by hand or drives a parser directly — no FX fetch.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent

REPO_ROOT = Path(__file__).resolve().parent.parent

_CA = """\
[settings]
year = 2025
country = "canada"
base_currency = "CAD"
source_currencies = []

[accounts.margin]
type = "taxable"
"""

_US = """\
[settings]
year = 2025
country = "usa"
base_currency = "USD"
source_currencies = []
{extra}
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


class TestTxViewsIncomeDating(unittest.TestCase):
    """A2-0326, A2-0641, A2-0642, A2-0655, A2-1109, A2-1125, A2-1127:
    a tax-year window of the `divs` / `roc` / `events` row views places
    each income row by lib/income_dating, as divs-sum / roc-sum do."""

    @rule("CA-INC-DATE-TRUST", "CA-INC-DATE-ROC-TRUST")
    def test_canada_trust_rows_in_record_year(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _CA, native={"margin": _TRUST_BOOK})
            divs = _json(_cli(root, "divs", "2025", "--json"))
            dsum = _json(_cli(root, "divs-sum", "2025", "--json"))
            self.assertEqual(divs["totals"]["dividend"], {"CAD": 60.0})
            self.assertEqual(dsum["totals"], {"CAD": 60.0})
            self.assertNotIn("dividend",
                             _json(_cli(root, "divs", "2026",
                                        "--json"))["totals"])
            roc = _json(_cli(root, "roc", "2025", "--json"))
            self.assertEqual([t["symbol"] for t in roc["rows"]],
                             ["ZQT.UN.TO"])
            self.assertEqual(_json(_cli(root, "roc", "2026",
                                        "--json"))["rows"], [])
            ev = _json(_cli(root, "events", "2025", "--json"))
            self.assertEqual(len(ev["rows"]), 3)
            # The text view says why a January-paid row is in 2025.
            r = _cli(root, "divs", "2025")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("TOTAL DIVIDEND:", r.stdout)
            self.assertIn("60.00 CAD", r.stdout)
            self.assertIn("record date", r.stderr)

    @rule("CA-INC-DATE-TRUST")
    @rule_absent("CA-INC-DATE-TRUST", country="usa")
    def test_same_book_us_keeps_pay_date(self):
        book = [dict(t, currency="USD") for t in _TRUST_BOOK]
        with tempfile.TemporaryDirectory() as tmp:
            ca = _project(Path(tmp) / "ca", _CA, native={"margin": book})
            us = _project(Path(tmp) / "us", _US.format(extra=""),
                          native={"margin": book})
            self.assertEqual(
                _json(_cli(ca, "divs", "2025", "--json"))
                ["totals"]["dividend"], {"USD": 60.0})
            self.assertEqual(
                _json(_cli(us, "divs", "2025", "--json"))
                ["totals"]["dividend"], {"USD": 10.0})
            self.assertEqual(
                _json(_cli(us, "divs", "2026", "--json"))
                ["totals"]["dividend"], {"USD": 50.0})

    @rule("US-INC-DATE-RIC")
    def test_us_ric_january_dividend_in_prior_year(self):
        cfg = _US.format(extra='ric_january_dividends = ["VTQ.US 2026-01-15"]\n')
        book = [_row("DIVIDEND", "2026-01-15", "VTQ.US", 25.0, cur="USD",
                     gross_amount=25.0)]
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, cfg, native={"margin": book})
            self.assertEqual(
                _json(_cli(root, "divs", "2025", "--json"))
                ["totals"]["dividend"], {"USD": 25.0})
            self.assertEqual(
                _json(_cli(root, "divs-sum", "2025", "--json"))
                ["totals"], {"USD": 25.0})
            self.assertEqual(
                _json(_cli(root, "divs", "2026", "--json"))["rows"], [])


class TestTxViewMissingBook(unittest.TestCase):
    """A2-0333: a configured account with inputs but no native book is
    named on stderr by the row views, as by their -sum twins."""

    def test_events_warns_about_missing_account(self):
        cfg = _CA + '\n[accounts.cash]\ntype = "taxable"\n'
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, cfg, native={"margin": _TRUST_BOOK},
                            inputs=("margin", "cash"))
            for view in ("events", "divs", "trades", "roc", "dil"):
                r = _cli(root, view)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("cash", r.stderr, view)
                self.assertIn("NOT in this report", r.stderr, view)


def _add_dist(root, symbol, record_date, per_share):
    """One taxjson.toml [[distributions]] entry (was a distributions.map
    line)."""
    with (Path(root) / "taxjson.toml").open("a") as f:
        f.write(f'\n[[distributions]]\nsymbol = "{symbol}"\n'
                f'record_date = {record_date}\nper_share = {per_share}\n')


class TestRocViewDistributions(unittest.TestCase):
    """A2-0116, A2-1116, A2-1128: the roc views' [[distributions]] rows."""

    @rule("CA-DIST-02")
    def test_rbc_notional_distribution_counted_once(self):
        notional = _row("ADJUST", "2025-12-31", "VDQ.TO", 52.93, type="dist",
                        description="NOTIONAL DISTRIBUTION ADJUSTMENT TO "
                                    "BOOK COST $52.93")
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _CA, native={"margin": [notional]},
                            base={"margin": [dict(notional)]})
            r = _cli(root, "roc-sum", "2025", "--json")
            doc = _json(r)
            self.assertEqual(doc["totals"], {"CAD": -52.93})
            self.assertEqual(doc["rows"][0]["dist_rows"], 0)
            self.assertNotIn("reduced twice", r.stderr)
            roc = _json(_cli(root, "roc", "2025", "--json"))
            self.assertEqual(len(roc["rows"]), 1)

    @rule("CA-DIST-01")
    def test_roc_view_warns_double_entry(self):
        broker = _row("ADJUST", "2025-06-16", "CRQ.TO", -50.0, type="roc")
        mapped = _row("ADJUST", "2025-06-15", "CRQ.TO", -50.0, type="dist",
                      id="DIST-CRQ.TO-2025-06-16-margin")
        mapped["date_settle"] = "2025-06-16"
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _CA, native={"margin": [broker]},
                            base={"margin": [dict(broker), mapped]})
            _add_dist(root, "CRQ.TO", "2025-06-16", -0.50)
            r = _cli(root, "roc", "2025")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("reduced twice", r.stderr)
            self.assertEqual(r.stdout.count("CRQ.TO"), 2)
            r = _cli(root, "roc-sum", "2025")
            self.assertIn("reduced twice", r.stderr)

    @rule("CA-DIST-01")
    def test_missing_base_book_is_named(self):
        broker = _row("ADJUST", "2025-06-16", "CRQ.TO", -50.0, type="roc")
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, _CA, native={"margin": [broker]})
            _add_dist(root, "ZRQ.TO", "2025-12-31", -0.50)
            for view in ("roc", "roc-sum"):
                r = _cli(root, view, "2025")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("margin_base.json", r.stderr, view)


class TestIncomeSumsNeedProject(unittest.TestCase):
    """A2-1110: divs-sum / roc-sum / dil-sum outside a configured project
    refuse like `divs` (the year, account types and income-dating rules
    are unknown)."""

    def test_refused_without_country(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "work" / "margin_raw.json").write_text(json.dumps(
                {"transactions": _TRUST_BOOK}))
            for view in ("divs-sum", "roc-sum", "dil-sum"):
                r = _cli(root, view)
                self.assertEqual(r.returncode, 1, view)
                self.assertIn("country is missing", r.stderr, view)


_LEAP = "AAQ270115C00050000.TO"


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
    dropping a LEAPS account or a renamed LEAPS."""

    def test_missing_native_book_stops(self):
        cfg = _CA + '\n[accounts.tfsa]\ntype = "sheltered"\n'
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, cfg,
                            native={"tfsa": _leaps_native("BBQ270115C00050000.TO")},
                            inputs=("margin", "tfsa"))
            w = root / "work"
            (w / "margin_gains.json").write_text(json.dumps(
                _leaps_gains(_LEAP, 100.0)))
            (w / "tfsa_gains.json").write_text(json.dumps(
                _leaps_gains("BBQ270115C00050000.TO", 200.0)))
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
    """A2-1115: ccd-sum heads a Rogers call (root RCI) under the held
    class share RCI.B.TO even when no share was sold in the year."""

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


class TestTransfersKrakenFee(unittest.TestCase):
    """A2-0663: a Kraken withdrawal's coin fee (kept out of the money
    `fee` field, S061-17) shows in the transfers view's FEE column."""

    def test_kraken_withdrawal_fee_shown(self):
        from taxjson.lib.brokerages.kraken import KrakenBrokerage
        import contextlib
        import io
        csv = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,"
               "fee,balance\n"
               "L1,F1,2026-05-04 16:00:00,withdrawal,,currency,TAO,spot,"
               "-0.1,0.002,14\n")
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "kr_ledgers.csv"
            p.write_text(csv)
            with contextlib.redirect_stderr(io.StringIO()):
                txs = KrakenBrokerage().parse_file(p)
            xfer = [t for t in txs if t["action"] == "TRANSFER"]
            self.assertEqual(xfer[0].get("fee_qty"), 0.002)
            self.assertEqual(xfer[0].get("fee_currency"), "TAO")
            cfg = _CA + '\n[accounts.crypto]\ntype = "taxable"\ncrypto = true\n'
            root = _project(Path(tmp) / "p", cfg)
            (root / "work" / "crypto_kraken_transfers.json").write_text(
                json.dumps({"metadata": {"kind": "transfer_sidecar",
                                         "account": "crypto"},
                            "transactions": xfer}))
            doc = _json(_cli(root, "transfers", "--json"))
            row = doc["transfers"][0]
            self.assertEqual(row["fee_qty"], 0.002)
            self.assertEqual(row["fee_currency"], "TAO")
            r = _cli(root, "transfers")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("0.002_TAO", r.stdout)


if __name__ == "__main__":
    unittest.main()
