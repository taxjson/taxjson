"""`taxjson journals` and the `taxjson renames` additions (owner request,
feat/journals-cmd): every broker journal between two listings per
account, how it was found and its state (joined / suggested / refused);
each rename's source and the look-alike rename hints as suggestions;
`--json`, `--pending` and the checklist steps.

Every fixture is SYNTHETIC: invented QZ* names and symbols, fake
account ids (pii-ok: 55500001, U5550001).
"""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from taxjson.lib import cross_listings as XL
from taxjson.lib import journals as J
from taxjson.lib import out
from taxjson.lib.symbol_codes import exact_name
from tax_rules.dual import SRC, projects_both
from tax_rules.dual import cli as _cli


def cli(root, *args, width="0"):
    """`taxjson -C root args...`; width "120": as a pipe shows it."""
    import os
    import subprocess
    import sys
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH=width)
    if width == "0":
        return _cli(root, *args)
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL, timeout=180)

NAME = "QZDOLLAR US DLR CURRENCY ETF UNIT"

_TOML = ('[settings]\ncountry = "canada"\nbase_currency = "CAD"\n'
         'year = 2025\n[accounts.margin]\ntype = "taxable"\n'
         '[accounts.tfsa]\ntype = "tfsa"\n')
_CFG = {"settings": {"country": "canada", "base_currency": "CAD",
                     "year": 2025},
        "accounts": {"margin": {"type": "taxable"},
                     "tfsa": {"type": "tfsa"}}}


def _leg(sym, day, qty, name, *, account="margin", broker="rbc_direct",
         journal=True, ref=""):
    return XL.Leg(account, broker, sym, day, qty, name=exact_name(name),
                  raw_name=name, journal=broker if journal else "",
                  ref=ref)


def _state(legs, *, named=(), distinct=(), currency_journals=False):
    """work/cross_listings.state as the run writes it for `legs`."""
    names = {}
    for g in legs:
        names.setdefault(g.symbol, set()).add(g.name)
    shown = {g.name: g.raw_name for g in legs}
    refused = []
    res = XL.analyze(legs, names, shown, map_named=named,
                     map_distinct=distinct, base_currency="CAD",
                     currency_journals=currency_journals, refused=refused)
    res["refused"] = refused
    return XL.state_text(res)


def _project(td, state, tmap=None, effective=None):
    root = Path(td)
    (root / "work").mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(_TOML)
    (root / "work" / XL.STATE).write_text(state)
    if tmap is not None:
        (root / "ticker.map").write_text(tmap)
    if effective is not None:
        (root / "work" / XL.EFFECTIVE_MAP).write_text(effective)
    return root


def _effective(root, state_text, tmap=""):
    """The effective map the run writes for the joined records."""
    st = json.loads(state_text)
    lines = sorted({f"{'JOURNAL' if r.get('kind') == 'JOURNAL' else 'TOBASE'}"
                    f" {r['from']} {r['to']}" for r in st["joined"]})
    return (tmap + ("\n" if tmap else "") + XL.EFFECTIVE_HEAD + "\n"
            + "\n".join(lines) + "\n")


def _one(doc, frm):
    js = [j for j in doc["journals"] if j["from"] == frm]
    assert len(js) == 1, doc["journals"]
    return js[0]


