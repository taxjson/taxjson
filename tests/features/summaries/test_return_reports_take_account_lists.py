"""`tjs sum`, `tjs gains-base` and `tjs gains-cra` take an ACCOUNT list
(`tjs sum margin,beta`, lib/account_list; cli/common `_accounts`,
`_view_scope`): they read the listed accounts' books and no other, in
whatever order the names are given. The rows, the per-account rows and
the summary tables' per-account figures are the single-account runs'
added; the Schedule 3 lines and the RETURN row round each security's
total over the listed accounts together, so they can differ from the
single runs' added by a cent per security sold in more than one listed
account; the three reports give the list one RETURN row, cell for cell
(src/taxjson/cli/estimate.py `_sum_inputs`, `_sum_return_heading`;
src/taxjson/cli/gains_filing.py `cmd_gains_base`, `_gains_base_filing`,
`cmd_gains_cra`, `_gains_cra_taxable_only`; src/taxjson/lib/gains_cra.py
`report`, `render`, `_totals_heading`; src/taxjson/lib/gains_base.py
`_named`).

A listed account with no books, or one not in [accounts], is refused,
exit 1, naming it (a single such name keeps its exit 2); gains-cra's
rule, taxable accounts only, checks every name (exit 1, naming each
sheltered one). gains-cra's --json keeps "scope": {"account": the text
given} and adds "account_names" there; sum's and gains-base's --json add
"account_names" (null for every account). With ACCOUNT, no report reads
as the whole return: sum's and gains-cra's heading "FOR ACCOUNTS margin,
beta — ... (not the whole return)", gains-base's TOTALS heading too and
its return row labelled "tjs sum margin,beta", the estimate's title "...,
for accounts margin, beta only" and its unused losses and AMT "for these
accounts". With no ACCOUNT they read as before.

The project: margin and beta (taxable), a superficial loss across them
(ZZQ.TO sold at a loss in margin, bought back in beta within 30 days and
held), QQS.TO sold in both (a third of a cent in each sale's ACB: the
list's Schedule 3 line is a cent from the two runs' added), a USD sale in
beta, a sale with no purchase in beta (UUN.TO: not in the totals), rrsp
and tfsa (sheltered), idle (taxable, no inputs, no books). Made-up
tickers and amounts.
"""
import csv
import re
import unittest
from decimal import Decimal
from pathlib import Path

from _support.filing_mixed_currency_project import Project, cents
from _tmpfiles import private_dir

TOML = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
        'country = "canada"\nbase_currency = "CAD"\nsource_currencies = ["USD"]\n'
        '[accounts.margin]\ntype = "taxable"\n[accounts.beta]\ntype = "taxable"\n'
        '[accounts.rrsp]\ntype = "sheltered"\n[accounts.tfsa]\ntype = "sheltered"\n'
        '[accounts.idle]\ntype = "taxable"\n')

FILES = {
    "margin": ("BUYSELL 2025-01-06 10:00:00 ZZQ.TO 10 CAD 100.00 1000.00 0\n"
               "BUYSELL 2025-03-06 10:00:00 ZZQ.TO -10 CAD 80.00 800.00 0\n"
               "BUYSELL 2025-02-03 10:00:00 YYB.TO 5 CAD 20.00 100.00 0\n"
               "BUYSELL 2025-06-02 10:00:00 YYB.TO -5 CAD 30.00 145.05 4.95\n"
               "DIVIDEND 2025-04-01 10:00:00 YYB.TO 0 CAD 0 12.50 0\n"
               "BUYSELL 2025-01-07 10:00:00 QQS.TO 3 CAD 3.3333 10.00 0\n"
               "BUYSELL 2025-06-02 10:00:00 QQS.TO -1 CAD 5.00 5.00 0\n"),
    "beta": ("BUYSELL 2025-03-20 10:00:00 ZZQ.TO 4 CAD 85.00 340.00 0\n"
             "BUYSELL 2025-01-10 10:00:00 XXR.TO 20 CAD 50.00 1000.00 0\n"
             "BUYSELL 2025-05-06 10:00:00 XXR.TO -20 CAD 40.00 795.00 5.00\n"
             "BUYSELL 2025-02-12 10:00:00 VVU.US 10 USD 20.00 200.00 0\n"
             "BUYSELL 2025-07-14 10:00:00 VVU.US -10 USD 25.00 250.00 0\n"
             "BUYSELL 2025-09-02 10:00:00 UUN.TO -5 CAD 10.00 50.00 0\n"
             "BUYSELL 2025-01-08 10:00:00 QQS.TO 3 CAD 3.3333 10.00 0\n"
             "BUYSELL 2025-06-03 10:00:00 QQS.TO -1 CAD 5.00 5.00 0\n"),
    "rrsp": ("BUYSELL 2025-02-10 10:00:00 WWS.TO 5 CAD 20.00 100.00 0\n"
             "BUYSELL 2025-08-01 10:00:00 WWS.TO -5 CAD 25.00 125.00 0\n"),
    "tfsa": "BUYSELL 2025-02-10 10:00:00 TTS.TO 5 CAD 20.00 100.00 0\n",
}

