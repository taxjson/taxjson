"""`taxjson edge-cases` on a US project (backlog TJ-0284, also the tjs
command audit's TJ-0339): the disallowed losses carried in the positions
held at the end of the project year come from the US engine's lots at
the end of Dec 31 (`inventory_year_end`, tax-logic US-RPT-13), not from
the gains file's final inventory, so a later sale or purchase leaves
them alone.

Engine-backed: run_gains computes every row from synthetic trades
(ZZQ.US, ZZR.US; made-up amounts), written to work/ as `taxjson run`
writes them; one test drives `taxjson run` and `taxjson edge-cases` on a
.tt project, as the audit's reproduction did.
"""
import contextlib
import copy
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

from taxjson.bin.taxjson_split_gains import split_for_account
from taxjson.lib.core import TaxTransaction, get_tax_rules
from taxjson.lib.dispatch import python_module_argv
from taxjson.lib.edge_cases import analyze, render_text
from taxjson.lib.pipeline import GainsRequest, run_gains

SRC = Path(__file__).resolve().parents[1] / "src"
STOCK = "ZZQ.US"
TITLE = "DENIED LOSSES CARRIED IN POSITIONS HELD AT THE END OF 2025 (1)"


def _t(acct, day, qty, net, sym=STOCK, settle=None):
    return TaxTransaction(account=acct, action="BUYSELL", date=day,
                          date_settle=settle or day, time="10:00:00",
                          symbol=sym, quantity=qty, net_amount=net,
                          currency="USD", id=f"{acct}-{day}-{sym}-{qty}")


# A 500.00 loss on 2025-11-17, disallowed: 10 shares bought back on
# 2025-11-20 take it into their basis (§1091(d)). At the end of 2025:
# 10 units, 500.00 deferred.
WASHED = [_t("margin", "2025-11-03", 10, -1000),
          _t("margin", "2025-11-17", -10, 500),
          _t("margin", "2025-11-20", 10, -500)]


def _gains(rows, tax_date="trade", **req):
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        return run_gains(copy.deepcopy(rows), [],
                         req=GainsRequest(country="usa", year=2025,
                                          taxable=True, tax_date=tax_date,
                                          **req))


def _edge(books, *, tax_date="trade", **req):
    """The gains run, analyze() and the --details text of a US project
    whose taxable accounts are `books` ({account: rows}); more than one
    account is the blended pass split per account (taxjson-split-gains),
    as the run does."""
    rows = [t for b in books.values() for t in b]
    doc = _gains(rows, tax_date, **req)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        work = root / "work"
        work.mkdir()
        for n, b in books.items():
            base = [t.to_dict() for t in b]
            (work / f"{n}_base.json").write_text(
                json.dumps({"transactions": base}))
            out = doc if len(books) == 1 else split_for_account(doc, n,
                                                                 base)
            (work / f"{n}_gains_wash.json").write_text(json.dumps(out))
        cfg = {"settings": {"year": 2025, "country": "usa",
                            "tax_date": tax_date},
               "accounts": {n: {"type": "taxable"} for n in books}}
        edge = analyze(root, cfg)
        text = "\n".join(render_text(edge, details=True, width_=200))
    return doc, edge, text


def _deferred(edge, sym=STOCK):
    return [(r["account"], r["qty"], r["deferred"], r["as_of"])
            for r in edge["year_boundary"]["deferred_at_year_end"]
            if r["symbol"] == sym]


def _held(rows, sym=STOCK):
    return [(h.get("account"), h["qty"], h["deferred_wash"])
            for h in rows if h["symbol"] == sym]