class TestSourcesAndStates(unittest.TestCase):
    """Each source and each state, from the run's state file."""

    def _doc(self, legs, tmap="", **kw):
        st = _state(legs, **kw)
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, st, tmap,
                            _effective(Path(td), st, tmap)
                            if json.loads(st)["joined"] else None)
            doc = J.report(root, _CFG)
            text = "\n".join(J.render(doc, width_=120))
        return doc, text

    def test_rbc_gambit_with_and_without_its_reference(self):
        legs = [_leg("QZD.US", "2025-05-12", -300, NAME, ref="ref|x|1"),
                _leg("QZD.TO", "2025-05-12", 300, NAME, ref="ref|x|1"),
                _leg("QZE.US", "2025-06-02", -40, "QZE CORP"),
                _leg("QZE.TO", "2025-06-02", 40, "QZE CORP")]
        doc, text = self._doc(legs)
        a, b = _one(doc, "QZD.US"), _one(doc, "QZE.US")
        self.assertEqual((a["source"], a["state"]),
                         ("rbc-journal-ref", "joined"))
        self.assertEqual(a["found_by"], "RBC journal transfer (J~ ref)")
        self.assertEqual((b["source"], b["state"]), ("rbc-journal", "joined"))
        self.assertEqual(a["line"], "TOBASE QZD.US QZD.TO")
        self.assertTrue(a["line_at"].startswith(f"work/{XL.EFFECTIVE_MAP}:"))
        self.assertIn("DISTINCT QZD.US QZD.TO", a["undo"])
        self.assertIn("TOBASE QZD.US QZD.TO (run)", text)

    def test_questrade_brw_currency_journal(self):
        legs = [_leg("QZD.TO", "2025-09-25", -300, NAME, broker="questrade",
                     ref="pair|q1"),
                _leg("QZD.U.TO", "2025-09-25", 300, NAME, broker="questrade",
                     ref="pair|q1")]
        legs[0].pair = legs[1].pair = "q1"
        legs[0].currency, legs[1].currency = "CAD", "USD"
        doc, _t = self._doc(legs, currency_journals=True)
        j = _one(doc, "QZD.TO")
        self.assertEqual((j["source"], j["state"]),
                         ("questrade-brw", "joined"))
        self.assertEqual(j["found_by"], "Questrade journal (BRW)")
        self.assertEqual(j["line"], "JOURNAL QZD.U.TO QZD.TO")

    def test_ib_interdepot_and_cross_broker_move(self):
        legs = [_leg("QZN.US", "2025-04-23", -250, "QZNATURAL RESOURCES",
                     broker="ib"),
                _leg("QZN.TO", "2025-04-23", 250, "QZNATURAL RESOURCES",
                     broker="ib"),
                # A move from RBC's TSX line to IB's NYSE line.
                _leg("QZP.TO", "2025-07-02", -80, "QZPOWER CORP",
                     journal=False),
                _leg("QZP.US", "2025-07-04", 80, "QZPOWER CORP",
                     broker="ib", journal=False, account="tfsa")]
        doc, text = self._doc(legs)
        a, b = _one(doc, "QZN.US"), _one(doc, "QZP.TO")
        self.assertEqual((a["source"], a["state"]),
                         ("ib-interdepot", "joined"))
        self.assertEqual((b["source"], b["state"]), ("cross-broker", "joined"))
        self.assertEqual(b["found_by"],
                         "cross-broker move with a listing change")
        self.assertEqual(b["in"], {"account": "tfsa", "broker": "ib",
                                   "date": "2025-07-04"})
        # Listed once, under the out-leg's account; --account finds it
        # from either side.
        self.assertIn("QZP.US (tfsa)", text)
        with tempfile.TemporaryDirectory() as td:
            st = _state(legs)
            root = _project(td, st, "", _effective(Path(td), st))
            self.assertEqual(
                [j["from"] for j in J.report(root, _CFG, "tfsa")["journals"]],
                ["QZP.TO"])
            self.assertEqual(J.report(root, _CFG, year=2024)["journals"], [])

    def test_suggested_names_the_tt_and_map_lines(self):
        legs = [_leg("QZN.US", "2025-04-23", -250, "QZCO INC CL A",
                     broker="ib"),
                _leg("QZN.TO", "2025-04-23", 250, "QZCO INC CL B",
                     broker="ib")]
        doc, text = self._doc(legs)
        j = _one(doc, "QZN.US")
        self.assertEqual(j["state"], "suggested")
        self.assertTrue(j["reason"].startswith(
            "the legs' names are not equal word for word"))
        self.assertEqual(j["settle"], ["JOURNAL 2025-04-23 QZN.US QZN.TO 250",
                                       "TOBASE QZN.US QZN.TO"])
        self.assertIsNone(j["line"])
        # The lines to copy are on their own lines, never wrapped.
        lines = text.splitlines()
        self.assertIn("      JOURNAL 2025-04-23 QZN.US QZN.TO 250", lines)
        self.assertIn("      TOBASE QZN.US QZN.TO", lines)
        self.assertEqual(doc["pending"], 1)

    def test_refused_by_distinct_and_by_two_companies(self):
        legs = [_leg("QZA.US", "2025-03-03", -10, "QZALPHA CORP"),
                _leg("QZA.TO", "2025-03-03", 10, "QZALPHA CORP"),
                _leg("QZW.US", "2025-03-10", -20, "QZWIDGET ENERGY INC"),
                _leg("QZW.TO", "2025-03-10", 20, "QZMOUNTAIN MINING LTD")]
        doc, text = self._doc(legs, tmap="DISTINCT QZA.US QZA.TO\n",
                              distinct=[("QZA.US", "QZA.TO")])
        a, b = _one(doc, "QZA.US"), _one(doc, "QZW.US")
        self.assertEqual(a["state"], "refused")
        self.assertIn("DISTINCT QZA.US QZA.TO", a["reason"])
        self.assertIn("remove `DISTINCT QZA.US QZA.TO` (ticker.map:1)",
                      a["undo"])
        self.assertEqual(b["state"], "refused")
        self.assertEqual(b["reason"], XL.DIFFERENT)
        self.assertIn("TOBASE QZW.US QZW.TO", b["undo"])
        self.assertIn("DISTINCT QZW.US QZW.TO", b["undo"])
        self.assertEqual(doc["counts"], {"joined": 0, "suggested": 0,
                                         "refused": 2, "decided": 1})
        # The user's DISTINCT line is a decision made: listed, not
        # pending. Two companies with no line of the user's: pending.
        self.assertEqual((a["pending"], a["decided_by"]),
                         (False, "ticker.map"))
        self.assertEqual((b["pending"], b["decided_by"]), (True, None))
        self.assertEqual(doc["pending"], 1)
        self.assertIn("refused by your ticker.map: ticker.map keeps them "
                      "apart", " ".join(text.split()))
        self.assertIn("To change it: remove", " ".join(text.split()))
        self.assertIn("0 joined, 0 suggested, 2 refused (1 by your "
                      "ticker.map); 1 pending.", " ".join(text.split()))

    def test_a_map_line_naming_a_listing_decides_not_pending(self):
        # GLOBAL renames one listing elsewhere: the user's map decides.
        legs = [_leg("QZA.US", "2025-03-03", -10, "QZALPHA CORP"),
                _leg("QZA.TO", "2025-03-03", 10, "QZALPHA CORP")]
        doc, _t = self._doc(legs, tmap="GLOBAL QZA.US QZB.US\n",
                            named=["QZA.US", "QZB.US"])
        j = _one(doc, "QZA.US")
        self.assertEqual((j["state"], j["pending"], j["decided_by"]),
                         ("refused", False, "ticker.map"))
        self.assertIn("GLOBAL QZA.US QZB.US (ticker.map:1)", j["reason"])
        self.assertEqual(doc["pending"], 0)

    def test_two_companies_by_coincidence_are_not_a_journal(self):
        # No journal wording, no broker reference: two unrelated
        # transfers of an equal quantity are not listed.
        legs = [_leg("QZW.US", "2025-03-10", -20, "QZWIDGET ENERGY INC",
                     journal=False),
                _leg("QZM.TO", "2025-03-11", 20, "QZMOUNTAIN MINING LTD",
                     journal=False)]
        doc, _t = self._doc(legs)
        self.assertEqual(doc["journals"], [])

    def test_joined_by_the_users_map_line(self):
        legs = [_leg("QZD.U.TO", "2025-05-12", -300, NAME, ref="ref|x|1"),
                _leg("QZD.TO", "2025-05-12", 300, NAME, ref="ref|x|1")]
        tmap = "# mine\nJOURNAL QZD.U.TO QZD.TO\n"
        doc, text = self._doc(legs, tmap=tmap, named=["QZD.U.TO", "QZD.TO"])
        j = _one(doc, "QZD.U.TO")
        self.assertEqual(j["state"], "joined")
        self.assertEqual((j["line"], j["line_at"]),
                         ("JOURNAL QZD.U.TO QZD.TO", "ticker.map:2"))
        self.assertIn("remove the line at ticker.map:2", j["undo"])
        self.assertIn("JOURNAL QZD.U.TO QZD.TO (ticker.map:2)", text)

    def test_a_source_another_stage_writes_is_read_generically(self):
        st = {"format": XL.FORMAT, "joined": [], "suggested": [
            {"from": "QZT.US", "to": "QZT.TO", "source": "tt",
             "out": {"account": "margin", "broker": "tt", "symbol": "QZT.US",
                     "date": "2025-02-03", "quantity": 5},
             "in": {"account": "margin", "broker": "tt", "symbol": "QZT.TO",
                    "date": "2025-02-03", "quantity": 5},
             "reason": "x"},
            {"from": "QZU.US", "to": "QZU.TO", "source": "zz-future",
             "account": "margin", "date": "2025-02-04", "quantity": 6}],
            "collisions": []}
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, json.dumps(st))
            doc = J.report(root, _CFG)
        self.assertEqual(_one(doc, "QZT.US")["found_by"], ".tt line")
        u = _one(doc, "QZU.US")
        self.assertEqual((u["source"], u["found_by"], u["quantity"]),
                         ("zz-future", "zz-future", 6.0))
        # "broker" says only that a broker's rows show it: read how.
        self.assertEqual(J.source_of({"source": "broker",
                                      "journal": "ib"}), "ib-interdepot")
        self.assertEqual(J.source_of({"journal": "tt"}), "tt")

    def test_broker_journal_the_state_does_not_list(self):
        # A run older than the refused records: the parsed export's J~
        # pair is listed, joined by the user's TOBASE line.
        def row(sym, qty, cur, way):
            return {"action": "TRANSFER", "date": "2025-05-12",
                    "symbol": sym, "quantity": qty, "currency": cur,
                    "description": f"TFR - {NAME} TRANSFER {way}  J~0TFR1Q"}
        books = {"transactions": [row("QZD.US", -300, "USD", "TO C$"),
                                  row("QZD.TO", 300, "CAD", "FROM U$")]}
        empty = json.dumps({"format": XL.FORMAT, "joined": [],
                            "suggested": [], "collisions": []})
        for tmap, state in (("TOBASE QZD.US QZD.TO\n", "joined"),
                            ("", "suggested")):
            with self.subTest(state=state), \
                    tempfile.TemporaryDirectory() as td:
                root = _project(td, empty, tmap)
                (root / "work" / "margin_rbc_direct.json").write_text(
                    json.dumps(books))
                j = _one(J.report(root, _CFG), "QZD.US")
                self.assertEqual((j["source"], j["state"]),
                                 ("rbc-journal-ref", state))
                if state == "suggested":
                    self.assertIn("did not pair the legs", j["reason"])

    def test_json_schema(self):
        legs = [_leg("QZD.US", "2025-05-12", -300, NAME, ref="ref|x|1"),
                _leg("QZD.TO", "2025-05-12", 300, NAME, ref="ref|x|1")]
        doc, _t = self._doc(legs)
        self.assertEqual(set(doc), {"format", "account", "year", "map",
                                    "journals", "counts", "pending"})
        self.assertEqual(doc["format"], "taxjson-journals/1")
        self.assertEqual(set(doc["journals"][0]), {
            "account", "broker", "date", "from", "to", "quantity", "in",
            "source", "found_by", "state", "line", "line_at", "reason",
            "settle", "undo", "names", "where", "pending", "decided_by"})
        json.dumps(doc)

    def test_layout_passes_the_style_lint(self):
        legs = [_leg("QZN.US", "2025-04-23", -250, "QZCO INC CL A",
                     broker="ib"),
                _leg("QZN.TO", "2025-04-23", 250, "QZCO INC CL B",
                     broker="ib"),
                _leg("QZD.US", "2025-05-12", -300, NAME, ref="ref|x|1"),
                _leg("QZD.TO", "2025-05-12", 300, NAME, ref="ref|x|1")]
        doc, text = self._doc(legs)
        self.assertEqual(out.lint(text, width_=120), [])
        self.assertTrue(text.splitlines()[0].startswith("JOURNALS — "))
        self.assertIn("ACCOUNT margin (2)", text)
        self.assertTrue(text.splitlines()[-1].startswith(
            "1 joined, 1 suggested, 0 refused; 1 pending."))
        pend = "\n".join(J.render(dict(doc, journals=[
            j for j in doc["journals"] if j["state"] != "joined"]),
            width_=120, pending=True))
        self.assertEqual(out.lint(pend, width_=120), [])
        self.assertNotIn("QZD.US →", pend)