# The RETURN row's money columns, by tjs sum's keys (lib/gain_columns).
CELLS = ("proceeds", "acb", "outlays", "total_cost", "gain", "denied")
_MONEY = re.compile(r"-?[\d,]+\.\d\d")


def _added(*totals):
    """The cells of several RETURN rows added, at the cent."""
    return {k: sum((cents(t[k]) for t in totals), Decimal("0.00")) for k in CELLS}


def _cells(t):
    return {k: cents(t[k]) for k in CELLS}


def _gb_return(doc):
    """gains-base's THE RETURN in tjs sum's keys (GAIN is `allowed`)."""
    r = doc["totals"]["return"]
    return {k: cents(r["allowed" if k == "gain" else k]) for k in CELLS}


def _row_cells(text, label):
    """The money cells of the text's row led by `label` (the last one)."""
    rows = [ln for ln in text.splitlines() if ln.startswith(label + " ")]
    if not rows:
        raise AssertionError(f"no {label} row in:\n{text}")
    return [m.replace(",", "") for m in _MONEY.findall(rows[-1])]


_PROJECT = []
# Each command's run, kept: every test reads the same books.
_RUNS = {}
_DOCS = {}


def setUpModule():
    p = Project(files=FILES, toml=TOML)
    p.run()
    _PROJECT.append(p)


def tearDownModule():
    _PROJECT.clear()
    _RUNS.clear()
    _DOCS.clear()


class _Lists(unittest.TestCase):
    """The project, built once for the module."""

    def tjs(self, *argv, **env):
        key = (argv, tuple(sorted(env.items())))
        if key not in _RUNS:
            _RUNS[key] = _PROJECT[0].tjs(*argv, **env)
        return _RUNS[key]

    def json(self, *argv):
        if argv not in _DOCS:
            _DOCS[argv] = _PROJECT[0].json(*argv)
        return _DOCS[argv]

    def refused(self, argv, code, *words):
        r = self.tjs(*argv)
        self.assertEqual(r.returncode, code, r.stdout[-1500:] + r.stderr[-1500:])
        for w in words:
            self.assertIn(w, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.stdout, "")
        return r.stderr


