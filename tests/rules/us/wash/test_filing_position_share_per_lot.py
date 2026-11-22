"""A US filing position on a sale over several lots: each gain row's
note carries its own share of the denial the rule would have made, and
the rows of one fill add up to the fill's record (backlog TJ-3095;
lib/loss_overrides, tax-logic US-WASH-25).

A sale the FIFO walk fills from two lots is two gain rows with one id.
`stamp_entries` put the fill's whole `would_disallow` on each, so the
US `tjs wash-radar`, which adds the rows up, showed twice the figure
(1000 for a record of 500), as did form-export's merged positions; the
form's per-row notes called the fill's figure each row's own.

Every fixture is SYNTHETIC: made-up ZZQ tickers, accounts 99900002 and
U1234567, invented amounts.

Formerly: test_fix_tj3095_radar_shares
"""
import contextlib
import io
import json
import tempfile
import unittest

from tax_rules import rule
from tax_rules.dual import cli, projects_both, tx
from taxjson.rules import ids as R

from taxjson.engine.by_country import get_tax_rules
from taxjson.engine.us.taxrules import USATaxRules
from taxjson.lib import loss_overrides as LO

B, A, Z = "99900002", "U1234567", "ZZQ.US"


def _ov(date, qty, account=B):
    return {"account": account, "date": date, "symbol": Z, "qty": qty,
            "reason": "r", "where": f"inputs/{account}/x.tt:1",
            "line": f'ALLOWLOSS {date} {Z} {qty} reason="r"'}


def _run(rows, ov):
    with contextlib.redirect_stderr(io.StringIO()):
        return get_tax_rules("usa").compute_gains(
            rows, loss_overrides=(ov,), per_account_basis=True)


def _gains(res):
    return [g for g in res["transactions"] if "gain" in g and "qty" in g]


def _notes(res, sale_id):
    return [g["loss_override"] for g in _gains(res) if g["id"] == sale_id]


# Two lots sold in one fill (50 + 50), a replacement for the first only.
def _two_lots(first_cost=-2500.0):
    return [tx("BUYSELL", "2024-12-30", Z, 50, first_cost, account=B),
            tx("BUYSELL", "2025-01-02", Z, 50, -2500.0, account=B),
            tx("BUYSELL", "2025-01-03", Z, 50, -2500.0, account=B),
            tx("BUYSELL", "2025-01-04", Z, -100, 4000.0, account=B)]