# ------------------------------------------------------------ the CLI
def _rbc(gambits, distinct=False):
    """An RBC export: a fund bought on its USD line, journaled to the CAD
    line (TFR legs, J or J~ reference), later sold there."""
    from test_fix_rbc import HDR, row
    body = ('"Activity Export as of Jan 5, 2026 at 8:59:00 am ET"\n\n'
            + HDR + row("March 11, 2025", "Buy", "QZD", NAME, "800", "10",
                        "-8000", "USD", NAME + " UNSOLICITED DA"))
    for day, qty, ref in gambits:
        j = "J~" + ref if ref else "J"
        body += (row(day, "Transfers", "QZD", NAME, f"-{qty}", "", "0",
                     "USD", f"TFR - {NAME} TRANSFER TO C$  {j}")
                 + row(day, "Transfers", "QZD", NAME, str(qty), "", "0",
                       "CAD", f"TFR - {NAME} TRANSFER FROM U$  {j}"))
    body += row("June 18, 2025", "Sell", "QZD", NAME, "-100", "14", "1400",
                "CAD", NAME + " UNSOLICITED CA JNL")
    return body


class TestCommand(unittest.TestCase):
    """`taxjson journals` on full runs (both countries)."""

    def test_no_run_is_an_error_naming_taxjson_run(self):
        with tempfile.TemporaryDirectory() as td:
            for root in projects_both(Path(td)).values():
                r = cli(root, "journals")
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertIn("error: no work/", r.stderr)
                self.assertIn("run `taxjson run` first", r.stderr)
                r = cli(root, "journals", width="120")
                self.assertTrue(r.stderr.startswith("Error: no work/"),
                                r.stderr)
                r = cli(root, "journals", "--account", "nosuch")
                self.assertEqual(r.returncode, 1)
                self.assertIn("no [accounts.nosuch]", r.stderr)

    def test_rbc_gambits_joined_then_refused_by_distinct(self):
        files = {"inputs/margin/rbc.csv": _rbc(
            [("May 12, 2025", 300, ""), ("May 16, 2025", 200, "0TFR1Q")])}
        with tempfile.TemporaryDirectory() as td:
            ps = projects_both(Path(td), files=files)
            # The command is the same in both countries; the US project
            # would need CAD rates for the CAD line.
            for country, root in (("canada", ps["canada"]),):
                with self.subTest(country=country):
                    self.assertEqual(
                        cli(root, "run", "--no-input").returncode, 0)
                    r = cli(root, "journals", "--json")
                    self.assertEqual(r.returncode, 0, r.stderr)
                    doc = json.loads(r.stdout)
                    self.assertEqual(
                        sorted((j["source"], j["state"])
                               for j in doc["journals"]),
                        [("rbc-journal", "joined"),
                         ("rbc-journal-ref", "joined")])
                    self.assertEqual(cli(root, "journals", "--pending")
                                     .returncode, 0)
                    t = cli(root, "journals", width="120")
                    self.assertEqual(t.returncode, 0)
                    self.assertEqual(out.lint(t.stdout, width_=120), [])
                    # The user keeps the listings apart: refused, a
                    # decision made — listed, never pending.
                    (root / "ticker.map").write_text(
                        "DISTINCT QZD.US QZD.TO\n")
                    cli(root, "run", "--no-input")
                    r = cli(root, "journals", "--json")
                    self.assertEqual(r.returncode, 0, r.stderr)
                    doc = json.loads(r.stdout)
                    self.assertEqual(
                        {(j["state"], j["pending"], j["decided_by"])
                         for j in doc["journals"]},
                        {("refused", False, "ticker.map")})
                    self.assertEqual(doc["pending"], 0)
                    self.assertIn("DISTINCT QZD.US QZD.TO",
                                  doc["journals"][0]["reason"])
                    r = cli(root, "journals", "--pending", "--json")
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertEqual(json.loads(r.stdout)["journals"], [])
                    t = cli(root, "journals", "--pending", width="120")
                    self.assertEqual(t.returncode, 0)
                    self.assertEqual(out.lint(t.stdout, width_=120), [])
                    self.assertTrue(t.stdout.splitlines()[-1].startswith(
                        "0 joined, 0 suggested, 2 refused (2 by your "
                        "ticker.map); 0 pending."), t.stdout)
                    t = cli(root, "journals", width="120")
                    self.assertEqual(out.lint(t.stdout, width_=120), [])
                    self.assertIn("refused by your ticker.map", t.stdout)
                    # The user's TOBASE line joins them.
                    (root / "ticker.map").write_text(
                        "TOBASE QZD.US QZD.TO\n")
                    cli(root, "run", "--no-input")
                    doc = json.loads(cli(root, "journals", "--json").stdout)
                    self.assertEqual(
                        {(j["state"], j["line_at"])
                         for j in doc["journals"]},
                        {("joined", "ticker.map:1")})

    def test_ib_interdepot_joined(self):
        from test_fix_journal_pairing import _ib_flip
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(Path(td), files=_ib_flip())["canada"]
            self.assertEqual(cli(root, "run", "--no-input").returncode, 0)
            doc = json.loads(cli(root, "journals", "--json",
                                 "--year", "2025").stdout)
            self.assertEqual([(j["from"], j["to"], j["source"], j["state"])
                              for j in doc["journals"]],
                             [("QZN.US", "QZN.TO", "ib-interdepot",
                               "joined")])

    def test_questrade_brw_joined(self):
        from test_fix_journal_books import QT_CSV, _rates
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(Path(td), files={
                "inputs/margin/questrade.csv": QT_CSV})["canada"]
            (root / "work").mkdir()
            _rates(root / "work" / "to_base.csv")
            self.assertEqual(cli(root, "run", "--no-input").returncode, 0)
            doc = json.loads(cli(root, "journals", "--json").stdout)
            self.assertEqual([(j["source"], j["state"], j["line"])
                              for j in doc["journals"]],
                             [("questrade-brw", "joined",
                               "JOURNAL QZD.U.TO QZD.TO")])