class TestTheThreeReportsGiveAListOneReturnRow(_Lists):
    """One trade gives one figure everywhere: the list's RETURN row in
    sum, gains-cra and gains-base, the same cells. Against the single
    accounts' runs: the rows add; the RETURN row rounds each security
    over the listed accounts together (QQS.TO, sold in both: a cent)."""

    def test_the_figures(self):
        listed = _cells(self.json("sum", "margin,beta")["filing"]["totals"])
        self.assertGreater(listed["denied"], 0)        # the superficial loss across them
        self.assertEqual(_cells(self.json("gains-cra", "margin,beta")["totals"]), listed)
        self.assertEqual(_gb_return(self.json("gains-base", "margin,beta")), listed)
        self.assertEqual(_cells(self.json("gains-base", "margin,beta")["totals"]["filing"]
                                ["totals"]), listed)
        # Every taxable account with books: the whole return's figures.
        self.assertEqual(_cells(self.json("sum")["filing"]["totals"]), listed)

    def test_a_security_sold_in_two_listed_accounts(self):
        """QQS.TO's sale in each account has an ACB of 3.3333...: each
        run's line says 3.33, the list's line 6.67 for the two, a cent
        more than the runs added (and the gain a cent less); proceeds,
        outlays and the denied loss add. The rows and the per-account
        rows add exactly."""
        listed = _cells(self.json("sum", "margin,beta")["filing"]["totals"])
        singles = _added(self.json("sum", "margin")["filing"]["totals"],
                         self.json("sum", "beta")["filing"]["totals"])
        cent = Decimal("0.01")
        self.assertEqual({k: listed[k] - singles[k] for k in CELLS},
                         {"proceeds": 0, "acb": cent, "outlays": 0, "total_cost": cent,
                          "gain": -cent, "denied": 0})
        doc = self.json("sum", "margin,beta")
        for k, v in doc["totals"].items():
            self.assertEqual(cents(v), cents(self.json("sum", "margin")["totals"][k])
                             + cents(self.json("sum", "beta")["totals"][k]), k)
        rows = self.json("gains-cra", "margin,beta")["rows_total"]
        self.assertEqual(_cells(rows), _added(self.json("gains-cra", "margin")["rows_total"],
                                              self.json("gains-cra", "beta")["rows_total"]))
        # gains-base says why its rows and the list's row differ.
        text = " ".join(self.tjs("gains-base", "margin,beta").stdout.split())
        self.assertIn("The taxable rows differ from the tjs sum margin,beta row by COST(ACB) "
                      "-0.01, TOTAL COST -0.01, GAIN +0.01 CAD: Schedule 3 rounds each "
                      "security's total, the rows each sale.", text)

    def test_the_text_rows(self):
        s = _row_cells(self.tjs("sum", "margin,beta").stdout, "RETURN")
        self.assertEqual(len(s), len(CELLS))
        self.assertEqual(_row_cells(self.tjs("gains-cra", "margin,beta").stdout, "RETURN"), s)
        self.assertEqual(_row_cells(self.tjs("gains-base", "margin,beta").stdout,
                                    "tjs sum margin,beta"), s)

    def test_a_sale_in_one_listed_account_with_no_purchase(self):
        """The advisories read the listed accounts (the names, never the
        comma text): beta's UUN.TO, which is in no total."""
        def kinds(doc):
            return [i["kind"] for i in doc["readiness"]["issues"]]
        self.assertEqual([(r["symbol"], r["account"]) for r in
                          self.json("sum", "margin,beta")["no_purchase_uncovered"]],
                         [("UUN.TO", "beta")])
        self.assertEqual(self.json("sum", "margin")["no_purchase_uncovered"], [])
        self.assertIn("no-purchase", kinds(self.json("gains-cra", "margin,beta")))
        self.assertNotIn("no-purchase", kinds(self.json("gains-cra", "margin")))


