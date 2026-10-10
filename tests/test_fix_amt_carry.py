"""Minimum tax (AMT) detail and carry-forwards that flow from one year
to the next (owner request): `taxjson amt`, the s.120.2 carryover in
the estimate ([estimate] amt_carryover / the prior
close-year lock), close-year's `carryforwards` record, `carryover` and
`handoff` reading it. Synthetic figures only."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent
from tax_rules.dual import cli, settings_for

from taxjson.lib import carryforward as CF
from taxjson.lib import tax_estimate as TE
from taxjson.lib import tax_logic as TL


def _ca(**kw):
    args = dict(realized=0.0, eligible_div=0.0, foreign_div=0.0, pil=0.0,
                other_income=0.0, other_losses=0.0, province="ON",
                year=2026)
    args.update(kw)
    return TE.estimate_canada(**args)


def _text(country, rid):
    for _t, rules in TL.rule_sections(country, {"country": country,
                                                "year": 2026}):
        for r in rules:
            if r.id == rid:
                return r.text
    raise AssertionError(f"{rid} not stated for {country}")


# ------------------------------------------------------------ arithmetic
class TestCarryoverArithmetic(unittest.TestCase):

    @rule("CA-AMT-02", "CA-AMT-07")
    def test_binding_year_creates_its_excess(self):
        r = _ca(realized=600000.0, year=2025)
        a = r["amt"]
        self.assertTrue(a["binding"])
        c = a["carryover"]
        self.assertEqual(c["created"], a["excess_fed"])
        self.assertEqual(c["closing_by_year"], {"2025": a["excess_fed"]})
        # The provincial AMT is not part of the carryover.
        self.assertGreater(a["provincial_amt"], 0)
        self.assertEqual(c["closing"], a["excess_fed"])
        self.assertIn("additional tax", _text("canada", "CA-AMT-02"))

    @rule("CA-AMT-03")
    def test_seven_year_limit(self):
        c = TE.ca_amt_carryover(2026, {2018: 100.0, 2019: 200.0,
                                       2025: 300.0}, 1000.0)
        self.assertEqual(c["expired_by_year"], {"2018": 100.0})
        self.assertEqual(c["available_by_year"],
                         {"2019": 200.0, "2025": 300.0})
        self.assertEqual(c["last_year_by_origin"]["2019"], 2026)
        with self.assertRaises(ValueError):
            TE.ca_amt_carryover(2026, {2026: 1.0}, 1000.0)
        r = _ca(other_income=250000.0, realized=10000.0,
                amt_carryover={2018: 100.0},
                amt_carryover_source="[estimate] amt_carryover")
        self.assertIn("2018 minimum tax carryover", " ".join(r["notes"]))
        self.assertEqual(r["amt"]["carryover"]["recovered"], 0.0)

    @rule("CA-AMT-04", "CA-AMT-07")
    def test_recovery_oldest_first_limited_to_headroom(self):
        base = _ca(other_income=250000.0, realized=10000.0)
        room = base["amt"]["headroom"]
        self.assertGreater(room, 1000)
        c = TE.ca_amt_carryover(2026, {2020: room - 500.0, 2024: 1000.0},
                                room)
        self.assertEqual(c["recovered_by_year"],
                         {"2020": round(room - 500.0, 2), "2024": 500.0})
        self.assertEqual(c["remaining_by_year"], {"2024": 500.0})
        # An origin of year - 7 left unrecovered lapses after the year.
        c = TE.ca_amt_carryover(2026, {2019: 5000.0}, 1000.0)
        self.assertEqual(c["lapsing_by_year"], {"2019": 4000.0})
        self.assertEqual(c["remaining_by_year"], {})
        # Recovered against federal tax before the FTC (line 40427).
        r = _ca(other_income=250000.0, realized=10000.0,
                amt_carryover={2024: 1000.0}, amt_carryover_source="x")
        self.assertEqual(r["amt"]["carryover"]["recovered_federal"], 1000.0)
        # Nothing in a year AMT binds.
        r = _ca(realized=600000.0, amt_carryover={2024: 1000.0},
                amt_carryover_source="x")
        self.assertEqual(r["amt"]["carryover"]["recovered"], 0.0)
        self.assertEqual(r["amt"]["carryover"]["closing_by_year"]["2024"],
                         1000.0)

    @rule("CA-AMT-05")
    def test_provincial_share(self):
        bc = _ca(province="BC", other_income=250000.0, realized=10000.0,
                 amt_carryover={2024: 1000.0}, amt_carryover_source="x")
        c = bc["amt"]["carryover"]
        self.assertAlmostEqual(c["recovered_provincial"],
                               1000.0 * bc["amt"]["provincial_factor"],
                               places=2)
        on = _ca(province="ON", other_income=250000.0, realized=10000.0,
                 amt_carryover={2024: 1000.0}, amt_carryover_source="x")
        c = on["amt"]["carryover"]
        # Before the surtax: the surtax on it is recovered too.
        self.assertGreater(c["recovered_provincial"],
                           1000.0 * on["amt"]["provincial_factor"] + 1)

    @rule("CA-AMT-06")
    def test_estimate_counts_only_the_change_it_causes(self):
        r = _ca(other_income=40000.0, realized=10000.0,
                amt_carryover={2024: 3000.0}, amt_carryover_source="x")
        c = r["amt"]["carryover"]
        self.assertGreater(c["base_recovered"], 0)
        self.assertAlmostEqual(c["attributed"],
                               c["recovered"] - c["base_recovered"],
                               places=2)
        self.assertAlmostEqual(r["estimated_tax_with_amt"],
                               r["estimated_tax"] + r["amt"]["topup"]
                               - c["attributed"], places=2)
        from taxjson.bin.taxjson_run import _net_tax_owing
        self.assertAlmostEqual(
            _net_tax_owing(r, 0.0),
            r["tax_with"]["total"] - c["recovered"], places=2)

    @rule("CA-AMT-06")
    def test_no_carryover_entered_changes_nothing(self):
        r = _ca(other_income=40000.0, realized=10000.0)
        self.assertEqual(r["estimated_tax_with_amt"],
                         round(r["estimated_tax"] + r["amt"]["topup"], 2))
        self.assertFalse(r["amt"]["carryover"]["entered"])
        self.assertIn("amt_carryover", " ".join(r["notes"]))
        self.assertNotIn("no prior-year minimum tax carryover",
                         r["assumptions"])

    @rule("CA-AMT-01")
    def test_ati_lines_add_up(self):
        r = _ca(realized=150000.0, eligible_div=1000.0, foreign_div=500.0,
                pil=20.0, other_income=90000.0, other_losses=8000.0,
                deductions=3000.0, carrying_charges=400.0)
        a = r["amt"]
        self.assertAlmostEqual(sum(ln["amount"] for ln in a["ati_lines"]),
                               a["adjusted_income"], places=2)
        self.assertAlmostEqual(a["subject"],
                               a["adjusted_income"] - a["exemption"],
                               places=2)
        self.assertAlmostEqual(
            a["minimum_fed"],
            a["gross_minimum"] - a["bpa_credit"] - a["ftc"], places=1)


class TestUsEstimateCarryoverSplit(unittest.TestCase):

    @rule("US-CARRY-01")
    def test_st_lt_after(self):
        r = TE.estimate_usa(st=-5000.0, lt=-2000.0, qualified_div=0.0,
                            pil=0.0, other_income=120000.0,
                            other_losses=0.0, year=2025)
        self.assertEqual((r["st_carryover_after"], r["lt_carryover_after"]),
                         (2000.0, 2000.0))
        self.assertEqual(r["losses_unused"], 4000.0)


# ----------------------------------------------------------- the inputs
class TestInputs(unittest.TestCase):

    @rule("CA-AMT-08")
    def test_file_config_and_refusals(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            r = CF.resolve_amt(root, {"year": 2026},
                               {"amt_carryover": {"2023": 1200.5,
                                                  "2024": 800}})
            self.assertEqual(r["by_year"], {2023: 1200.5, 2024: 800.0})
            self.assertEqual(r["source"], "[estimate] amt_carryover")
            # The old file is no longer an input (taxjson migrate moves
            # it; every command stops while it is there): the config
            # alone decides.
            (root / "amt_carryover.txt").write_text("2023 1\n")
            r = CF.resolve_amt(root, {"year": 2026},
                               {"amt_carryover": {"2022": 50}})
            self.assertEqual(r["by_year"], {2022: 50.0})
            for bad in ({"2023": "12,34"}, {"2023": -1}, {"1850": 5},
                        {"x": 5}):
                with self.assertRaises(CF.CarryInputError, msg=bad):
                    CF.resolve_amt(root, {"year": 2026},
                                   {"amt_carryover": bad})
            with self.assertRaisesRegex(CF.CarryInputError, "by year"):
                CF.resolve_amt(root, {"year": 2026},
                               {"amt_carryover": 5000})
            self.assertEqual(CF.resolve_amt(root, {"year": 2026}, {})
                             ["by_year"], None)

    def test_legacy_file_reader_kept_for_migrate(self):
        # load_amt_file reads the old amt_carryover.txt for `taxjson
        # migrate`, as loudly as before.
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "amt_carryover.txt"
            f.write_text("# from my NOA\n2023 1,200.50\n2024 800  # T691\n")
            self.assertEqual(CF.load_amt_file(f), {2023: 1200.5,
                                                   2024: 800.0})
            f.write_text("2023 12,34\n")
            with self.assertRaisesRegex(CF.CarryInputError, ":1:"):
                CF.load_amt_file(f)
            f.write_text("2023 1\n2023 2\n")
            with self.assertRaisesRegex(CF.CarryInputError, "twice"):
                CF.load_amt_file(f)

    @rule("CA-AMT-08", "CA-CARRY-02", "CA-CARRY-03")
    def test_lock_fallback_and_gap_note(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "filed").mkdir()
            (root / "filed" / "2024.json").write_text(json.dumps({
                "year": 2024, "carryforwards": {
                    "net_capital_loss": {"opening": 0, "created": 900,
                                         "applied": 0, "closing": 900},
                    "minimum_tax": {"created": 70, "closing": 70,
                                    "recovered_federal": 0,
                                    "closing_by_year": {"2024": 70}}}}))
            st = {"year": 2026}
            r = CF.resolve_amt(root, st, {})
            self.assertEqual(r["by_year"], {2024: 70.0})
            self.assertIn("filed/2024.json", r["source"])
            self.assertIn("2025 has none", " ".join(r["notes"]))
            lr = CF.resolve_losses(root, st, "canada", explicit=False)
            self.assertEqual(lr["other_losses"], 900.0)
            self.assertEqual(CF.resolve_losses(root, st, "canada",
                                               explicit=True),
                             {"source": None, "notes": []})
            # A damaged block is named, never read as zero.
            (root / "filed" / "2024.json").write_text(json.dumps({
                "year": 2024, "carryforwards": {
                    "net_capital_loss": {"closing": "lots"}}}))
            with self.assertRaises(CF.CarryInputError):
                CF.resolve_losses(root, st, "canada", explicit=False)


# --------------------------------------------------------- CLI projects
_CA_BOOK_25 = ("BUYSELL 2025-01-02 10:00:00 XYZ.TO 1000 CAD 100 100000 0\n"
               "BUYSELL 2025-03-03 10:00:00 XYZ.TO -1000 CAD 700 700000 0\n")
_CA_BOOK_26 = ("BUYSELL 2026-01-05 10:00:00 ABC.TO 100 CAD 100 10000 0\n"
               "BUYSELL 2026-03-03 10:00:00 ABC.TO -100 CAD 300 30000 0\n")
_US_BOOK_25 = ("BUYSELL 2025-01-02 10:00:00 XYZ.US 100 USD 100 10000 0\n"
               "BUYSELL 2025-03-03 10:00:00 XYZ.US -100 USD 20 2000 0\n")
_US_BOOK_26 = ("BUYSELL 2026-01-05 10:00:00 ABC.US 100 USD 100 10000 0\n"
               "BUYSELL 2026-03-03 10:00:00 ABC.US -100 USD 130 13000 0\n")


def _project(root: Path, country: str, year: int, book: str,
             extra: str = "", **settings) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "inputs" / "margin").mkdir(parents=True, exist_ok=True)
    (root / "inputs" / "margin" / "m.tt").write_text(book)
    (root / "taxjson.toml").write_text(
        settings_for(country, year=year, **settings)
        + '[accounts.margin]\ntype = "taxable"\n' + extra)
    r = cli(root, "run", "--no-input")
    assert r.returncode == 0, r.stderr[-2000:]
    return root


def _ok(r):
    assert r.returncode == 0, (r.returncode, r.stdout[-1500:],
                               r.stderr[-1500:])
    return r


class _CaChain(unittest.TestCase):
    """A closed 2025 Canada project where AMT binds (carryover in from
    [estimate] amt_carryover, one origin expired), and its 2026
    successor."""
    tmp = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        t = Path(cls.tmp)
        p25 = _project(t / "ca25", "canada", 2025, _CA_BOOK_25,
                       "[estimate]\nother_income = 40000\n"
                       "amt_carryover = { 2017 = 999, 2019 = 3000, "
                       "2023 = 5000 }\n",
                       province="ON")
        _ok(cli(p25, "close-year", "--yes"))
        cls.lock25 = json.loads((p25 / "filed" / "2025.json").read_text())
        _project(t / "ca26", "canada", 2026, _CA_BOOK_26,
                 "[estimate]\nother_income = 250000\n", province="ON",
                 prior_year_record="../ca25/filed/2025.json")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def root(self, name):
        return Path(self.tmp) / name


class TestCaChain(_CaChain):

    @rule("CA-CARRY-01", "CA-AMT-02", "CA-AMT-03")
    def test_close_year_records_the_carry_forwards(self):
        cf = self.lock25["carryforwards"]
        mt = cf["minimum_tax"]
        self.assertEqual(mt["opening_by_year"],
                         {"2019": 3000.0, "2023": 5000.0})
        self.assertEqual(mt["expired_by_year"], {"2017": 999.0})
        self.assertGreater(mt["created"], 0)
        self.assertEqual(mt["closing_by_year"]["2025"], mt["created"])
        self.assertEqual(mt["recovered_federal"], 0.0)
        self.assertEqual(mt["opening_source"], "[estimate] amt_carryover")
        ncl = cf["net_capital_loss"]
        self.assertEqual((ncl["created"], ncl["closing"]), (0.0, 0.0))

    @rule("CA-AMT-01", "CA-AMT-04", "CA-AMT-08", "CA-CARRY-03")
    def test_next_year_reads_the_lock_and_amt_agrees_with_estimate(self):
        root = self.root("ca26")
        est = json.loads(_ok(cli(root, "estimate", "--json")).stdout)
        amt = json.loads(_ok(cli(root, "amt", "--json")).stdout)
        self.assertEqual(est["estimate"]["amt"], amt["amt"])
        c = amt["amt"]["carryover"]
        mt = self.lock25["carryforwards"]["minimum_tax"]
        self.assertEqual(c["available_by_year"], mt["closing_by_year"])
        self.assertAlmostEqual(c["recovered_federal"], mt["closing"],
                               places=2)
        self.assertIn("2025 close-year record",
                      amt["carry_sources"]["amt_carryover"])
        text = _ok(cli(root, "amt")).stdout
        for want in ("ADJUSTED TAXABLE INCOME (s.127.52)",
                     "Basic exemption (s.127.53)",
                     "MINIMUM TAX CARRYOVER (s.120.2)",
                     "Recovered: federal (line 40427)", "s.127.531"):
            self.assertIn(want, text)
        etext = _ok(cli(root, "estimate")).stdout
        self.assertIn("MINIMUM TAX CARRYOVER", etext)
        self.assertIn("2025 close-year record", etext)

    @rule("CA-AMT-01")
    def test_amt_for_a_closed_year_prints_the_record(self):
        root = self.root("ca26")
        r = _ok(cli(root, "amt", "2025", "--json"))
        doc = json.loads(r.stdout)
        self.assertTrue(doc["recorded"])
        self.assertEqual(doc["minimum_tax"],
                         self.lock25["carryforwards"]["minimum_tax"])
        r = cli(root, "amt", "2023")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("2023 project", r.stderr)

    @rule("CA-AMT-08")
    def test_explicit_input_wins_over_the_lock(self):
        root = self.root("ca26")
        f = root / "taxjson.toml"
        toml = f.read_text()
        f.write_text(toml.replace(
            "other_income = 250000",
            "other_income = 250000\namt_carryover = { 2024 = 100 }"))
        try:
            amt = json.loads(_ok(cli(root, "amt", "--json")).stdout)
            self.assertEqual(amt["amt"]["carryover"]["available_by_year"],
                             {"2024": 100.0})
            self.assertEqual(amt["carry_sources"]["amt_carryover"],
                             "[estimate] amt_carryover")
        finally:
            f.write_text(toml)

    @rule("CA-CARRY-05")
    def test_handoff_flags_a_mismatched_amt_input(self):
        root = self.root("ca26")
        _ok(cli(root, "handoff"))
        f = root / "taxjson.toml"
        toml = f.read_text()
        mt = self.lock25["carryforwards"]["minimum_tax"]
        f.write_text(toml.replace(
            "other_income = 250000",
            "other_income = 250000\namt_carryover = { 2019 = 3000, "
            f"2023 = 4000, 2025 = {mt['closing_by_year']['2025']} }}"))
        try:
            r = cli(root, "handoff", "--json")
            self.assertEqual(r.returncode, 1, r.stderr)
            carry = json.loads(r.stdout)["carry"]
            self.assertEqual([i["what"] for i in carry],
                             ["[estimate] amt_carryover 2023"])
        finally:
            f.write_text(toml)

    @rule("CA-CARRY-05")
    def test_handoff_flags_other_losses_and_claimed(self):
        root = self.root("ca26")
        toml = (root / "taxjson.toml").read_text()
        (root / "taxjson.toml").write_text(
            toml.replace("other_income = 250000",
                         "other_income = 250000\nother_losses = 5")
            + "\n[carryover]\nclaimed = { 2025 = 10 }\n")
        try:
            r = cli(root, "handoff", "--json")
            self.assertEqual(r.returncode, 1, r.stderr)
            whats = [i["what"] for i in json.loads(r.stdout)["carry"]]
            self.assertEqual(sorted(whats), ["[carryover] claimed 2025",
                                             "[estimate] other_losses"])
        finally:
            (root / "taxjson.toml").write_text(toml)


class TestCaLossChain(unittest.TestCase):
    """A loss year's net capital loss flows into the next year."""

    @rule("CA-CARRY-01", "CA-CARRY-02", "CA-CARRY-03", "CA-CARRY-04")
    def test_net_capital_loss_flows(self):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            p25 = _project(
                t / "a25", "canada", 2025,
                "BUYSELL 2025-01-02 10:00:00 XYZ.TO 100 CAD 100 10000 0\n"
                "BUYSELL 2025-03-03 10:00:00 XYZ.TO -100 CAD 40 4000 0\n",
                "[estimate]\nother_losses = 1000\n")
            _ok(cli(p25, "close-year", "--yes"))
            ncl = json.loads((p25 / "filed" / "2025.json").read_text()
                             )["carryforwards"]["net_capital_loss"]
            self.assertEqual(ncl, {"opening": 1000.0, "opening_source":
                                   "[estimate] other_losses",
                                   "created": 6000.0, "applied": 0.0,
                                   "closing": 7000.0})
            p26 = _project(t / "a26", "canada", 2026, _CA_BOOK_26,
                           prior_year_record="../a25/filed/2025.json")
            doc = json.loads(_ok(cli(p26, "estimate", "--json",
                                     "--province", "ON")).stdout)
            e = doc["estimate"]
            self.assertEqual(e["losses_opening"], 7000.0)
            self.assertEqual(e["losses_applied"], 7000.0)
            self.assertIn("2025 close-year record",
                          e["carry_sources"]["other_losses"])
            # Explicit input always wins.
            e2 = json.loads(_ok(cli(p26, "estimate", "--json",
                                    "--province", "ON",
                                    "--other-losses", "0")).stdout
                            )["estimate"]
            self.assertEqual(e2["losses_opening"], 0.0)
            self.assertEqual(e2["carry_sources"]["other_losses"],
                             "--other-losses")
            text = _ok(cli(p26, "estimate", "--province", "ON")).stdout
            self.assertIn("Net capital losses carried in: 7,000.00 — "
                          "from ../a25/filed/2025.json", text)
            # The ledger starts 2026 from the lock's balance.
            led = json.loads(_ok(cli(p26, "carryover", "--json")).stdout)
            r25 = next(r for r in led["rows"] if r["year"] == 2025)
            self.assertTrue(r25["balance_from_lock"])
            self.assertEqual(r25["carryforward_balance"], 7000.0)
            r26 = next(r for r in led["rows"] if r["year"] == 2026)
            self.assertEqual(r26["available_to_apply"], 7000.0)