# ------------------------------------------------------------ renames
_QT_HINT = ("warning: ATTENTION: margin_2025.csv: Questrade symbol QZOLD.US "
            "looks renamed to QZNEW.US — if they are one security add to "
            "ticker.map:  GLOBAL QZOLD.US QZNEW.US  — QZOLD.US stops on "
            "2025-03-03 with 10 share(s) still open, and QZNEW.US (same "
            "Description 'QZ HOLDINGS INC') first appears on 2025-03-10 "
            "with a SALE of 10. That looks like a ticker change booked "
            "without a corporate-action row.\n")
_RBC_HINT = ("warning: ATTENTION: rbc.csv: RBC symbol QZR (USD) looks "
             "renamed to QZS — if they are one security add to ticker.map:  "
             "GLOBAL QZR.US QZS.US  — QZR stops on 2025-04-01 with 5 "
             "share(s) still open, and QZS (same Symbol Description 'QZ "
             "FIRST AGAIN CORP') first appears on 2025-04-15 with a SALE of "
             "5. That is a ticker change RBC booked without a "
             "reorganization row.\n")
_WB_HINT = ("warning: ATTENTION: Webull wb.csv: QZW goes short with a SALE "
            "on 2025-05-06 and shares the Security Description 'QZ WIDGETS' "
            "with QZV — likely a ticker change Webull reported without a "
            "reorganization row. If so, add the dated change `RENAME QZV "
            "QZW 2025-05-02` (the first QZW row here; use the broker's "
            "change date if you know it) to ticker.map so both are one "
            "position (`taxjson renames`).\n")