class TestSum(_Lists):
    """`tjs sum ACCOUNT[,ACCOUNT...]`."""

    def test_the_union_of_the_accounts(self):
        doc = self.json("sum", "margin,beta")
        one = {a: self.json("sum", a) for a in ("margin", "beta")}
        self.assertEqual(doc["accounts"],
                         sorted(one["beta"]["accounts"] + one["margin"]["accounts"],
                                key=lambda r: r["account"]))
        for k, v in doc["totals"].items():
            self.assertEqual(cents(v), cents(one["margin"]["totals"][k])
                             + cents(one["beta"]["totals"][k]), k)
        self.assertEqual([a["account"] for a in doc["filing"]["accounts"]], ["beta", "margin"])
        self.assertEqual(doc["account_names"], ["margin", "beta"])
        text = self.tjs("sum", "margin,beta").stdout
        self.assertIn("FOR ACCOUNTS margin, beta — CAD (Schedule 3, tax year 2025; not the "
                      "whole return)\n", text)
        self.assertNotIn("FOR THE RETURN", text)
        self.assertNotIn("rrsp", text)

    def test_the_order_given_moves_nothing(self):
        """Only the echoes of the names keep their order: the JSON's
        `account_names` and the heading."""
        ab, ba = self.json("sum", "margin,beta"), self.json("sum", "beta,margin")
        self.assertEqual(ba["account_names"], ["beta", "margin"])
        self.assertEqual({k: v for k, v in ab.items() if k != "account_names"},
                         {k: v for k, v in ba.items() if k != "account_names"})
        self.assertEqual(self.tjs("sum", "beta,margin").stdout,
                         self.tjs("sum", "margin,beta").stdout.replace(
                             "FOR ACCOUNTS margin, beta", "FOR ACCOUNTS beta, margin"))

    def test_the_estimate_says_whose_it_is(self):
        """The estimate of a list covers those accounts alone: its title
        says so, and the lines computed on them say "for these accounts"
        ("for this account" for one name); the carried-in losses are the
        person's, as before."""
        argv = ("--other-income", "50000", "--province", "ON")
        doc = self.json("sum", "margin,beta", *argv)
        self.assertGreater(doc["estimate"]["losses_unused"], 0)
        text = self.tjs("sum", "margin,beta", *argv).stdout
        title = [ln for ln in text.splitlines() if ln.startswith("TAX ESTIMATE — ")]
        self.assertEqual(len(title), 1, text)
        self.assertTrue(title[0].endswith(", for accounts margin, beta only"), title)
        self.assertIn("\n  Unused capital losses for these accounts:  ", text)
        self.assertRegex(text, r"\nAMT CHECK — .*exemption\), for these accounts\n")
        self.assertIn("\n  Net capital losses carried in:  0.00 — ", text)
        # One name: in the singular.
        self.assertGreater(self.json("sum", "margin", *argv)["estimate"]["losses_unused"], 0)
        one = self.tjs("sum", "margin", *argv).stdout
        self.assertRegex(one, r"\nTAX ESTIMATE — .*, for account margin only\n")
        self.assertIn("\n  Unused capital losses for this account:  ", one)
        self.assertRegex(one, r"\nAMT CHECK — .*exemption\), for this account\n")
        self.assertNotIn("these accounts", one)
        whole = self.tjs("sum", *argv).stdout
        self.assertRegex(whole, r"\nTAX ESTIMATE — canada/ON, rates vintage \S+\n")
        self.assertNotIn("for these accounts", whole)
        self.assertNotIn("for this account", whole)
        self.assertNotIn("not the whole return", whole)
        self.assertIn("FOR THE RETURN — taxable accounts (beta, margin), CAD", whole)
        self.assertIsNone(self.json("sum")["account_names"])

    def test_a_sheltered_account_in_the_list(self):
        """sum rolls up every account it is given, as `tjs sum rrsp`;
        the return is the taxable ones'."""
        doc = self.json("sum", "margin,rrsp")
        self.assertEqual([a["account"] for a in doc["accounts"]], ["margin", "rrsp"])
        self.assertEqual(doc["sheltered_included"], ["rrsp"])
        self.assertEqual(_cells(doc["filing"]["totals"]),
                         _cells(self.json("sum", "margin")["filing"]["totals"]))

    def test_a_listed_account_with_no_books_or_not_configured(self):
        self.refused(("sum", "margin,idle"), 1, "no gains file for account 'idle' in work/")
        self.refused(("sum", "idle,margin,nope"), 1, "no [accounts.nope] in taxjson.toml")

    def test_one_name(self):
        self.refused(("sum", "idle"), 2, "no gains for account 'idle' in work/")
        self.refused(("sum", "nope"), 2, "no gains for account 'nope' in work/")
        self.assertIn("FOR ACCOUNT margin — CAD (Schedule 3, tax year 2025; not the whole "
                      "return)\n", self.tjs("sum", "margin").stdout)
        self.assertEqual(self.json("sum", "margin")["account_names"], ["margin"])


