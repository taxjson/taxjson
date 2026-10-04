"""Crypto sends: classify every unmatched outgoing crypto transfer as
self / gift / payment, price the dispositions, and generate the .tt
BUYSELL lines (`taxjson crypto-sends`, the `taxjson run` prompt, the
checklist step). Stablecoin gifts/payments get the FX-gain calculation
instead of a sale line.

Synthetic ledgers only. Rates: a hand-written work/to_base.csv; coin
prices: a fill-crypto cache under a temporary HOME with TAXJSON_OFFLINE
set, so nothing here touches the network.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from taxjson.lib import crypto_sends as cs
from tax_rules import rule


REPO_ROOT = Path(__file__).resolve().parent.parent

# Kraken ledger (UTC). America/Toronto is UTC-5 in winter, UTC-4 in
# summer — the local stamps the ids and .tt lines carry are noted.
KR_LEDGER = (
    "txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,balance\n"
    # CAD -> USDC instant conversion: the USD pool's first 1000 units
    # cost exactly 1370 CAD.
    "LA1AAA,RA1,2025-01-15 15:00:00,spend,,currency,ZCAD,spot,-1370,0,0\n"
    "LA2AAA,RA1,2025-01-15 15:00:00,receive,,currency,USDC,spot,1000,0,"
    "1000\n"
    # USDC reward, 10 units at the 2025-02-01 rate 1.40 -> cost 14.
    "LB1BBB,RB1,2025-02-01 12:00:00,earn,reward,currency,USDC,spot,10,0,"
    "1010\n"
    # Gift of 100 USDC (+1 USDC withdrawal fee). 07:00 local.
    "LC1CCC,RC1,2025-03-01 12:00:00,withdrawal,,currency,USDC,spot,-100,1,"
    "909\n"
    # ATOM arriving from Coinbase (matched: a self-custody move).
    "LD1DDD,RD1,2025-06-22 14:08:31,deposit,,currency,ATOM,spot,80,0,80\n"
    # USDC reward 9 days after the Coinbase USDC gift below -> that
    # gift's small FX loss is (likely) superficial.
    "LE1EEE,RE1,2025-07-20 12:00:00,earn,reward,currency,USDC,spot,1,0,"
    "910\n"
    # QZL reward so the book holds QZL before the payment.
    "LF1FFF,RF1,2026-01-12 12:00:00,earn,reward,currency,QZL,spot,3,0,3\n"
    # Payment in QZL: 2.5 QZL + a 0.05 QZL network fee the parser
    # books by itself. 17:30:00 local.
    "LG1GGG,RG1,2026-04-13 21:30:00,withdrawal,,currency,QZL,spot,-2.5,"
    "0.05,0.45\n"
    # A Hybrid Earn sweep nobody has classified yet.
    "LH1HHH,RH1,2026-06-01 12:00:00,hybridearnwithdrawal,,currency,USDC,"
    "spot,-5,0,905\n")

CB_CSV = (
    "ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
    "Price Currency,Price at Transaction,Subtotal,"
    "Total (inclusive of fees and/or spread),Fees and/or Spread,Notes\n"
    "c1,2025-05-01 12:00:00 UTC,Buy,USDC,20,CAD,1.38,27.60,27.60,0,"
    "Bought 20 USDC\n"
    "c2,2025-06-01 12:00:00 UTC,Buy,BTC,0.002,CAD,140000,280,280,0,"
    "Bought 0.002 BTC\n"
    "c3,2025-06-10 12:00:00 UTC,Buy,ATOM,80.002,CAD,9,720.02,"
    "720.02,0,Bought ATOM\n"
    "c4,2025-07-11 11:20:39 UTC,Send,USDC,10,CAD,1.35,13.50,13.50,0,"
    "Sent 10 USDC to an address\n"
    "c5,2025-06-22 14:05:10 UTC,Send,ATOM,80.002,CAD,9.85,788.02,"
    "788.02,0,Sent ATOM to an address\n"
    "c6,2025-08-01 12:00:00 UTC,Send,BTC,0.001,CAD,150000,150,150,0,"
    "Sent BTC to an address\n")

PAY_ID = "kr-20260413T173000-QZL-2.5"
# QZL is a fictional coin. Yahoo lists it under a numbered id, which
# only the project's ticker.map CRYPTO line knows (there is no built-in
# coin id table): every project here carries the line.
CRYPTO_MAP_LINE = "CRYPTO QZL QZL55501\n"
BTC_ID = "cb-20250801T080000-BTC-0.001"
KR_USDC_ID = "kr-20250301T070000-USDC-100"
CB_USDC_ID = "cb-20250711T072039-USDC-10"
HYBRID_ID = "kr-20260601T080000-USDC-5"

SPECIAL_RATES = {"2025-02-01": "1.4000", "2025-03-01": "1.4500",
                 "2026-04-13": "1.3611"}


def _rates_file(path: Path):
    d = date(2025, 1, 1)
    lines = []
    while d <= date.today():
        iso = d.isoformat()
        lines.append(f"{iso} 12:00:00 USD CAD "
                     f"{SPECIAL_RATES.get(iso, '1.3500')} boc")
        d += timedelta(days=1)
    path.write_text("\n".join(lines) + "\n")


def _cad_usd_rates_file(path: Path):
    """CAD->USD rates for a USD (US) project: the fixture ledger buys
    USDC with CAD, which a US project books as a purchase of property
    (stablecoins are property there, tax-logic US-CRYPTO-02)."""
    d = date(2025, 1, 1)
    lines = []
    while d <= date.today():
        lines.append(f"{d.isoformat()} 12:00:00 CAD USD 0.7300 yahoo")
        d += timedelta(days=1)
    path.write_text("\n".join(lines) + "\n")


def _project(td, *, country="canada"):
    root = Path(td) / "proj"
    acct = root / "inputs" / "crypto"
    acct.mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        f'[settings]\nlocal_timezone = "America/Toronto"\nyear = 2026\ncountry = "{country}"\n'
        f'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
        f'[accounts.crypto]\ntype = "taxable"\ncrypto = true\n')
    (acct / "kr_ledgers.csv").write_text(KR_LEDGER)
    (acct / "cb_2025.csv").write_text(CB_CSV)
    (root / "ticker.map").write_text(CRYPTO_MAP_LINE)
    (root / "work").mkdir()
    _rates_file(root / "work" / "to_base.csv")
    home = Path(td) / "home"
    home.mkdir()
    (home / ".crypto_price_cache.json").write_text(json.dumps({
        "QZL55501-2026-04-13": 1.84,
        "QZL55501-2026-01-12": 2.1,
    }))
    return root, home


def _env(home):
    return {**os.environ, "HOME": str(home), "TAXJSON_OFFLINE": "1",
            "TAXJSON_LOCAL_TZ": "America/Toronto", "NO_COLOR": "1",
            "PYTHONPATH": str(REPO_ROOT / "src")}


def _cli(root, home, *a):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *a], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, env=_env(home))


class TestUnits(unittest.TestCase):
    def test_ids_and_masking_carry_no_refids(self):
        self.assertEqual(cs.mask_ref("LG1GGG-xfer"), "LG***")
        self.assertEqual(cs.mask_ref(""), "")
        self.assertEqual(
            cs.send_id("kraken", "2026-04-13", "17:30:00", "QZL", -2.5),
            PAY_ID)
        self.assertEqual(cs.fmt_price(512.3456789), "512.346")
        self.assertEqual(cs.fmt_price(150000.0), "150000")
        self.assertEqual(cs.fmt_price(0.0000123456789), "0.0000123457")

    def test_matching_pairs_a_send_with_its_arrival(self):
        rows = [
            {"exchange": "coinbase", "account": "c", "date": "2025-06-22",
             "time": "10:05:10", "symbol": "ATOM", "quantity": -80.002},
            {"exchange": "kraken", "account": "c", "date": "2025-06-22",
             "time": "10:08:31", "symbol": "ATOM", "quantity": 80.0},
            # Same coin, far too late: not an arrival of the send above.
            {"exchange": "coinbase", "account": "c", "date": "2025-09-01",
             "time": "10:00:00", "symbol": "ATOM", "quantity": -1.0},
            {"exchange": "kraken", "account": "c", "date": "2025-09-09",
             "time": "10:00:00", "symbol": "ATOM", "quantity": 1.0},
        ]
        unmatched, pairs = cs.match_transfers(rows)
        self.assertEqual(len(pairs), 1)
        self.assertEqual([r["quantity"] for r in unmatched], [-1.0])

    def test_prompt_records_answers_and_skip(self):
        with tempfile.TemporaryDirectory() as td:
            man = Path(td) / "sends.json"
            sends = [{"id": PAY_ID, "summary": "2.5 QZL", "decision": None,
                      "stable": False},
                     {"id": HYBRID_ID, "summary": "5 USDC",
                      "decision": None, "stable": True}]
            answers = iter(["p", ""])
            out = []
            n = cs.prompt_undecided(sends, man, ask=lambda _p: next(answers),
                                    say=out.append)
            self.assertEqual(n, 1)
            doc = json.loads(man.read_text())
            self.assertEqual(doc["sends"][PAY_ID]["decision"], "payment")
            self.assertNotIn(HYBRID_ID, doc["sends"])


class TestCryptoSendsProject(unittest.TestCase):
    """One synthetic project, the whole flow: run -> list -> --set ->
    --write -> run books the lines."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root, cls.home = _project(cls._td.name)
        r = _cli(cls.root, cls.home, "run", "--no-input")
        cls.first_run = r
        if r.returncode != 0:
            raise AssertionError(r.stdout[-3000:] + r.stderr[-3000:])

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _list(self):
        r = _cli(self.root, self.home, "crypto-sends", "crypto", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        return {s["id"]: s for s in doc["accounts"]["crypto"]["sends"]}, doc

    def test_a_headless_run_points_at_the_command(self):
        err = self.first_run.stderr + self.first_run.stdout
        self.assertIn("taxjson crypto-sends", err)
        self.assertIn("not yet classified", err)

    @rule("CA-CRYPTO-07")
    def test_b_listing_unmatched_sends_with_fair_values(self):
        sends, doc = self._list()
        self.assertEqual(set(sends), {PAY_ID, BTC_ID, KR_USDC_ID,
                                      CB_USDC_ID, HYBRID_ID})
        self.assertEqual(doc["accounts"]["crypto"]["matched"], 1)
        # A Kraken Hybrid Earn sweep keeps the coins yours: decided
        # automatically, never asked about.
        self.assertEqual(sends[HYBRID_ID]["decision"], "self")
        self.assertTrue(sends[HYBRID_ID]["auto"])
        pay = sends[PAY_ID]
        self.assertIsNone(pay["decision"])
        self.assertEqual(pay["ref"], "LG***")
        self.assertAlmostEqual(pay["fair_value"]["price"], 2.50442, places=5)
        self.assertIn("Yahoo QZL55501-USD", pay["fair_value"]["source"])
        self.assertIn("Bank of Canada", pay["fair_value"]["source"])
        # The parser's own fee row is not counted again.
        self.assertEqual(pay["quantity"], 2.5)
        self.assertEqual(pay["tt"],
                         "BUYSELL 2026-04-13 17:30:00 QZL -2.5 CAD 2.50442 "
                         "6.26 0")
        btc = sends[BTC_ID]
        self.assertIn("Coinbase", btc["fair_value"]["source"])
        self.assertEqual(btc["tt"], "BUYSELL 2025-08-01 08:00:00 BTC "
                                    "-0.001 CAD 150000 150.00 0")
        # Nothing private in the listing: no txids, no refids. Checked
        # on the listing's own values — the project paths (`manifest`,
        # `tt_file`, the tmp dir in the text) are random and may
        # contain any short string such as "c6".
        def leaves(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    if k not in ("manifest", "tt_file"):
                        yield from leaves(v)
            elif isinstance(node, list):
                for v in node:
                    yield from leaves(v)
            elif isinstance(node, str):
                yield node
        values = list(leaves(doc))
        self.assertIn("LG***", values)
        text = _cli(self.root, self.home, "crypto-sends").stdout
        for p in sorted({str(self.root), str(self.home),
                         str(self.root.resolve()),
                         str(self.home.resolve())}, key=len, reverse=True):
            text = text.replace(p, "<tmp>")
        for secret in ("LG1GGG", "RG1", "LC1CCC", "c6"):
            for v in values:
                self.assertNotIn(secret, v)
            self.assertNotIn(f" {secret}", text)

    @rule("CA-CRYPTO-08")
    def test_c_stablecoins_get_the_fx_gain_not_a_sale(self):
        sends, _ = self._list()
        k = sends[KR_USDC_ID]
        self.assertTrue(k["stable"])
        self.assertIsNone(k["tt"])
        # Pool: 1000 @1370 + 10 @14 -> avg 1384/1010; 100 at 1.45.
        self.assertAlmostEqual(k["fx"]["value"], 145.00, places=2)
        self.assertAlmostEqual(k["fx"]["acb"], 137.03, places=2)
        self.assertAlmostEqual(k["fx"]["gain"], 7.97, places=2)
        self.assertFalse(k["fx"]["superficial"])
        c = sends[CB_USDC_ID]
        self.assertLess(c["fx"]["gain"], 0)
        self.assertTrue(c["fx"]["superficial"])

    @rule("CA-CRYPTO-05")
    def test_d_set_write_and_run_books_the_lines(self):
        for sid, dec in ((PAY_ID, "payment"), (BTC_ID, "gift"),
                         (KR_USDC_ID, "gift"), (CB_USDC_ID, "gift")):
            r = _cli(self.root, self.home, "crypto-sends", "crypto",
                     "--set", f"{sid}={dec}", "--note", "synthetic")
            self.assertEqual(r.returncode, 0, r.stderr)
        bad = _cli(self.root, self.home, "crypto-sends", "crypto", "--set",
                   f"{PAY_ID}=donation")
        self.assertNotEqual(bad.returncode, 0)
        bad = _cli(self.root, self.home, "crypto-sends", "crypto", "--set",
                   "kr-19990101T000000-XYZ-1=gift")
        self.assertNotEqual(bad.returncode, 0)
        man = json.loads((self.root / "inputs" / "crypto" / "sends.json")
                         .read_text())
        self.assertEqual(man["sends"][PAY_ID]["decision"], "payment")
        self.assertEqual(man["sends"][PAY_ID]["note"], "synthetic")

        w = _cli(self.root, self.home, "crypto-sends", "crypto", "--write")
        self.assertEqual(w.returncode, 0, w.stderr)
        tt = self.root / "inputs" / "crypto" / "crypto_sends.tt"
        body = tt.read_text()
        lines = [ln for ln in body.splitlines()
                 if ln and not ln.startswith("#")]
        self.assertEqual(lines, [
            # R1-26: the matched ATOM send arrived 0.002 short — the
            # network fee, sold at the Coinbase spot price.
            "BUYSELL 2025-06-22 10:05:10 ATOM -0.002 CAD 9.85 0.02 0",
            "BUYSELL 2025-08-01 08:00:00 BTC -0.001 CAD 150000 150.00 0",
            "BUYSELL 2026-04-13 17:30:00 QZL -2.5 CAD 2.50442 6.26 0"])
        self.assertIn("cb-20250622T100510-ATOM-80.002-fee: network fee",
                      body)
        self.assertIn(PAY_ID, body)
        self.assertIn("Yahoo QZL55501-USD", body)
        self.assertNotIn("USDC", "\n".join(lines))
        # Idempotent: same decisions -> same bytes, file not rewritten.
        m0 = tt.stat().st_mtime_ns
        w2 = _cli(self.root, self.home, "crypto-sends", "crypto", "--write")
        self.assertEqual(w2.returncode, 0, w2.stderr)
        self.assertEqual(tt.read_text(), body)
        self.assertEqual(tt.stat().st_mtime_ns, m0)

        r = _cli(self.root, self.home, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        base = json.loads((self.root / "work" / "crypto_base.json")
                          .read_text())["transactions"]
        pay = [t for t in base if t["symbol"] == "QZL"
               and t["action"] == "BUYSELL"
               and abs(float(t["quantity"]) + 2.5) < 1e-12]
        self.assertEqual(len(pay), 1)
        self.assertAlmostEqual(float(pay[0]["net_amount"]), 6.26, places=2)
        coin_fee = [t for t in base if t["symbol"] == "ATOM"
                    and t["action"] == "BUYSELL"
                    and abs(float(t["quantity"]) + 0.002) < 1e-12]
        self.assertEqual(len(coin_fee), 1)
        # Nothing left to classify: the Hybrid Earn sweep is automatic.
        self.assertNotIn("not yet classified", r.stderr + r.stdout)

    def test_e_duplicate_hand_written_line_is_flagged(self):
        dup = self.root / "inputs" / "crypto" / "ldo_payment.tt"
        dup.write_text("BUYSELL 2026-04-13 17:30:00 QZL -2.5 CAD 2.50442 "
                       "6.26 0\n")
        try:
            _cli(self.root, self.home, "crypto-sends", "crypto", "--set",
                 f"{PAY_ID}=payment")
            r = _cli(self.root, self.home, "crypto-sends", "crypto")
            self.assertIn("ldo_payment.tt", r.stdout + r.stderr)
            self.assertIn("counted twice", r.stdout + r.stderr)
        finally:
            dup.unlink()

    def test_f_checklist_step(self):
        from taxjson.lib import checklist as cl
        from taxjson.lib.tomlcompat import tomllib
        cfg = tomllib.loads((self.root / "taxjson.toml").read_text())
        ctx = cl.Ctx(root=self.root, cfg=cfg, year=2026, today=date.today(),
                     run_sub=lambda argv, timeout=900: (0, "", ""))
        # An explicit decision still overrides the automatic one.
        r = _cli(self.root, self.home, "crypto-sends", "crypto", "--set",
                 f"{HYBRID_ID}=self")
        self.assertEqual(r.returncode, 0, r.stderr)
        for sid, dec in ((PAY_ID, "payment"), (BTC_ID, "gift"),
                         (KR_USDC_ID, "gift"), (CB_USDC_ID, "gift")):
            _cli(self.root, self.home, "crypto-sends", "crypto", "--set",
                 f"{sid}={dec}")
        _cli(self.root, self.home, "crypto-sends", "crypto", "--write")
        res = cl.DETECTORS["crypto-sends"](ctx)
        self.assertEqual(res.status, "done", res.detail)

    def test_g_hand_written_crypto_sends_tt_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _project(td)
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            mine = root / "inputs" / "crypto" / "crypto_sends.tt"
            mine.write_text("# my own notes\n")
            _cli(root, home, "crypto-sends", "crypto", "--set",
                 f"{PAY_ID}=payment")
            w = _cli(root, home, "crypto-sends", "crypto", "--write")
            self.assertNotEqual(w.returncode, 0)
            self.assertEqual(mine.read_text(), "# my own notes\n")


class TestUsGift(unittest.TestCase):
    @rule("US-SEND-02")
    def test_us_project_refuses_gift(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _project(td, country="usa")
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2026\ncountry = "usa"\n'
                'base_currency = "USD"\nsource_currencies = ["CAD"]\n'
                '[accounts.crypto]\ntype = "taxable"\ncrypto = true\n')
            _cad_usd_rates_file(root / "work" / "to_base.csv")
            # Coinbase rows are CAD-priced; keep the US fixture to Kraken.
            (root / "inputs" / "crypto" / "cb_2025.csv").unlink()
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            g = _cli(root, home, "crypto-sends", "crypto", "--set",
                     f"{PAY_ID}=gift")
            self.assertNotEqual(g.returncode, 0)
            self.assertIn("not a sale", g.stderr)
            p = _cli(root, home, "crypto-sends", "crypto", "--set",
                     f"{PAY_ID}=payment")
            self.assertEqual(p.returncode, 0, p.stderr)

    @rule("US-SEND-01")
    def test_us_payment_is_written_as_a_sale_at_fair_value(self):
        # A2-0851: a payment in a US project is a sale at fair value in
        # crypto_sends.tt, booked by the next run (US-SEND-01).
        with tempfile.TemporaryDirectory() as td:
            root, home = _project(td, country="usa")
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2026\ncountry = "usa"\n'
                'base_currency = "USD"\nsource_currencies = ["CAD"]\n'
                '[accounts.crypto]\ntype = "taxable"\ncrypto = true\n')
            _cad_usd_rates_file(root / "work" / "to_base.csv")
            (root / "inputs" / "crypto" / "cb_2025.csv").unlink()
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            p = _cli(root, home, "crypto-sends", "crypto", "--set",
                     f"{PAY_ID}=payment")
            self.assertEqual(p.returncode, 0, p.stderr)
            w = _cli(root, home, "crypto-sends", "crypto", "--write")
            self.assertEqual(w.returncode, 0, w.stderr)
            body = (root / "inputs" / "crypto" / "crypto_sends.tt"
                    ).read_text()
            lines = [ln for ln in body.splitlines()
                     if ln.startswith("BUYSELL") and " QZL " in ln]
            self.assertEqual(len(lines), 1, body)
            self.assertTrue(lines[0].startswith(
                "BUYSELL 2026-04-13 17:30:00 QZL -2.5 "), lines[0])
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            base = json.loads((root / "work" / "crypto_base.json")
                              .read_text())["transactions"]
            sale = [t for t in base if t["symbol"] == "QZL"
                    and t["action"] == "BUYSELL"
                    and abs(float(t["quantity"]) + 2.5) < 1e-12]
            self.assertEqual(len(sale), 1)
            self.assertGreater(float(sale[0]["net_amount"]), 0.0)


if __name__ == "__main__":
    unittest.main()