# --------------------------------------------------------- dual country
class TestDualCountry(unittest.TestCase):

    @rule("CA-AMT-01")
    @rule_absent("CA-AMT-01", country="usa")
    @rule("US-AMT-01")
    def test_amt_is_canada_only(self):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            ca = _project(t / "ca", "canada", 2025, _CA_BOOK_25,
                          province="ON")
            us = _project(t / "us", "usa", 2025, _US_BOOK_25)
            _ok(cli(ca, "amt"))
            r = cli(us, "amt")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("Canada-only", r.stderr)
            self.assertIn("Form 6251", r.stderr)
            toml = (us / "taxjson.toml").read_text()
            (us / "taxjson.toml").write_text(
                toml + "[estimate]\namt_carryover = { 2024 = 100 }\n")
            r = cli(us, "estimate")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("amt_carryover is Canada-only", r.stderr)
            self.assertIn("Form 6251", _text("usa", "US-AMT-01"))

    @rule("CA-AMT-04")
    @rule_absent("CA-AMT-04", country="usa")
    def test_us_estimate_has_no_minimum_tax_carryover(self):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            ca = _project(t / "ca", "canada", 2025, _CA_BOOK_25,
                          "[estimate]\nother_income = 40000\n"
                          "amt_carryover = { 2023 = 500 }\n",
                          province="ON")
            us = _project(t / "us", "usa", 2025, _US_BOOK_25,
                          "[estimate]\nother_income = 40000\n")
            e_ca = json.loads(_ok(cli(ca, "estimate", "--json")).stdout
                              )["estimate"]
            e_us = json.loads(_ok(cli(us, "estimate", "--json")).stdout
                              )["estimate"]
            self.assertIn("carryover", e_ca["amt"])
            self.assertEqual(e_ca["amt"]["carryover"]["available_by_year"],
                             {"2023": 500.0})
            self.assertNotIn("amt", e_us)

    @rule("CA-CARRY-01")
    @rule_absent("CA-CARRY-01", country="usa")
    @rule("US-CARRY-01", "US-CARRY-02", "US-CARRY-03", "US-CARRY-04")
    @rule_absent("US-CARRY-01", country="canada")
    def test_close_year_records_each_countrys_carryover(self):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            ca = _project(t / "ca", "canada", 2025, _CA_BOOK_25,
                          province="ON")
            us = _project(t / "us", "usa", 2025, _US_BOOK_25,
                          "[estimate]\nother_income = 40000\n")
            _ok(cli(ca, "close-year", "--yes"))
            _ok(cli(us, "close-year", "--yes"))
            cf_ca = json.loads((ca / "filed" / "2025.json").read_text()
                               )["carryforwards"]
            cf_us = json.loads((us / "filed" / "2025.json").read_text()
                               )["carryforwards"]
            self.assertEqual(sorted(k for k in cf_ca if k not in
                                    ("version", "computed_by")),
                             ["minimum_tax", "net_capital_loss"])
            self.assertEqual(sorted(k for k in cf_us if k not in
                                    ("version", "computed_by")),
                             ["capital_loss"])
            cl = cf_us["capital_loss"]
            # -8,000 short-term: 3,000 used, 5,000 carried.
            self.assertEqual((cl["st_closing"], cl["lt_closing"]),
                             (5000.0, 0.0))
            # The next US year reads it (US-CARRY-02) ...
            us26 = _project(t / "us26", "usa", 2026, _US_BOOK_26,
                            prior_year_record="../us/filed/2025.json")
            e = json.loads(_ok(cli(us26, "estimate", "--json")).stdout
                           )["estimate"]
            self.assertEqual((e["carryover_short_term"],
                              e["carryover_long_term"]), (5000.0, 0.0))
            self.assertIn("2025 close-year record",
                          e["carry_sources"]["other_losses"])
            # ... the ledger takes it (US-CARRY-04) ...
            led = json.loads(_ok(cli(us26, "carryover", "--json")).stdout)
            r25 = next(r for r in led["rows"] if r["year"] == 2025)
            self.assertTrue(r25["balance_from_lock"])
            self.assertEqual(r25["st_carryover"], 5000.0)
            # ... and handoff flags a mismatched input (US-CARRY-03).
            toml = (us26 / "taxjson.toml").read_text()
            (us26 / "taxjson.toml").write_text(
                toml + "[estimate]\nother_losses = 4000\n")
            r = cli(us26, "handoff", "--json")
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertEqual([i["what"] for i in
                              json.loads(r.stdout)["carry"]],
                             ["[estimate] other_losses"])


if __name__ == "__main__":
    unittest.main()