class TestGainsBase(_Lists):
    """`tjs gains-base [PERIOD] ACCOUNT[,ACCOUNT...]`."""

    def test_the_union_of_the_accounts(self):
        doc = self.json("gains-base", "margin,beta")
        one = {a: self.json("gains-base", a) for a in ("margin", "beta")}
        self.assertEqual(doc["rows"],
                         sorted(one["margin"]["rows"] + one["beta"]["rows"],
                                key=lambda r: (r["date"], r["account"], r["symbol"], r["id"])))
        for k, v in doc["totals"]["taxable"].items():
            self.assertEqual(cents(v), cents(one["margin"]["totals"]["taxable"][k])
                             + cents(one["beta"]["totals"]["taxable"][k]), k)
        self.assertEqual(doc["totals"]["filing"]["command"], "tjs sum margin,beta")
        self.assertEqual(one["margin"]["totals"]["filing"]["command"], "tjs sum margin")
        self.assertEqual(doc["account_names"], ["margin", "beta"])
        self.assertEqual(one["margin"]["account_names"], ["margin"])
        self.assertIsNone(self.json("gains-base")["account_names"])
        self.assertIn("ACCT ", self.tjs("gains-base", "margin,beta").stdout)

    def test_the_return_row_says_whose_it_is(self):
        """With ACCOUNT the row is `tjs sum ACCOUNT`'s, labelled by that
        command, under a TOTALS heading saying it is not the whole
        return; with no ACCOUNT, THE RETURN (tjs sum), as before."""
        text = self.tjs("gains-base", "margin,beta").stdout
        self.assertIn("\nTOTALS — CAD, accounts margin, beta (not the whole return)\n", text)
        self.assertNotIn("THE RETURN", text)
        one = self.tjs("gains-base", "margin").stdout
        self.assertIn("\nTOTALS — CAD, account margin (not the whole return)\n", one)
        self.assertEqual(len(_row_cells(one, "tjs sum margin")), len(CELLS))
        self.assertNotIn("THE RETURN", one)
        whole = self.tjs("gains-base").stdout
        self.assertIn("\nTOTALS — CAD\n", whole)
        self.assertEqual(len(_row_cells(whole, "THE RETURN (tjs sum)")), len(CELLS))
        self.assertNotIn("not the whole return", whole)

    def test_the_order_given_moves_no_figure(self):
        ab, ba = self.json("gains-base", "margin,beta"), self.json("gains-base", "beta,margin")
        self.assertEqual(ba["totals"]["filing"]["command"], "tjs sum beta,margin")
        self.assertEqual({k: v for k, v in ab["totals"]["filing"].items() if k != "command"},
                         {k: v for k, v in ba["totals"]["filing"].items() if k != "command"})
        self.assertEqual(ab["rows"], ba["rows"])
        self.assertEqual({k: v for k, v in ab["totals"].items() if k != "filing"},
                         {k: v for k, v in ba["totals"].items() if k != "filing"})

    def test_with_a_period(self):
        self.assertEqual(self.json("gains-base", "tax_year", "margin,beta")["rows"],
                         self.json("gains-base", "margin,beta")["rows"])
        whole = self.json("gains-base", "all", "margin,beta")
        self.assertEqual({r["account"] for r in whole["rows"]}, {"margin", "beta"})
        self.assertIsNone(whole["totals"]["return"])
        # No return row: nothing to say it is not the whole return.
        self.assertIn("\nTOTALS — CAD\n", self.tjs("gains-base", "all", "margin,beta").stdout)
        self.refused(("gains-base", "ytd,margin"), 1, "A period never has a comma.")

    def test_a_sheltered_account_in_the_list(self):
        doc = self.json("gains-base", "margin,rrsp")
        self.assertEqual([(a["account"], a["type"]) for a in doc["totals"]["accounts"]],
                         [("margin", "taxable"), ("rrsp", "sheltered")])
        self.assertEqual(_gb_return(doc), _cells(self.json("sum", "margin")["filing"]["totals"]))
        self.assertEqual(doc["totals"]["filing"]["command"], "tjs sum margin,rrsp")

    def test_a_listed_account_with_no_books_or_not_configured(self):
        self.refused(("gains-base", "margin,idle"), 1,
                     "no gains file for account 'idle' in work/")
        self.refused(("gains-base", "all", "margin,nope"), 1,
                     "no [accounts.nope] in taxjson.toml")

    def test_one_name(self):
        self.refused(("gains-base", "idle"), 2, "no gains for account 'idle' in work/")
        self.refused(("gains-base", "nope"), 2, "no gains for account 'nope' in work/")


