"""IB one-contract-id ticker changes in a year folder that reads its
exports from a folder shared by every year (`inputs_dir = "../inputs"`):
the same events and books as a single-folder project.

The IB parse found the project from the statement's path (inputs/../),
which in the shared layout is the folder holding the year folders: the
project's .tt RENAME lines and its ticker.map's dated RENAME lines were
not read, so a change they decide was judged from the contract id alone
(pre-release review of the multi-year layout, H2). The run now names the
project to its stages (TAXJSON_PROJECT_ROOT).

The cases of rules.ca.acb.test_contract_id_ticker_changes run again,
each project laid out as a year folder
(tests/_support/ib_conid_renames's mixins, each class's `_run` building
the shared layout), plus a ticker.map dated RENAME in both layouts.

Every fixture is SYNTHETIC: invented QZ* tickers and names, fake account
ids (pii-ok: U5550001).

Formerly: test_fix_multi_year_conid
"""
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import cli, projects_both, settings_for
from taxjson.rules import ids as R
from _support.ib_statement_rows import FII_H, HEAD, TRADES_H, _trade
from _support.ib_conid_renames import (CorporateActionRenamesCases,
                                       DatesPerContractIdCases,
                                       DeclarationsAndPlaceholdersCases,
                                       NewEventBeforeNewFirstTradeCases,
                                       OldRowAfterNewFirstRowCases)

YEAR = 2025


def shared_projects(tmp, *, year=YEAR,
                    accounts='[accounts.margin]\ntype = "taxable"\n',
                    files=None):
    """projects_both's two projects as year folders: <tmp>/<country>/
    <year>/ with inputs_dir = "../inputs"; a file under inputs/ goes to
    the shared folder, every other one into the year folder."""
    out = {}
    for c in ("canada", "usa"):
        top = Path(tmp) / c
        root = top / str(year)
        root.mkdir(parents=True, exist_ok=True)
        (root / "taxjson.toml").write_text(
            settings_for(c, year=year, inputs_dir="../inputs") + accounts)
        for rel, text in (files or {}).items():
            p = (top / rel) if Path(rel).parts[0] == "inputs" else root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        out[c] = root
    return out


def _shared_run(files, country, *extra):
    td = tempfile.TemporaryDirectory()
    root = shared_projects(td.name, files=files)[country]
    r = cli(root, "run", "--no-input", "--strict", *extra)
    return td, root, r


class SharedNewEventBeforeNewFirstTrade(NewEventBeforeNewFirstTradeCases, unittest.TestCase):
    _run = staticmethod(_shared_run)


class SharedCorporateActionRenames(CorporateActionRenamesCases, unittest.TestCase):
    _run = staticmethod(_shared_run)


class SharedDatesPerContractId(DatesPerContractIdCases, unittest.TestCase):
    _run = staticmethod(_shared_run)


class SharedOldRowAfterNewFirstRow(OldRowAfterNewFirstRowCases, unittest.TestCase):
    _run = staticmethod(_shared_run)


class SharedDeclarationsAndPlaceholders(DeclarationsAndPlaceholdersCases, unittest.TestCase):
    _run = staticmethod(_shared_run)


_ONE_ID = ('Financial Instrument Information,Data,Stocks,"QZNB, QZOA",'
           'QZNB CORP,990000779,US9990007791,,NYSE,1,,,COMMON,,\n')


class TestMapDatedRenameInBothLayouts(unittest.TestCase):
    """A ticker.map dated RENAME is checked against the contract id's
    rows like a .tt line: a declared date after NEW already trades is
    refused at the parse, naming the line to write — in a year folder
    too (its ticker.map is in the year folder, its exports in the shared
    folder; the parse used to miss the map and the run failed later in
    the gains engine)."""

    def _check(self, make):
        body = (HEAD + TRADES_H
                + _trade('QZOA', '2025-02-05, 10:00:00', 100, 10, -1000)
                + _trade('QZNB', '2025-05-12, 10:00:00', -100, 12, 1200,
                         code='C')
                + FII_H + _ONE_ID)
        with tempfile.TemporaryDirectory() as td:
            root = make(td, files={
                "inputs/margin/ib.csv": body,
                "ticker.map": "RENAME QZOA.US QZNB.US 2025-05-14\n"})[
                    "canada"]
            r = cli(root, "run", "--no-input")
            out = " ".join((r.stdout + r.stderr).split())
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("ticker.map:1: the ticker change QZOA.US -> "
                          "QZNB.US is declared on 2025-05-14", out)
            self.assertIn("`RENAME 2025-05-12 QZOA.US QZNB.US`", out)
            self.assertNotIn("rename would merge", out)

    @rule(R.CA_ACB_RENAME)
    def test_single_folder(self):
        self._check(projects_both)

    @rule(R.CA_ACB_RENAME)
    def test_year_folder(self):
        self._check(shared_projects)


if __name__ == "__main__":
    unittest.main()