@rule("US-RPT-13")
class TestUSDeferredAtYearEnd(unittest.TestCase):
    """The US engine's lots at the end of Dec 31, whatever comes after."""

    def test_next_year_partial_sale(self):
        doc, edge, text = _edge({"margin": WASHED + [
            _t("margin", "2026-02-10", -4, 320)]})
        # The final inventory is after the sale; the year end is not.
        self.assertEqual(_held(doc["inventory"]), [("margin", 6, 300.0)])
        self.assertEqual(_held(doc["inventory_year_end"]),
                         [("margin", 10, 500.0)])
        self.assertEqual(_deferred(edge),
                         [("margin", 10, 500.0, "2025-12-31")])
        (r,) = edge["year_boundary"]["deferred_at_year_end"]
        self.assertIn("500.00 of denied losses sits in the cost of the 10 "
                      "units still held at the end of 2025; it reduces",
                      r["why"])
        self.assertNotIn("end of the books", r["why"])
        # The text: the same row and reason (--details), and the row in
        # the default view.
        self.assertIn(TITLE, text)
        self.assertIn(r["why"], " ".join(text.split()))
        default = "\n".join(render_text(edge, width_=200))
        self.assertIn(TITLE, default)
        self.assertRegex(default, r"margin\s+ZZQ\.US\s+10\s+500\.00")

    def test_next_year_full_sale_keeps_the_holding(self):
        doc, edge, _ = _edge({"margin": WASHED + [
            _t("margin", "2026-02-10", -10, 800)]})
        self.assertEqual(_held(doc["inventory"]), [])
        self.assertEqual(_deferred(edge),
                         [("margin", 10, 500.0, "2025-12-31")])

    def test_next_year_purchase_is_not_counted(self):
        doc, edge, _ = _edge({"margin": WASHED + [
            _t("margin", "2026-03-02", 7, -700)]})
        self.assertEqual(_held(doc["inventory"]), [("margin", 17, 500.0)])
        self.assertEqual(_deferred(edge),
                         [("margin", 10, 500.0, "2025-12-31")])

    def test_later_rows_change_nothing(self):
        # Appending later trades outside any 2025 window leaves the year
        # end, and every figure of the year, as they were.
        alone_doc, alone, _ = _edge({"margin": WASHED})
        more_doc, more, _ = _edge({"margin": WASHED + [
            _t("margin", "2026-02-02", 4, -40, sym="ZZR.US"),
            _t("margin", "2026-03-02", -1, 100),
            _t("margin", "2026-04-01", 3, -300)]})
        self.assertEqual(alone["year_boundary"]["deferred_at_year_end"],
                         more["year_boundary"]["deferred_at_year_end"])
        self.assertEqual(alone_doc["inventory_year_end"],
                         more_doc["inventory_year_end"])
        for key in ("transactions", "wash_sales"):
            self.assertEqual(alone_doc[key], more_doc[key])

    def test_january_replacement_is_not_held_at_year_end(self):
        # A December loss whose replacement is bought in January: the
        # walk is not cut at Dec 31, so the January purchase still
        # disallows the loss, and its lot takes the basis add, but that
        # lot was not held on Dec 31.
        doc, edge, _ = _edge({"margin": [
            _t("margin", "2025-11-03", 10, -1000),
            _t("margin", "2025-12-15", -10, 500),
            _t("margin", "2026-01-05", 10, -600)]})
        (loss,) = [g for g in doc["transactions"]
                   if g.get("symbol") == STOCK and g.get("qty")]
        self.assertAlmostEqual(loss["disallowed_amount"], 500.0)
        self.assertEqual(_held(doc["inventory"]), [("margin", 10, 500.0)])
        self.assertEqual(_held(doc["inventory_year_end"]), [])
        self.assertEqual(_deferred(edge), [])
        (w,) = [w for w in edge["year_boundary"]["loss_windows"]
                if w["symbol"] == STOCK]
        self.assertEqual(w["denied"], 500.0)

    def test_next_year_loss_on_a_december_purchase(self):
        # A January loss whose replacement was bought in December: on
        # Dec 31 nothing was disallowed yet, so no 2025 position carries
        # it (the final inventory's 250.00 is a 2026 deferral).
        doc, edge, _ = _edge({"margin": [
            _t("margin", "2025-11-03", 10, -1000),
            _t("margin", "2025-12-22", 5, -400),
            _t("margin", "2026-01-05", -10, 500)]})
        self.assertEqual(_held(doc["inventory"]), [("margin", 5, 250.0)])
        self.assertEqual(_held(doc["inventory_year_end"]),
                         [("margin", 15, 0.0)])
        self.assertEqual(_deferred(edge), [])

    def test_short_lots(self):
        # A short covered at a loss and re-shorted in the window: the
        # replacement short's proceeds carry the deferral (Reg.
        # §1.1091-1(g)); covered in 2026, it is still open on Dec 31.
        doc, edge, _ = _edge({"margin": [
            _t("margin", "2025-11-03", -10, 1000),
            _t("margin", "2025-11-17", 10, -1500),
            _t("margin", "2025-11-20", -10, 1000),
            _t("margin", "2026-02-10", 10, -900)]})
        self.assertEqual(_held(doc["inventory"]), [])
        self.assertEqual(_held(doc["inventory_year_end"]),
                         [("margin", -10, 500.0)])
        self.assertEqual([(a, q, d) for a, q, d, _s in _deferred(edge)],
                         [("margin", -10, 500.0)])

    def test_blended_accounts_keep_their_own_lots(self):
        # margin's loss is washed by margin2's purchase (§1091 across
        # your accounts); margin2 sells in 2026. The split gives margin2
        # its own Dec 31 lot (FIFO is per account, US-BASIS-01).
        doc, edge, _ = _edge({
            "margin": WASHED[:2],
            "margin2": [_t("margin2", "2025-11-20", 10, -500),
                        _t("margin2", "2026-03-02", -10, 900)]})
        self.assertEqual(_held(doc["inventory"]), [])
        self.assertEqual(_deferred(edge),
                         [("margin2", 10, 500.0, "2025-12-31")])

    def test_settle_project_reads_settlement_dates(self):
        # The engine's walk runs on trade dates (US-WASH-01), but a
        # project on tax_date = "settle" reads its year end on
        # settlement dates (backlog TJ-0343; test_fix_1029_edgecases
        # has the sale that settles in January): no note.
        doc, edge, text = _edge({"margin": WASHED}, tax_date="settle")
        self.assertEqual(doc["inventory_year_end_basis"], "settle")
        (r,) = edge["year_boundary"]["deferred_at_year_end"]
        self.assertEqual(r["as_of"], "2025-12-31")
        self.assertIn("still held at the end of 2025;", r["why"])
        self.assertIn(r["why"], " ".join(text.split()))
        tdoc, trade, _ = _edge({"margin": WASHED})
        self.assertNotIn("inventory_year_end_basis", tdoc)
        self.assertNotIn("(by ", trade["year_boundary"]
                         ["deferred_at_year_end"][0]["why"])

    def test_no_filed_figure_changes(self):
        # The engine with and without a year end: the same rows,
        # disallowances, lots and totals; only the new key differs.
        rules = get_tax_rules("usa")
        rules.emit_replacement_stderr = False
        rows = WASHED + [_t("margin", "2025-12-15", -4, 300),
                         _t("margin", "2026-01-05", 4, -320),
                         _t("margin", "2026-02-10", -6, 480)]
        with contextlib.redirect_stderr(io.StringIO()):
            plain = rules.compute_gains(copy.deepcopy(rows),
                                        per_account_basis=True)
            ye = rules.compute_gains(copy.deepcopy(rows),
                                     per_account_basis=True,
                                     year_end="2025-12-31")
        self.assertNotIn("inventory_year_end", plain)
        self.assertEqual(_held(ye.pop("inventory_year_end")),
                         [("margin", 6, 300.0)])
        self.assertEqual(json.dumps(plain, sort_keys=True, default=str),
                         json.dumps(ye, sort_keys=True, default=str))