class TestGainsCra(_Lists):
    """`tjs gains-cra ACCOUNT[,ACCOUNT...]` (each taxable)."""

    def test_the_union_of_the_accounts(self):
        doc = self.json("gains-cra", "margin,beta")
        one = {a: self.json("gains-cra", a) for a in ("margin", "beta")}
        self.assertEqual(doc["rows"],
                         sorted(one["margin"]["rows"] + one["beta"]["rows"],
                                key=lambda r: (r["date"], r["account"], r["symbol"], r["id"])))
        self.assertEqual(doc["rows"], self.json("gains-cra")["rows"])
        self.assertEqual(_cells(doc["rows_total"]),
                         _added(one["margin"]["rows_total"], one["beta"]["rows_total"]))
        self.assertEqual([t["currency"] for t in doc["tables"]], ["CAD", "USD"])

    def test_the_scope(self):
        self.assertEqual(self.json("gains-cra", "margin,beta")["scope"],
                         {"account": "margin,beta", "account_names": ["margin", "beta"]})
        self.assertEqual(self.json("gains-cra", "beta,margin")["scope"],
                         {"account": "beta,margin", "account_names": ["beta", "margin"]})
        self.assertEqual(self.json("gains-cra", "margin")["scope"],
                         {"account": "margin", "account_names": ["margin"]})
        self.assertIsNone(self.json("gains-cra")["scope"])

    def test_the_order_given_moves_no_figure(self):
        ab, ba = self.json("gains-cra", "margin,beta"), self.json("gains-cra", "beta,margin")
        self.assertEqual({k: v for k, v in ab.items() if k != "scope"},
                         {k: v for k, v in ba.items() if k != "scope"})

    def test_the_text_names_the_list(self):
        text = self.tjs("gains-cra", "margin,beta").stdout
        self.assertIn("tax year 2025, accounts margin, beta, Schedule 3's cells", text)
        self.assertIn("\nFOR ACCOUNTS margin, beta — Schedule 3, as tjs sum shows it for these "
                      "accounts (not the whole return)\n", text)
        self.assertNotIn("FOR THE RETURN", text)
        self.assertIn("ALL SALES — CAD", text)
        one = self.tjs("gains-cra", "margin").stdout
        self.assertIn("tax year 2025, account margin, Schedule 3's cells", one)
        self.assertIn("\nFOR ACCOUNT margin — Schedule 3, as tjs sum shows it for this account "
                      "(not the whole return)\n", one)
        self.assertIn("\nFOR THE RETURN — Schedule 3, as tjs form-export and tjs sum show it\n",
                      self.tjs("gains-cra").stdout)

    def test_the_heading_fits_the_width(self):
        """The list named once, the heading wrapped where it is wider than
        the report."""
        text = self.tjs("gains-cra", "margin,beta", TAXJSON_WIDTH="80").stdout
        lines = text.splitlines()
        at = next(i for i, ln in enumerate(lines) if ln.startswith("FOR ACCOUNTS "))
        self.assertEqual(" ".join(lines[at:at + 2]),
                         "FOR ACCOUNTS margin, beta — Schedule 3, as tjs sum shows it for these "
                         "accounts (not the whole return)")
        self.assertTrue(all(len(ln) <= 80 for ln in lines[at:at + 2]), lines[at:at + 2])
        self.assertEqual(lines[at - 1], "")

    def test_the_csv(self):
        def rows(*argv):
            path = Path(private_dir()) / "gains-cra.csv"
            r = self.tjs(*argv, "--csv", str(path))
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            with open(path, newline="", encoding="utf-8") as f:
                return list(csv.reader(f))
        listed, margin, beta = (rows("gains-cra", "margin,beta"), rows("gains-cra", "margin"),
                                rows("gains-cra", "beta"))
        self.assertEqual(listed[0], margin[0])
        self.assertEqual(sorted(listed[1:]), sorted(margin[1:] + beta[1:]))

    def test_every_name_must_be_taxable(self):
        self.refused(("gains-cra", "margin,rrsp"), 1, "'rrsp' is not a taxable account",
                     "`tjs gains-base rrsp` lists its rows; without it: `tjs gains-cra margin`.")
        self.refused(("gains-cra", "rrsp,margin,tfsa"), 1,
                     "'rrsp', 'tfsa' are not taxable accounts",
                     "`tjs gains-base rrsp,tfsa` lists their rows; without them: `tjs gains-cra "
                     "margin`.")
        err = self.refused(("gains-cra", "rrsp,tfsa"), 1,
                           "'rrsp', 'tfsa' are not taxable accounts",
                           "`tjs gains-base rrsp,tfsa` lists their rows.")
        self.assertNotIn("without", err)

    def test_a_listed_account_with_no_books_or_not_configured(self):
        # The words sum and gains-base give it (cli/common `_gains_files_of`).
        self.refused(("gains-cra", "margin,idle"), 1, "no gains file for account 'idle' in work/",
                     "check the names (an account with no inputs has no books)")
        self.refused(("gains-cra", "margin,nope,rrsp"), 1,
                     "no [accounts.nope] in taxjson.toml")

    def test_one_name(self):
        err = self.refused(("gains-cra", "rrsp"), 1, "'rrsp' is not a taxable account",
                           "`tjs gains-base rrsp` lists its rows.")
        self.assertNotIn("without", err)
        self.refused(("gains-cra", "idle"), 2, "no gains for account 'idle' in work/")
        self.refused(("gains-cra", "nope"), 2, "no gains for account 'nope' in work/")


if __name__ == "__main__":
    unittest.main()
