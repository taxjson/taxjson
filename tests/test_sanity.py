"""`taxjson sanity` — cross-check of positions against external
holdings TOML files (portoml-style). Bare arguments (account names and
.toml files) form one AGGREGATE group: combined positions vs combined
holdings, per symbol — quick, but blind to a position sitting in the
wrong account. `ACCOUNT[+ACCOUNT]=FILE[+FILE]` arguments form PAIRED
groups checked independently (many-to-many: margin spans two broker
exports). Option symbols follow their underlying's ticker.map rename."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True)


def _gains(root, acct, inv):
    (root / "work").mkdir(exist_ok=True)
    (root / "work" / f"{acct}_gains.json").write_text(json.dumps(
        {"summary": {"year": "2026"}, "transactions": [],
         "inventory": [{"symbol": s, "qty": q, "total_cost": 1.0}
                       for s, q in inv.items()]}))


def _holdings_toml(path, account, holdings):
    rows = "".join(
        f'[[holding]]\nsymbol = "{s}"\nquantity = {q}\n'
        f'asset_type = "equity"\n\n' for s, q in holdings.items())
    path.write_text(f'schema_version = "1.0"\n[meta]\n'
                    f'account = "{account}"\n\n{rows}')


class TestSanity(unittest.TestCase):
    def _project(self, tmp):
        root = Path(tmp)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            '[accounts.margin]\ntype = "taxable"\n'
            '[accounts.rrsp]\ntype = "sheltered"\n')
        # margin spans TWO broker accounts; rrsp shares tickers with
        # margin (same strategy) but different quantities.
        _gains(root, "margin", {"ALK.TO": 30000, "XIU.TO": 100,
                                "ANET.US": 50})
        _gains(root, "rrsp", {"XIU.TO": 40, "ANET.US": 75})
        ext = root / "ext"
        ext.mkdir()
        _holdings_toml(ext / "U1_holdings.toml", "U1",
                       {"ALK.TO": 30000, "ANET.US": 50})
        _holdings_toml(ext / "U2_holdings.toml", "U2",
                       {"XIU.TO": 100})
        _holdings_toml(ext / "U3_holdings.toml", "U3",
                       {"XIU.TO": 40, "ANET.US": 75})
        return root

    def _mix(self, root):
        e = root / "ext"
        return ["margin", "rrsp",
                str(e / "U1_holdings.toml"),
                str(e / "U2_holdings.toml"),
                str(e / "U3_holdings.toml")]

    def test_free_mix_aggregate_clean_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            r = _run(root, "sanity", *self._mix(root), "--json")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        doc = json.loads(r.stdout)
        self.assertTrue(doc["clean"])
        self.assertEqual(doc["accounts"], ["margin", "rrsp"])
        self.assertEqual(
            sorted(f["file_account"] for f in doc["files"]),
            ["U1", "U2", "U3"])

    def test_discrepancies_detected_and_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            # Drift rrsp: qty change + a symbol only on the broker side.
            _holdings_toml(root / "ext" / "U3_holdings.toml", "U3",
                           {"XIU.TO": 42, "ANET.US": 75,
                            "NEW.TO": 10})
            r = _run(root, "sanity", *self._mix(root), "--json")
        self.assertEqual(r.returncode, 1)
        doc = json.loads(r.stdout)
        issues = {(d["symbol"], d["issue"]) for d in doc["discrepancies"]}
        self.assertIn(("XIU.TO", "QTY_MISMATCH"), issues)
        self.assertIn(("NEW.TO", "MISSING_IN_TAXJSON"), issues)

    def test_duplicate_account_counted_once(self):
        # Real data: `sanity margin rrsp rrsp2 tfsa rrsp2 ...` doubled
        # every rrsp2 position (FNV 80-vs-40) while the header printed
        # the deduplicated account list.
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            r = _run(root, "sanity", "margin", "rrsp", "rrsp", "margin",
                     *self._mix(root)[2:], "--json")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertTrue(json.loads(r.stdout)["clean"])
        self.assertIn("given more than once", r.stderr)

    def test_duplicate_file_counted_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            e = root / "ext"
            r = _run(root, "sanity", *self._mix(root),
                     str(e / "U1_holdings.toml"), "--json")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertTrue(json.loads(r.stdout)["clean"])
        self.assertIn("given more than once", r.stderr)

    def test_bad_items_are_usage_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            e = root / "ext"
            # Neither an account nor a file (directories included).
            r = _run(root, "sanity", "nope",
                     str(e / "U1_holdings.toml"))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("neither an account", r.stderr)
            r = _run(root, "sanity", "margin", str(e))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("neither an account", r.stderr)
            # Need at least one of each.
            r = _run(root, "sanity", "margin")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("at least one ACCOUNT and one FILE",
                          r.stderr)

    def test_uncovered_accounts_are_notes_not_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            e = root / "ext"
            r = _run(root, "sanity", "margin",
                     str(e / "U1_holdings.toml"),
                     str(e / "U2_holdings.toml"), "--json")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["uncovered_accounts"], ["rrsp"])

    def test_option_symbols_follow_underlying_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            (root / "ticker.map").write_text("TOBASE AEM.US AEM.TO\n")
            # taxjson holds the consolidated .TO option; broker file
            # reports the actual .US listing.
            _gains(root, "margin", {"ALK.TO": 30000, "XIU.TO": 100,
                                    "ANET.US": 50,
                                    "AEM280121C00155000.TO": 1})
            _holdings_toml(root / "ext" / "U1_holdings.toml", "U1",
                           {"ALK.TO": 30000, "ANET.US": 50,
                            "AEM280121C00155000.US": 1})
            r = _run(root, "sanity", *self._mix(root), "--json")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertTrue(json.loads(r.stdout)["clean"])

    # ---- paired form: ACCOUNT[+ACCOUNT]=FILE[+FILE] --------------------

    def test_paired_groups_clean(self):
        # margin spans two broker accounts (IBKR + Webull style):
        # `margin=U1+U2`; rrsp is a plain 1:1 pairing.
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            e = root / "ext"
            r = _run(root, "sanity",
                     f"margin={e / 'U1_holdings.toml'}"
                     f"+{e / 'U2_holdings.toml'}",
                     f"rrsp={e / 'U3_holdings.toml'}", "--json")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        doc = json.loads(r.stdout)
        self.assertTrue(doc["clean"])
        self.assertEqual(doc["accounts"], ["margin", "rrsp"])
        self.assertEqual(len(doc["groups"]), 2)
        self.assertTrue(all(g["paired"] for g in doc["groups"]))
        by = {tuple(g["accounts"]): g for g in doc["groups"]}
        self.assertEqual(len(by[("margin",)]["files"]), 2)
        self.assertEqual(len(by[("rrsp",)]["files"]), 1)

    def test_paired_catches_position_in_wrong_account(self):
        # The case the aggregate form is blind to: totals agree, but
        # ANET sits in the wrong account on the taxjson side. Aggregate
        # says OK; the paired form must flag it per group.
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            e = root / "ext"
            _gains(root, "margin", {"ALK.TO": 30000, "XIU.TO": 100,
                                    "ANET.US": 75})
            _gains(root, "rrsp", {"XIU.TO": 40, "ANET.US": 50})
            agg = _run(root, "sanity", *self._mix(root), "--json")
            pair = _run(root, "sanity",
                        f"margin={e / 'U1_holdings.toml'}",
                        f"margin={e / 'U2_holdings.toml'}",
                        f"rrsp={e / 'U3_holdings.toml'}", "--json")
            text = _run(root, "sanity",
                        f"margin={e / 'U1_holdings.toml'}"
                        f"+{e / 'U2_holdings.toml'}",
                        f"rrsp={e / 'U3_holdings.toml'}")
        self.assertEqual(agg.returncode, 0, agg.stderr + agg.stdout)
        self.assertEqual(pair.returncode, 1, pair.stderr + pair.stdout)
        doc = json.loads(pair.stdout)
        # Repeated `margin=` merged into ONE group.
        self.assertEqual(len(doc["groups"]), 2)
        flagged = {(tuple(d["accounts"]), d["symbol"], d["issue"])
                   for d in doc["discrepancies"]}
        self.assertEqual(flagged, {
            (("margin",), "ANET.US", "QTY_MISMATCH"),
            (("rrsp",), "ANET.US", "QTY_MISMATCH")})
        self.assertEqual(text.returncode, 1, text.stderr + text.stdout)
        self.assertIn("paired check", text.stdout)
        # Every file read is listed by PATH under its group.
        self.assertIn(f"file:     {e / 'U1_holdings.toml'}", text.stdout)
        self.assertIn(f"file:     {e / 'U2_holdings.toml'}", text.stdout)
        self.assertIn(f"file:     {e / 'U3_holdings.toml'}", text.stdout)
        self.assertIn("ACCOUNTS", text.stdout)
        self.assertIn("2 discrepancy(ies).", text.stdout)

    def test_many_accounts_to_one_file(self):
        # One broker export covering two taxjson accounts: `a+b=FILE`.
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            e = root / "ext"
            _holdings_toml(e / "flex.toml", "FLEX",
                           {"ALK.TO": 30000, "ANET.US": 125,
                            "XIU.TO": 140})
            r = _run(root, "sanity", f"margin+rrsp={e / 'flex.toml'}",
                     "--json")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        doc = json.loads(r.stdout)
        self.assertTrue(doc["clean"])
        self.assertEqual(doc["groups"][0]["accounts"],
                         ["margin", "rrsp"])

    def test_paired_and_aggregate_mix(self):
        # rrsp paired, margin's two files left bare with margin: the
        # bare items form their own aggregate group alongside.
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            e = root / "ext"
            r = _run(root, "sanity", "margin",
                     str(e / "U1_holdings.toml"),
                     f"rrsp={e / 'U3_holdings.toml'}",
                     str(e / "U2_holdings.toml"), "--json")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        doc = json.loads(r.stdout)
        self.assertTrue(doc["clean"])
        kinds = sorted((tuple(g["accounts"]), g["paired"])
                       for g in doc["groups"])
        self.assertEqual(kinds, [(("margin",), False),
                                 (("rrsp",), True)])

    def test_item_in_two_groups_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            e = root / "ext"
            r = _run(root, "sanity",
                     f"margin={e / 'U1_holdings.toml'}",
                     f"margin+rrsp={e / 'U3_holdings.toml'}")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("more than one group", r.stderr)
            r = _run(root, "sanity", "margin",
                     str(e / "U1_holdings.toml"),
                     f"rrsp={e / 'U1_holdings.toml'}")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("more than one group", r.stderr)
            # Malformed pair: bad account, missing file, empty side.
            r = _run(root, "sanity", f"nope={e / 'U1_holdings.toml'}")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("is not an account", r.stderr)
            r = _run(root, "sanity", f"margin={e / 'missing.toml'}")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("is not an existing", r.stderr)
            r = _run(root, "sanity", "margin=")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("paired form is", r.stderr)

    def test_option_matched_via_underlying_root(self):
        # Real case: IB names the Montréal contract on RCI.B
        # `RCI.B 16JUL27 55 C` (taxjson keys RCI.B270716C00055000.TO)
        # while the positions export keys it by the exchange option
        # root `RCI...` with underlying = "RCI.B.TO". Same contract:
        # the fallback re-keys the FILE's row to the underlying
        # spelling — but only when the file's spelling has no
        # taxjson counterpart, so a genuine mismatch still shows.
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            e = root / "ext"
            _gains(root, "margin", {"ALK.TO": 30000, "XIU.TO": 100,
                                    "ANET.US": 50,
                                    "RCI.B270716C00055000.TO": 20})
            (e / "U1_holdings.toml").write_text(
                'schema_version = "1.0"\n[meta]\naccount = "U1"\n\n'
                '[[holding]]\nsymbol = "ALK.TO"\nquantity = 30000\n'
                'asset_type = "equity"\n\n'
                '[[holding]]\nsymbol = "ANET.US"\nquantity = 50\n'
                'asset_type = "equity"\n\n'
                '[[holding]]\nsymbol = "RCI270716C00055000.TO"\n'
                'quantity = 20\nasset_type = "option"\n'
                'underlying = "RCI.B.TO"\nright = "call"\n'
                'strike = 55.0\n\n')
            r = _run(root, "sanity", f"margin={e / 'U1_holdings.toml'}"
                     f"+{e / 'U2_holdings.toml'}", "--json")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            doc = json.loads(r.stdout)
            self.assertTrue(doc["clean"])
            self.assertEqual(
                doc["groups"][0]["matched_via_underlying"],
                [{"file_symbol": "RCI270716C00055000.TO",
                  "taxjson_symbol": "RCI.B270716C00055000.TO"}])
            text = _run(root, "sanity", "margin",
                        str(e / "U1_holdings.toml"),
                        str(e / "U2_holdings.toml"))
            self.assertEqual(text.returncode, 0, text.stdout)
            self.assertIn("via its underlying", text.stdout)
            # taxjson ALSO holds the file's spelling: no re-key, and
            # the quantity difference is reported as a real mismatch.
            _gains(root, "margin", {"ALK.TO": 30000, "XIU.TO": 100,
                                    "ANET.US": 50,
                                    "RCI.B270716C00055000.TO": 20,
                                    "RCI270716C00055000.TO": 5})
            r = _run(root, "sanity", f"margin={e / 'U1_holdings.toml'}"
                     f"+{e / 'U2_holdings.toml'}", "--json")
            self.assertEqual(r.returncode, 1)
            doc = json.loads(r.stdout)
            self.assertEqual(doc["groups"][0]["matched_via_underlying"],
                             [])
            issues = {(d["symbol"], d["issue"])
                      for d in doc["discrepancies"]}
            self.assertEqual(issues, {
                ("RCI270716C00055000.TO", "QTY_MISMATCH"),
                ("RCI.B270716C00055000.TO", "MISSING_IN_HOLDINGS")})

    # ---- pairings from taxjson.toml `holdings` -------------------------

    def _config_with_holdings(self, root, margin, rrsp=None):
        def _lst(paths):
            return "[" + ", ".join(f'"{p}"' for p in paths) + "]"
        rrsp_line = (f"holdings = {_lst(rrsp)}\n" if rrsp else "")
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            '[accounts.margin]\ntype = "taxable"\n'
            f'holdings = {_lst(margin)}\n'
            '[accounts.rrsp]\ntype = "sheltered"\n' + rrsp_line)

    def test_no_arguments_pairs_from_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            # margin's two files: one absolute, one project-relative.
            self._config_with_holdings(
                root, [str(root / "ext" / "U1_holdings.toml"),
                       "ext/U2_holdings.toml"],
                ["ext/U3_holdings.toml"])
            r = _run(root, "sanity", "--json")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            doc = json.loads(r.stdout)
            self.assertTrue(doc["clean"])
            by = {tuple(g["accounts"]): g for g in doc["groups"]}
            self.assertEqual(set(by), {("margin",), ("rrsp",)})
            self.assertTrue(all(g["paired"] for g in doc["groups"]))
            self.assertEqual(len(by[("margin",)]["files"]), 2)
            self.assertEqual(doc["uncovered_accounts"], [])
            # Wrong-account placement is caught, as with explicit pairs.
            _gains(root, "margin", {"ALK.TO": 30000, "XIU.TO": 100,
                                    "ANET.US": 75})
            _gains(root, "rrsp", {"XIU.TO": 40, "ANET.US": 50})
            r = _run(root, "sanity", "--json")
            self.assertEqual(r.returncode, 1)
            self.assertEqual(
                {d["symbol"] for d in json.loads(r.stdout)["discrepancies"]},
                {"ANET.US"})

    def test_shared_file_merges_accounts_into_one_group(self):
        # One export covering two taxjson accounts, listed under both.
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            e = root / "ext"
            _holdings_toml(e / "flex.toml", "FLEX",
                           {"ALK.TO": 30000, "ANET.US": 125,
                            "XIU.TO": 140})
            self._config_with_holdings(root, ["ext/flex.toml"],
                                       ["ext/flex.toml"])
            r = _run(root, "sanity", "--json")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            doc = json.loads(r.stdout)
            self.assertEqual([g["accounts"] for g in doc["groups"]],
                             [["margin", "rrsp"]])

    def test_missing_holdings_file_is_a_note_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            self._config_with_holdings(
                root, ["ext/U1_holdings.toml", "ext/U2_holdings.toml"],
                ["ext/not_there.toml"])
            r = _run(root, "sanity", "--json")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            doc = json.loads(r.stdout)
            self.assertEqual([g["accounts"] for g in doc["groups"]],
                             [["margin"]])
            self.assertEqual(doc["uncovered_accounts"], ["rrsp"])
            self.assertTrue(any("not_there.toml" in n
                                for n in doc["notes"]), doc["notes"])
            # Text mode surfaces the note on stderr.
            r = _run(root, "sanity")
            self.assertEqual(r.returncode, 0)
            self.assertIn("not_there.toml", r.stderr)

    def test_explicit_items_override_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            e = root / "ext"
            self._config_with_holdings(
                root, ["ext/U1_holdings.toml", "ext/U2_holdings.toml"],
                ["ext/U3_holdings.toml"])
            r = _run(root, "sanity", f"rrsp={e / 'U3_holdings.toml'}",
                     "--json")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            doc = json.loads(r.stdout)
            self.assertEqual(doc["accounts"], ["rrsp"])
            self.assertEqual(doc["uncovered_accounts"], ["margin"])

    def test_no_arguments_and_no_config_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp)
            r = _run(root, "sanity")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("holdings = [...]", r.stderr)

    def test_map_symbol_helper(self):
        from taxjson.bin.taxjson_ticker_map import map_symbol
        m = {"AEM.US": "AEM.TO", "FB.US": "META.US"}
        self.assertEqual(map_symbol("AEM.US", m), "AEM.TO")
        self.assertEqual(
            map_symbol("AEM280121C00155000.US", m),
            "AEM280121C00155000.TO")
        self.assertEqual(map_symbol("XIU.TO", m), "XIU.TO")
        self.assertEqual(map_symbol("FB251219C00300000.US", m),
                         "META251219C00300000.US")


if __name__ == "__main__":
    unittest.main()