def _renames_project(td, tmap="", tt=""):
    root = Path(td)
    (root / "work").mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(_TOML)
    (root / "ticker.map").write_text(tmap)
    (root / "work" / "margin_questrade.json.diag").write_text(_QT_HINT)
    (root / "work" / "margin_rbc_direct.json.diag").write_text(_RBC_HINT)
    (root / "work" / "tfsa_webull.json.diag").write_text(_WB_HINT)
    return root


class TestRenames(unittest.TestCase):

    def test_look_alike_hints_are_suggested_with_the_tt_line(self):
        from taxjson.lib.renames import render, report
        with tempfile.TemporaryDirectory() as td:
            doc = report(_renames_project(td), _CFG)
        got = [(h["account"], h["old"], h["new"], h["line"], h["source"])
               for h in doc["suggested"]]
        self.assertEqual(got, [
            ("margin", "QZOLD.US", "QZNEW.US",
             "RENAME 2025-03-10 QZOLD.US QZNEW.US", "Questrade looks renamed"),
            ("margin", "QZR.US", "QZS.US", "RENAME 2025-04-15 QZR.US QZS.US",
             "RBC looks renamed"),
            ("tfsa", "QZV", "QZW", "RENAME 2025-05-02 QZV QZW",
             "Webull looks renamed")])
        self.assertEqual(doc["suggested"][0]["map_line"],
                         "RENAME QZOLD.US QZNEW.US 2025-03-10")
        self.assertEqual((doc["unresolved"], doc["pending"]), (0, 3))
        text = "\n".join(render(doc, width_=120))
        self.assertIn("\n      RENAME 2025-03-10 QZOLD.US QZNEW.US\n", text)
        self.assertIn("SUGGESTED (3)", text)
        self.assertEqual(out.lint(text, width_=120), [])
        self.assertIn("3 look-alike rename(s) to settle.",
                      text.splitlines()[-1])

    def test_a_hint_the_map_answers_is_not_suggested(self):
        from taxjson.lib.renames import report
        for tmap in ("GLOBAL QZOLD.US QZNEW.US\n",
                     "DISTINCT QZOLD.US QZNEW.US\n",
                     "RENAME QZOLD.US QZNEW.US 2025-03-07\n"):
            with self.subTest(tmap=tmap), \
                    tempfile.TemporaryDirectory() as td:
                doc = report(_renames_project(td, tmap), _CFG)
                self.assertNotIn("QZOLD.US",
                                 [h["old"] for h in doc["suggested"]])
                self.assertEqual(len(doc["suggested"]), 2)

    def test_a_hint_the_books_answer_is_not_pending(self):
        # A rename event of the pair in the books (a .tt line, a broker
        # row): the hint is answered, never pending.
        from taxjson.lib.renames import rename_hints
        with tempfile.TemporaryDirectory() as td:
            hs = rename_hints(_renames_project(td), _CFG, [
                {"old": "QZOLD.US", "new": "QZNEW.US",
                 "date": "2025-03-07"}])
        self.assertEqual([h["old"] for h in hs], ["QZR.US", "QZV"])

    def test_source_labels(self):
        from taxjson.lib.renames import row_source
        for src, want in (("tt", ".tt line"), ("ib-conid",
                                                "detected IB contract id"),
                          ("map", "ticker.map line"),
                          ("ticker.map", "ticker.map line"),
                          ("m.tt", ".tt line (m.tt)"),
                          ("ib.csv", "broker row (ib.csv)")):
            self.assertEqual(row_source({"source": src}), want)
        # The stage that booked the row (event_source) names it first.
        self.assertEqual(row_source({"event_source": "ib-conid",
                                     "source": "ib.csv"}),
                         "detected IB contract id")
        self.assertEqual(row_source({"event_source": "tt",
                                     "source": "m.tt"}), ".tt line (m.tt)")

    def test_cli_source_column_pending_and_exit_codes(self):
        tt = ("BUYSELL 2024-01-10 10:00:00 OLD.TO 100 CAD 10.00 -1000.00 0.00\n"
              "SPLIT 2024-06-01 09:00:00 OLD.TO NEW.TO 1\n"
              "BUYSELL 2025-03-03 10:00:00 NEW.TO -100 CAD 5.00 500.00 0.00\n")
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files={
                "inputs/margin/m.tt": tt})["canada"]
            self.assertEqual(cli(root, "run", "--no-input").returncode, 0)
            r = cli(root, "renames", width="120")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(out.lint(r.stdout, width_=120), [])
            head = next(ln for ln in r.stdout.splitlines()
                        if ln.startswith("DATE"))
            self.assertIn("SOURCE", head)
            self.assertIn(".tt line (m.tt)", r.stdout)
            self.assertEqual(cli(root, "renames", "--pending").returncode, 0)
            # A look-alike hint in the run's .diag: suggested; the plain
            # exit code stays 0, --pending exits 1.
            (root / "work" / "margin_questrade.json.diag").write_text(
                _QT_HINT)
            r = cli(root, "renames")
            self.assertEqual(r.returncode, 0)
            self.assertIn("RENAME 2025-03-10 QZOLD.US QZNEW.US", r.stdout)
            r = cli(root, "renames", "--pending", "--json")
            self.assertEqual(r.returncode, 1)
            doc = json.loads(r.stdout)
            self.assertEqual((doc["renames"], doc["pending"]), ([], 1))
            t = cli(root, "renames", "--pending", width="120")
            self.assertEqual(t.returncode, 1)
            self.assertEqual(out.lint(t.stdout, width_=120), [])


