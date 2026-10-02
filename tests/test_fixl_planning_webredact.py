"""Planning low round: web UI and `taxjson redact` findings.

Web
  R1-266  /api/whatif unknown account is a 404; a symbol the account does
          not hold is a 404 page; /healthz reports a config that no longer
          loads; a lower-case price currency gets no false fallback note
  R1-349  the web loader refuses the account names `taxjson run` refuses
  S078-17 a corrupt wash_radar_<acct>.json is an error banner, not a
          silent fall-back to the generation-time .rpt
  S078-18 a wrong-shape radar JSON / holdings.toml is an error banner
          (not a 500)
  S078-22 the stale banner sees an mtime-preserving edit and a deleted
          input (content fingerprint)
  S079-03 what-if 'Permanently denied' is the registered-account share
  S079-04 what-if and find_holding take a symbol in any case
  S079-09 `taxjson serve` on a non-UTF-8 taxjson.toml: one line, no
          traceback

Redact
  R1-352  a line the csv module cannot split still has its account id
          collected; a file-name id keeps the content's placeholder
  S036-14 a US 'City, ST ZIP' preamble line is blanked
  S036-15 the street regex never swallows the next CSV field
  S036-18 one invocation never gives two accounts (or two files) the same
          pseudonym / output name
  S036-23 `broker_account = "..."` / `account = "..."` keys are ids
  S036-24 / S037-01  an unreadable input or --out is one line, and the
          rest of the batch is still redacted
  S037-03 address columns of a columnar AccountInformation section
  S037-08 Phone:/SIN:/Payee:/Beneficiary: label cells redact the next cell

All data is synthetic.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Split so the source carries no address/postal literal for PII scanners.
_BAKER = "221 " + "Baker St"
_MAIN = "123 " + "Main St"
_ELM = "12 " + "Elm Street"
_POST = "S6H" + " 1A1"

try:
    from fastapi.testclient import TestClient  # noqa: F401
    _HAVE_WEB = True
except Exception:          # pragma: no cover - extra not installed
    _HAVE_WEB = False


def _d(days):
    return (date.today() + timedelta(days=days)).isoformat()


def _row(symbol, qty, net, date_, settle=None, acct="margin", price=None):
    return {"action": "BUYSELL", "date": date_,
            "date_settle": settle or date_, "symbol": symbol,
            "quantity": qty,
            "price": price if price is not None else abs(net / qty),
            "net_amount": net, "currency": "CAD", "account": acct}


def _project(tmp, books, *, accounts=None, country="canada"):
    root = Path(tmp)
    (root / "work").mkdir(exist_ok=True)
    (root / "reports").mkdir(exist_ok=True)
    accounts = accounts or {"margin": 'type = "taxable"\n'}
    cur = "USD" if country == "usa" else "CAD"
    cfg = (f'[settings]\nyear = {date.today().year}\n'
           f'country = "{country}"\nbase_currency = "{cur}"\n')
    for name, body in accounts.items():
        cfg += f'[accounts."{name}"]\n{body}'
    (root / "taxjson.toml").write_text(cfg)
    for name, rows in books.items():
        (root / "work" / f"{name}_base.json").write_text(
            json.dumps({"transactions": rows}))
    return root


def _ctx(root):
    from taxjson.web.context import ProjectContext
    return ProjectContext.load(root)


# ====================================================================== web
class TestWebRoutes(unittest.TestCase):
    """R1-266 / S078-18: status codes and error banners."""

    def setUp(self):
        if not _HAVE_WEB:
            self.skipTest("web extra not installed")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = _project(self.tmp.name, {"margin": [
            _row("AAA.TO", 100, -2000.0, _d(-200))]})
        (self.root / "reports" / "margin_holdings.toml").write_text(
            '[[holding]]\nsymbol = "AAA.TO"\nquantity = 100.0\n')
        from taxjson.web.app import create_app
        self.c = TestClient(create_app(_ctx(self.root)), base_url="http://127.0.0.1")

    def test_api_whatif_unknown_account_is_404(self):
        r = self.c.get("/api/whatif", params=dict(
            account="nosuch", symbol="AAA.TO", qty=1, price=10))
        self.assertEqual(r.status_code, 404)
        self.assertFalse(r.json()["ok"])

    def test_unheld_symbol_page_is_404(self):
        self.assertEqual(
            self.c.get("/holdings/margin/AAA.TO").status_code, 200)
        r = self.c.get("/holdings/margin/NOPE.TO")
        self.assertEqual(r.status_code, 404)
        self.assertIn("NOPE.TO", r.text)

    def test_healthz_reports_a_config_that_no_longer_loads(self):
        cfg = self.root / "taxjson.toml"
        cfg.write_text(cfg.read_text() + "[settings\n")
        st = cfg.stat()
        os.utime(cfg, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
        r = self.c.get("/healthz").json()
        self.assertFalse(r["ok"])
        self.assertIn("taxjson.toml", r["config_error"])

    def test_wrong_shape_holdings_is_a_banner_not_500(self):
        (self.root / "reports" / "margin_holdings.toml").write_text(
            "holding = 5\n")
        r = self.c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("margin_holdings.toml", r.text)
        self.assertEqual(self.c.get("/holdings?account=margin").status_code,
                         200)
        r = self.c.get("/api/holdings", params={"account": "margin"})
        self.assertEqual(r.status_code, 500)
        self.assertFalse(r.json()["ok"])

    def test_wrong_shape_radar_json_is_a_banner_not_500(self):
        (self.root / "reports" / "wash_radar_margin.rpt").write_text("")
        for doc in ("[1, 2]", '{"sections": [1]}',
                    '{"sections": [{"title": "X", "rows": [1]}]}'):
            (self.root / "reports" / "wash_radar_margin.json").write_text(
                doc)
            r = self.c.get("/wash-radar?account=margin")
            self.assertEqual(r.status_code, 200, doc)
            self.assertIn("wash_radar_margin.json", r.text)


class TestWebRadarJson(unittest.TestCase):
    """S078-17: a corrupt sidecar is an error, not the stale .rpt."""

    def test_corrupt_json_raises_instead_of_stale_rpt(self):
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin": []})
            rep = root / "reports"
            (rep / "wash_radar_margin.rpt").write_text(
                "--- LOCKED ---\nSHOP.TO | 10 | 0 | 2026-09-25 (3d) | "
                "do not sell\n")
            (rep / "wash_radar_margin.json").write_text('{"sections": [')
            with self.assertRaises(data.ReportArtifactError) as cm:
                data.wash_radar_sections(_ctx(root), "margin")
            self.assertIn("wash_radar_margin.json", str(cm.exception))


class TestWebLoader(unittest.TestCase):
    """R1-349 / S079-09."""

    def test_traversal_account_name_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {}, accounts={
                "margin": 'type = "taxable"\n',
                "../../outside": 'type = "taxable"\n'})
            with self.assertRaises(ValueError) as cm:
                _ctx(root)
            self.assertIn("not a valid account name", str(cm.exception))

    def test_combined_name_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {}, accounts={
                "COMBINED": 'type = "taxable"\n'})
            with self.assertRaises(ValueError):
                _ctx(root)

    def test_serve_non_utf8_config_is_one_line(self):
        from taxjson.web import server
        try:
            import uvicorn  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("uvicorn not installed")
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {})
            cfg = root / "taxjson.toml"
            cfg.write_bytes(cfg.read_bytes() + "# caf\xe9\n".encode("latin-1"))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = server.serve(root, port=18765)
        self.assertEqual(rc, 1)
        self.assertIn("taxjson serve:", err.getvalue())
        self.assertIn("not valid TOML", err.getvalue())

    def test_serve_invalid_account_type_is_one_line(self):
        from taxjson.web import server
        try:
            import uvicorn  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("uvicorn not installed")
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {}, accounts={"m": 'type = "Taxable"\n'})
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = server.serve(root, port=18765)
        self.assertEqual(rc, 1)
        self.assertIn("taxjson serve:", err.getvalue())


class TestWebFreshness(unittest.TestCase):
    """S078-22: with a recorded fingerprint, content decides."""

    def test_mtime_preserving_edit_and_deleted_input_are_stale(self):
        from taxjson.lib.checklist import record_input_fingerprint
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {})
            inp = root / "inputs" / "margin"
            inp.mkdir(parents=True)
            a, b = inp / "a.csv", inp / "b.csv"
            a.write_text("x,1\n")
            b.write_text("y,2\n")
            cfg = {"accounts": {"margin": {}}}
            record_input_fingerprint(root, cfg)
            (root / "reports" / "margin.sum").write_text("sum\n")
            self.assertFalse(data.freshness(_ctx(root))["stale"])
            st = a.stat()
            a.write_text("x,9\n")
            os.utime(a, ns=(st.st_atime_ns, st.st_mtime_ns - 3600 * 10**9))
            f = data.freshness(_ctx(root))
            self.assertTrue(f["stale"])
            self.assertIn("a.csv", f["why"])
            a.write_text("x,1\n")
            b.unlink()
            f = data.freshness(_ctx(root))
            self.assertTrue(f["stale"])
            self.assertIn("removed", f["why"])


class TestWhatIf(unittest.TestCase):
    """S079-03 / S079-04 / R1-266(1)."""

    def _books(self):
        return {
            "margin": [_row("AAA.TO", 100, -2000.0, _d(-200)),
                       _row("AAA.TO", 50, -550.0, _d(-5))],
            "rrsp": [_row("AAA.TO", 30, -330.0, _d(-4), acct="rrsp")]}

    def _whatif(self, **kw):
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, self._books(), accounts={
                "margin": 'type = "taxable"\n',
                "rrsp": 'type = "sheltered"\n'})
            return data.what_if_sell(_ctx(root), "margin",
                                     kw.pop("symbol", "AAA.TO"), 100, 12.0,
                                     **kw)

    def test_permanent_share_is_the_registered_part(self):
        r = self._whatif()
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["is_wash_sale"])
        self.assertGreater(r["permanently_disallowed"], 0)
        self.assertLess(r["permanently_disallowed"], r["disallowed_amount"])

    def test_lower_case_symbol(self):
        r = self._whatif(symbol="aaa.to")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["symbol"], "AAA.TO")

    def test_find_holding_any_case(self):
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin": []})
            (root / "reports" / "margin_holdings.toml").write_text(
                '[[holding]]\nsymbol = "ZZZ.TO"\nquantity = 100.0\n')
            h = data.find_holding(_ctx(root), "margin", "zzz.to")
        self.assertIsNotNone(h)
        self.assertEqual(h["symbol"], "ZZZ.TO")

    def test_lower_case_currency_has_no_fallback_note(self):
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin": []})
            on = _d(0)
            (root / "work" / "to_base.csv").write_text(
                f"{on} 12:00:00 USD CAD 1.41450\n")
            up = data._price_to_base(_ctx(root), 250.0, "USD", on)
            low = data._price_to_base(_ctx(root), 250.0, "usd", on)
        self.assertEqual(up, low)
        self.assertIsNone(low[2])


# =================================================================== redact
class TestRedactLow(unittest.TestCase):
    def _rt(self, text):
        from taxjson.bin.taxjson_redact import redact_text
        return redact_text(text)

    def test_unsplittable_line_still_collects_its_id(self):   # R1-352 (a)
        text = ("Date,Account #,Description,Amount\n"
                "2025-01-02,55511112,short,1.00\n"
                "2025-01-03,55522224," + "x" * 131082 + ",2.00\n")
        out, rep = self._rt(text)
        self.assertNotIn("55522224", out)
        self.assertEqual(len(rep.accounts), 2)
        self.assertEqual(rep.unreplaced, {})

    def test_file_name_keeps_the_content_placeholder(self):   # R1-352 (b)
        from taxjson.bin.taxjson_redact import redacted_name, redact_text
        _, rep = redact_text("Trades,Header,Account\nTrades,Data,U55512345\n")  # pii-ok
        ph = rep.accounts["U55512345"]  # pii-ok
        for name in ("U55512345_2025.csv", "U55512345.2025.dividends.csv"):  # pii-ok
            got = redacted_name(Path(name), rep.accounts)
            self.assertIn(ph, got)
            self.assertNotIn("U55512345", got)  # pii-ok
        # A name-only 8-digit id gets its own placeholder, not a renumber
        # of the placeholder digits.
        got = redacted_name(Path("U55512345_55599999.csv"), rep.accounts)  # pii-ok (synthetic)
        self.assertIn(ph, got)
        self.assertNotIn("55599999", got)

    def test_us_city_state_zip_preamble(self):                # S036-14
        text = ("Jane Sample\n" + _MAIN + "\nSpringfield, IL 62704\n"
                "Date,Symbol,Quantity,Amount\n2025-01-02,AAPL,10,-1500\n")
        out, _ = self._rt(text)
        self.assertNotIn("Springfield", out)
        self.assertNotIn("62704", out)

    def test_street_does_not_swallow_the_next_field(self):    # S036-15
        for line in ("AccountInformation,Data,U55500001,Main," + _BAKER + ","  # pii-ok
                     "Unit 5,Moose Jaw\n",
                     "2025-01-02," + _ELM + ",Apt 3,100.00\n",
                     "2025-01-02,Unit 5," + _BAKER + ",100.00\n"):
            out, _ = self._rt("a,b,c,d\n" + line)
            self.assertEqual(out.splitlines()[1].count(","),
                             line.count(","), (line, out))
            self.assertNotIn("Baker", out)
            self.assertNotIn("Elm Street", out)
        # Inside ONE field the unit is still part of the address.
        out, _ = self._rt('a,b\n2025-01-02,"' + _BAKER + ', Unit 5"\n')
        self.assertNotIn("Unit 5", out)

    def test_broker_account_key(self):                        # S036-23
        text = ('[meta]\nbroker = "questrade"\n'
                'broker_account = "55576543"\n'  # pii-ok
                '[[holding]]\naccount = "55576544"\nsymbol = "XIU.TO"\n'  # pii-ok
                '{"account_id": "55576545", "note": "x"}\n')  # pii-ok
        out, rep = self._rt(text)
        for v in ("55576543", "55576544", "55576545"):
            self.assertNotIn(v, out)
        self.assertEqual(len(rep.accounts), 3)
        # A plain name value is not an id.
        out, rep = self._rt('account = "margin"\n')
        self.assertIn('"margin"', out)
        self.assertEqual(rep.accounts, {})

    def test_flex_account_information_address_columns(self):  # S037-03
        hdr = ("ClientAccountID,AccountAlias,Name,AccountType,Street,Street2,"
               "City,State,Country,PostalCode,PrimaryEmail,Currency\n")
        row = ("U55500001,margin-1,Zelda Quixote,Individual," + _BAKER + ","  # pii-ok
               "Unit 5,Moose Jaw,SK,Canada," + _POST + ",z@example.com,CAD\n")
        for text in ("AccountInformation,Header," + hdr
                     + "AccountInformation,Data," + row, hdr + row):
            out, _ = self._rt(text)
            data_line = out.splitlines()[1]
            for v in ("Zelda", "Unit 5", "Moose Jaw", ",SK,", "Canada",
                      "margin-1"):
                self.assertNotIn(v, data_line, (v, data_line))
            self.assertIn("CAD", data_line)
            self.assertEqual(data_line.count(","),
                             text.splitlines()[1].count(","))

    def test_label_cell_contact_values(self):                 # S037-08
        for line, val in (("Phone:,4165550123", "4165550123"),
                          ("SIN:,046454286", "046454286"),  # pii-ok (CRA sample SIN)
                          ("Payee:,Zelda Quixote", "Zelda"),
                          ("Beneficiary:,Zelda Quixote", "Zelda")):
            out, rep = self._rt(line + "\n")
            self.assertNotIn(val, out, line)
            self.assertTrue(rep.found_anything(), line)


class TestRedactBatch(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_redact",
             "--no-denylist", *args], capture_output=True, text=True,
            cwd=REPO_ROOT)

    def test_two_accounts_two_names_two_pseudonyms(self):     # S036-18
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            a = t / "U5551111_2025.csv"   # pii-ok (synthetic)
            b = t / "U5552222_2025.csv"   # pii-ok (synthetic)
            a.write_text("Trades,Header,Account,Qty\n"
                         "Trades,Data,U5551111,1\n")   # pii-ok
            b.write_text("Trades,Header,Account,Qty\n"
                         "Trades,Data,U5552222,2\n")   # pii-ok
            r = self._run("--force", "--out", str(t / "out"), str(a), str(b))
            self.assertEqual(r.returncode, 0, r.stderr)
            outs = sorted((t / "out").iterdir())
            self.assertEqual(len(outs), 2, outs)
            texts = [p.read_text() for p in outs]
            ids = [ln.split(",")[2] for tx in texts
                   for ln in tx.splitlines()[1:]]
            self.assertEqual(len(set(ids)), 2, ids)

    def test_same_basename_is_not_overwritten(self):          # S036-18
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            for n in ("a", "b"):
                (t / n).mkdir()
                (t / n / "activity.csv").write_text(f"Date,Qty\n2025,{n}\n")
            r = self._run("--force", "--out", str(t / "out"),
                          str(t / "a" / "activity.csv"),
                          str(t / "b" / "activity.csv"))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("already written", r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            self.assertEqual(len(list((t / "out").iterdir())), 1)

    @unittest.skipIf(os.geteuid() == 0, "root reads mode-000 files")
    def test_unreadable_input_does_not_stop_the_batch(self):  # S037-01
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            bad = t / "a_unreadable.csv"
            bad.write_text("Date,Qty\n2025,1\n")
            ok = t / "b_ok.csv"
            ok.write_text("Date,Qty\n2025,2\n")
            bad.chmod(0)
            try:
                r = self._run("--out", str(t / "out"), str(bad), str(ok))
            finally:
                bad.chmod(0o600)
            self.assertEqual(r.returncode, 1)
            self.assertNotIn("Traceback", r.stderr)
            self.assertIn("a_unreadable.csv", r.stderr)
            self.assertTrue((t / "out" / "b_ok.redacted.csv").exists())

    @unittest.skipIf(os.geteuid() == 0, "root writes anywhere")
    def test_unwritable_out_is_one_line(self):                # S036-24
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            src = t / "a.csv"
            src.write_text("Date,Qty\n2025,1\n")
            ro = t / "ro"
            ro.mkdir()
            ro.chmod(0o555)
            try:
                r = self._run("--out", str(ro / "x"), str(src))
            finally:
                ro.chmod(0o755)
            self.assertEqual(r.returncode, 1)
            self.assertNotIn("Traceback", r.stderr)
            self.assertIn("taxjson redact:", r.stderr)


if __name__ == "__main__":
    unittest.main()