class TestUSRetroWash(unittest.TestCase):
    @rule("US-RPT-13", "US-WASH-22")
    def test_basis_add_to_a_sold_replacement_is_in_no_lot(self):
        # margin's 2025-01-10 loss is washed by margin2's purchase bought
        # and sold in December 2024, a filed (locked) year: the basis add
        # is booked as its own 2025 row (place_retro_wash_adjustments),
        # never in a lot held on Dec 31 2025.
        books = {
            "margin": [_t("margin", "2024-11-01", 10, -1000),
                       _t("margin", "2025-01-10", -10, 500),
                       _t("margin", "2025-06-02", 3, -300)],
            "margin2": [_t("margin2", "2024-12-20", 10, -800),
                        _t("margin2", "2024-12-28", -10, 850)]}
        doc, edge, _ = _edge(books, locked_years=(2024,))
        (add,) = [g for g in doc["transactions"]
                  if str(g.get("id", "")).endswith("-wash-basis")]
        self.assertAlmostEqual(add["gain"], -500.0)
        self.assertEqual(_held(doc["inventory_year_end"]),
                         [("margin", 3, 0.0)])
        self.assertEqual(_deferred(edge), [])


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=600)


@rule("US-RPT-13")
class TestUSYearEndCLI(unittest.TestCase):
    """The audit's reproduction (TJ-0339): `taxjson run`, then
    `taxjson edge-cases --json`, before and after a 2026 sale is added."""

    TT = ("BUYSELL 2025-11-03 10:00:00 ZZQ.US 10 USD 10 100 0\n"
          "BUYSELL 2025-11-17 10:00:00 ZZQ.US -10 USD 5 50 0\n"
          "BUYSELL 2025-11-20 10:00:00 ZZQ.US 10 USD 5 50 0\n")

    def test_a_next_year_sale_leaves_the_year_end(self):
        with tempfile.TemporaryDirectory(prefix="tj0284-") as td:
            root = Path(td)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "usa"\n'
                'base_currency = "USD"\nsource_currencies = []\n\n'
                '[accounts.margin]\ntype = "taxable"\n')
            seen = []
            for later in ("", "BUYSELL 2026-02-10 10:00:00 ZZQ.US -10 USD "
                              "8 80 0\n"):
                (root / "inputs" / "margin" / "m.tt").write_text(
                    self.TT + later)
                r = _cli(root, "run", "--no-input")
                self.assertEqual(r.returncode, 0, r.stderr)
                gains = json.loads((root / "work" / "margin_gains.json")
                                   .read_text(encoding="utf-8"))
                self.assertIn("inventory_year_end", gains)
                r = _cli(root, "edge-cases", "--json")
                self.assertEqual(r.returncode, 0, r.stderr)
                rows = json.loads(r.stdout)["year_boundary"][
                    "deferred_at_year_end"]
                seen.append(rows)
                r = _cli(root, "edge-cases", "--details")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn(TITLE, r.stdout)
                self.assertIn("50.00 of denied losses sits in the cost of "
                              "the 10 units still held at the end of 2025",
                              " ".join(r.stdout.split()))
            (first, after) = seen
            self.assertEqual(first, after)
            self.assertEqual([(x["account"], x["symbol"], x["qty"],
                               x["deferred"], x["as_of"]) for x in first],
                             [("margin", "ZZQ.US", 10.0, 50.0,
                               "2025-12-31")])


if __name__ == "__main__":
    unittest.main()