# ----------------------------------------------------------- checklist
class TestChecklist(unittest.TestCase):

    def _ctx(self, root):
        from taxjson.lib import checklist
        return checklist.Ctx(root, _CFG, 2025, date(2026, 1, 5),
                             lambda *a, **k: (0, "", ""))

    def test_journals_step(self):
        from taxjson.lib import checklist
        legs = [_leg("QZN.US", "2025-04-23", -250, "QZCO INC CL A",
                     broker="ib"),
                _leg("QZN.TO", "2025-04-23", 250, "QZCO INC CL B",
                     broker="ib")]
        self.assertIn("journals", [s[0] for s in checklist.STEPS])
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, _state(legs))
            r = checklist.d_journals(self._ctx(root))
            self.assertEqual(r.status, "attention")
            self.assertIn("taxjson journals --pending", r.detail)
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, _state(legs[:1]))
            self.assertEqual(checklist.d_journals(self._ctx(root)).status,
                             "done")
        with tempfile.TemporaryDirectory() as td:
            r = checklist.d_journals(self._ctx(Path(td)))
            self.assertEqual(r.status, "blocked")
        # Kept apart by the user's DISTINCT line: done (a decision made);
        # a broker journal whose legs name two companies: attention.
        apart = [_leg("QZA.US", "2025-03-03", -10, "QZALPHA CORP"),
                 _leg("QZA.TO", "2025-03-03", 10, "QZALPHA CORP")]
        two = [_leg("QZW.US", "2025-03-10", -20, "QZWIDGET ENERGY INC"),
               _leg("QZW.TO", "2025-03-10", 20, "QZMOUNTAIN MINING LTD")]
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, _state(apart,
                                       distinct=[("QZA.US", "QZA.TO")]),
                            "DISTINCT QZA.US QZA.TO\n")
            r = checklist.d_journals(self._ctx(root))
            self.assertEqual(r.status, "done", r.detail)
            self.assertIn("1 kept apart by ticker.map", r.detail)
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, _state(two))
            r = checklist.d_journals(self._ctx(root))
            self.assertEqual(r.status, "attention")
            self.assertIn("1 pending journal(s)", r.detail)

    def test_renames_step_flags_a_hint(self):
        from taxjson.lib import checklist
        with tempfile.TemporaryDirectory() as td:
            root = _renames_project(td)
            r = checklist.d_renames(self._ctx(root))
            self.assertEqual(r.status, "attention")
            self.assertIn("look-alike rename", r.detail)


if __name__ == "__main__":
    unittest.main()
