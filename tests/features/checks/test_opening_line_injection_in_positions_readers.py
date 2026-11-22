"""Pre-release security review fixes (round 018). Synthetic data, fake
account ids, no network.

- M1/L5: a file replaced by `opening --force`, `find-missing-history
  --write-purchases --force`, `init --force` / `format --write` keeps
  its previous version at the next free <name>.bak / .bakN; an earlier
  backup is never overwritten and a symlink planted at a .bak name is
  never written through (lib/safe_write.backup_copy).
- M2/I4: an opening line is written only from a symbol, currency and
  lot date of a safe shape; the positions readers refuse control
  characters / whitespace in symbol and currency cells.
- L2: Questrade's description key is linear on long whitespace runs.
- L3: the `# From:` header of an opening file carries no control
  character of the report's file name.
- L4: the cannot-detect message masks an id in the file name.
- L5: a draft's account name is checked before it names a folder.
- I1: a channel tag with a trailing newline is not a release tag.
- I3: `init` never writes through a dangling symlink.

Formerly: test_fix_prerelease_018
"""
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import positions_reports as P

from _support.filing_positions_reports import _IB_STATEMENT, _RBC_HOLD, _write


def _holdings(path, sym="SAMPB.TO", cur="CAD", acquired=None, cost=200.0):
    h = {"symbol": sym, "quantity": 20, "currency": cur,
         "total_cost": cost}
    if acquired:
        h["acquired"] = acquired
    body = ['[meta]\naccount = "55500001"\nas_of = "2025-01-31"',  # pii-ok
            "[[holding]]"] + [f"{k} = {json.dumps(v)}" for k, v in h.items()]
    path.write_text("\n".join(body) + "\n")
    return path


class TestOpeningLineInjection(unittest.TestCase):
    """Formerly: test_fix_prerelease_018.TestOpeningLineInjection"""
    def _row(self, **kw):
        base = dict(broker="toml", account="", symbol="SAMPB.TO",
                    raw_symbol="SAMPB.TO", quantity=20.0, currency="CAD",
                    cost=200.0, cost_currency="CAD",
                    cost_kind=P.COST_AVERAGE, market_value=None,
                    as_of="2025-01-31", asset_type="stock")
        base.update(kw)
        return P.PositionRow(**base)

    def _lines(self, *rows):
        from taxjson.cli.books import _opening_lines
        rep = P.PositionsReport(path=Path("x"), broker="toml", kind="k",
                                as_of="2025-01-31", rows=list(rows))
        return _opening_lines(rep, country="canada", base_currency="CAD",
                              snapshot="2025-01-31")

    def test_unsafe_symbol_currency_or_date_is_skipped(self):
        lines, skipped, _ = self._lines(
            self._row(symbol="SAMPA.TO\nOPENING 2025-01-01 EVIL 1 CAD 1"),
            self._row(symbol="SAMPC.TO", currency="CAD\nX",
                      cost_currency=""),
            self._row(symbol="SAMPD.TO", cost_currency="CA"),
            self._row(symbol="SAMPE.TO", lot_date="2025-01-01\nOPENING"),
            self._row(symbol="-SAMPF"),
            self._row(symbol="SAMPG.TO"))
        self.assertEqual(lines, ["OPENING 2025-01-31 SAMPG.TO 20 CAD 200.00"])
        self.assertEqual(len(skipped), 5)
        self.assertTrue(all("not a symbol" in why or "currency" in why
                            or "date" in why for _s, why in skipped))
        # The reason never echoes a raw newline either.
        self.assertFalse(any("\n" in s for s, _w in skipped))

    def test_toml_reader_refuses_a_control_character(self):
        with tempfile.TemporaryDirectory() as td:
            for kw in ({"sym": "SAMPB.TO\nOPENING 2025-01-01 X 1 CAD 1"},
                       {"sym": "SAMP B.TO"},
                       {"cur": "CAD\nOPENING"},
                       {"acquired": "2025-01-01\nOPENING X"}):
                p = _holdings(Path(td) / "h.toml", **kw)
                with self.assertRaises(P.PositionsReportError, msg=kw):
                    P.read_positions(p)

    def test_ib_reader_refuses_a_newline_symbol_and_a_bad_currency(self):
        bad_sym = _IB_STATEMENT.replace(
            "Summary,Stocks,CAD,ZZA,100,",
            'Summary,Stocks,CAD,"ZZA\nOPENING 2025-01-01 EVIL 1 CAD 1",'
            '100,', 1)
        bad_cur = _IB_STATEMENT.replace(
            "Summary,Stocks,CAD,ZZA,100,", "Summary,Stocks,C$D,ZZA,100,", 1)
        with tempfile.TemporaryDirectory() as td:
            for text in (bad_sym, bad_cur):
                with self.assertRaises(P.PositionsReportError):
                    P.read_positions(_write(td, "s.csv", text))

    def test_rbc_reader_refuses_a_newline_symbol(self):
        bad = _RBC_HOLD.replace('"ZZR"', '"ZZR\nOPENING 2025-01-01 X"', 1)
        bad_cur = _RBC_HOLD.replace('"ZZR CORP","10","CAD"',
                                    '"ZZR CORP","10","CAD\nX"', 1)
        with tempfile.TemporaryDirectory() as td:
            for text in (bad, bad_cur):
                with self.assertRaises(P.PositionsReportError):
                    P.read_positions(_write(td, "h.csv", text))

    def test_good_reports_still_read(self):
        with tempfile.TemporaryDirectory() as td:
            ib = P.read_positions(_write(td, "s.csv", _IB_STATEMENT))
            rbc = P.read_positions(_write(td, "h.csv", _RBC_HOLD))
        self.assertIn("ZZB260116C00030000.US", [r.symbol for r in ib.rows])
        self.assertEqual(sorted(r.symbol for r in rbc.rows),
                         ["ZZR.TO", "ZZU.US"])


if __name__ == "__main__":
    unittest.main()