class TestRowShares(unittest.TestCase):

    @rule(R.US_WASH_25)
    def test_each_lots_row_carries_its_own_share(self):
        res = _run(_two_lots(), _ov("2025-01-04", 100))
        lo, = res["loss_overrides"]
        self.assertEqual(lo["status"], LO.STATUS_APPLIED)
        sale, = lo["sales"]
        self.assertAlmostEqual(float(sale["would_disallow"]), 500.0)
        self.assertAlmostEqual(float(sale["loss"]), 1000.0)
        notes = _notes(res, sale["ids"][0])
        self.assertEqual(len(notes), 2)
        # The first lot's 50 units matched the 2025-01-03 buy; the second
        # lot's found no replacement.
        self.assertEqual([round(float(n["would_disallow"]), 2)
                          for n in notes], [500.0, 0.0])
        self.assertAlmostEqual(
            sum(float(n["would_disallow"]) for n in notes),
            float(sale["would_disallow"]))
        for n in notes:
            self.assertAlmostEqual(float(n["sale_would_disallow"]), 500.0)
            self.assertAlmostEqual(float(n["would_permanent"]), 0.0)
        # The walk's share keys never reach the result.
        for g in res["transactions"]:
            self.assertFalse([k for k in g if k.startswith("_")], g)

    @rule(R.US_WASH_25)
    def test_a_gaining_lot_beside_a_losing_one(self):
        # The first lot (bought at 30) gains 500, the second (at 50)
        # loses 500 and is matched by the 2025-01-03 buy.
        res = _run(_two_lots(first_cost=-1500.0), _ov("2025-01-04", 100))
        sale, = res["loss_overrides"][0]["sales"]
        self.assertAlmostEqual(float(sale["would_disallow"]), 500.0)
        self.assertAlmostEqual(float(sale["loss"]), 500.0)
        rows = [g for g in _gains(res) if g["id"] == sale["ids"][0]]
        self.assertEqual([round(float(g["raw_gain"]), 2) for g in rows],
                         [500.0, -500.0])
        # The lot that gained claims no loss: no note on it (TJ-3108).
        self.assertNotIn("loss_override", rows[0])
        self.assertAlmostEqual(float(rows[1]["loss_override"]["would_disallow"]), 500.0)

    @rule(R.US_WASH_25)
    def test_the_radar_reads_the_fill_once(self):
        from taxjson.bin.taxjson_wash_radar import _us_engine_losses
        with contextlib.redirect_stderr(io.StringIO()):
            out = _us_engine_losses(_two_lots(), [], (_ov("2025-01-04", 100),))
        v, = out.values()
        self.assertAlmostEqual(v["loss"], 1000.0)
        self.assertAlmostEqual(v["would_disallow"], 500.0)
        # The position is the fill's, not its last row's (TJ-3110).
        self.assertAlmostEqual(v["position"]["would_disallow"], 500.0)

    def test_the_radar_counts_a_share_the_loss_test_passes_over(self):
        """A row whose loss a later adjustment took to about zero keeps
        its share; the radar adds it to the fill's (TJ-3109)."""
        from unittest import mock
        from taxjson.bin.taxjson_wash_radar import _us_engine_losses

        def note(dis):
            return {"where": "w", "reason": "r", "would_disallow": dis, "would_permanent": 0.0,
                    "sale_would_disallow": 503.0, "sale_would_permanent": 0.0}
        rows = [{"id": "99900004", "qty": -50.0, "gain": -500.0, "raw_gain": -500.0, "date": "2025-01-04",
                 "symbol": Z, "loss_override": note(500.0)},
                {"id": "99900004", "qty": -50.0, "gain": 0.0, "raw_gain": 0.0, "date": "2025-01-04",
                 "symbol": Z, "loss_override": note(3.0)}]
        with mock.patch.object(USATaxRules, "compute_gains", lambda *a, **k: {"transactions": rows}):
            v, = _us_engine_losses([], [], (_ov("2025-01-04", 100),)).values()
        self.assertAlmostEqual(v["loss"], 500.0)
        self.assertAlmostEqual(v["would_disallow"], 503.0)

    @rule(R.US_WASH_22)
    def test_a_split_row_splits_its_share(self):
        """The named sale's shares, bought in the window of another
        account's later loss, are matched as a sold replacement: the
        row is split (`_us_split_gain_entry`) and each half carries its
        share of what the rule would have disallowed."""
        rows = [tx("BUYSELL", "2025-01-02", Z, 100, -5000.0, account=B),
                tx("BUYSELL", "2025-01-04", Z, -100, 4000.0, account=B),
                tx("BUYSELL", "2025-01-06", Z, 100, -4500.0, account=A),
                tx("BUYSELL", "2025-01-20", Z, -40, 1400.0, account=A)]
        res = _run(rows, _ov("2025-01-04", 100))
        lo, = res["loss_overrides"]
        self.assertEqual(lo["status"], LO.STATUS_APPLIED)
        sale, = lo["sales"]
        self.assertAlmostEqual(float(sale["would_disallow"]), 1000.0)
        b_rows = [g for g in _gains(res) if g["id"] == sale["ids"][0]]
        self.assertEqual([round(g["qty"], 6) for g in b_rows], [40.0, 60.0])
        self.assertTrue(b_rows[0].get("wash_basis_added"))
        self.assertEqual(
            [round(float(g["loss_override"]["would_disallow"]), 2)
             for g in b_rows], [400.0, 600.0])
        for g in res["transactions"]:
            self.assertFalse([k for k in g if k.startswith("_")], g)


def _files():
    return {"inputs/margin/m.tt": (
        "BUYSELL 2024-12-30 10:00:00 ZZQ.US 50 USD 50.00 2500.00 0.00\n"
        "BUYSELL 2025-01-02 10:00:00 ZZQ.US 50 USD 50.00 2500.00 0.00\n"
        "BUYSELL 2025-01-03 10:00:00 ZZQ.US 50 USD 50.00 2500.00 0.00\n"
        "BUYSELL 2025-01-04 10:00:00 ZZQ.US -100 USD 40.00 4000.00 0.00\n"
        'ALLOWLOSS 2025-01-04 ZZQ.US 100 reason="my position"\n')}


class TestCommands(unittest.TestCase):
    """The radar's sentence and the form's rows on the repro project."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = projects_both(cls._td.name, files=_files())["usa"]
        r = cli(cls.root, "run", "--no-input")
        assert r.returncode == 0, r.stdout + r.stderr

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    @rule(R.US_WASH_25)
    def test_wash_radar_shows_the_sales_figure_once(self):
        r = cli(self.root, "wash-radar", "--date", "2025-01-10", "--details")
        text = " ".join(r.stdout.split())
        self.assertIn("the loss of 1,000.00 USD on 2025-01-04 is claimed as "
                      "your filing position against §1091 (ALLOWLOSS "
                      "inputs/margin/m.tt:5): the rule would disallow "
                      "500.00 USD; the books allow it", text)

    @rule(R.US_WASH_25)
    def test_form_8949_rows_carry_their_own_shares(self):
        doc = json.loads(cli(self.root, "form-export", "--json").stdout)
        shares = [r["filing_position"]["would_disallow"] for r in doc["part_I"]]
        self.assertEqual(shares, [500.0, 0.0])
        text = " ".join(cli(self.root, "form-export", "--details").stdout.split())
        self.assertIn("the wash-sale rule would disallow 500.00; the loss is "
                      "claimed in full", text)
        self.assertNotIn("would disallow 1,000.00", text)


if __name__ == "__main__":
    unittest.main()
