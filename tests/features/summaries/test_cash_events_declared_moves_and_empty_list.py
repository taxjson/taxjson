"""Backlog group fxcorp: the FX-on-cash ledger v2 (lib/fx_cash_v2, the
Kraken cash reader) and the spin-off views and checks (lib/corp_views,
lib/missing_history, lib/positions_check, `tjs sanity`). One test
per backlog item, each named for it. Synthetic data only.

Formerly: test_fix_backlog_fxcorp"""
import unittest

from taxjson.lib import cash_events as CE
from taxjson.lib import cash_lines as CL
from taxjson.lib import fx_cash_v2 as FX


def _tt(book, line, n):
    """A .tt cash line of `book` (acct/label), as the reader gives it."""
    where = f"inputs/{book.split('/')[0]}/cash.tt:{n}"
    e = CL.parse_line(line, where)
    e.update(book=book, origin="tt", where=where,
             label=book.split("/")[1])
    return e


def _build(lines=(), events=(), year=2026, native=(), carry=None,
           rate=1.40, base="CAD", country="canada", **cash):
    return FX.build(list(native), dict(cash, events=list(events),
                                       lines=list(lines)),
                    base, year, lambda c, d: rate, country=country,
                    carry=carry)


# ------------------------------------------------------ TJ-0054, TJ-0168

class TestCashEventsRead(unittest.TestCase):
    """Formerly: test_fix_backlog_fxcorp.TestCashEventsRead"""
    def _read(self):
        dep = dict(CL.move("2026-02-01", "USD", 1000.0,
                           settle="2026-02-01"),
                   book="crypto/kraken", label="kraken", origin="broker",
                   desc="Kraken deposit USD")
        ln = _tt("crypto/kraken",
                 "CASHMOVE 2026-02-01 USD 1000.00 cost=1340.00 at=kraken", 2)
        opn = _tt("crypto/kraken", "CASHOPEN 2026-01-01 USD 0 0 at=kraken",
                  1)
        CE.apply_declarations([dep], [ln])
        self.assertTrue(ln.get("matched"))
        doc = _build(lines=[opn, ln], events=[dep])
        return doc, [opn, dep, ln]

    def test_tj0054_declared_move_listed_once(self):
        """TJ-0054: `fx-cash --cash-events` listed a .tt CASHMOVE that
        declared a broker's deposit as a second deposit (four lines for
        two moves). The broker's move is listed once, with its declared
        cost and the line that declared it."""
        doc, evs = self._read()
        text = FX.render(doc, None, "canada", width_=160, cash_events=evs)
        part = text.split("CASH EVENTS READ", 1)[1]
        moves = [ln for ln in part.splitlines() if "CASHMOVE" in ln]
        self.assertEqual(len(moves), 1, part)
        self.assertIn("cost=1,340.00", moves[0])
        self.assertIn("DECLARED BY", part)
        self.assertIn("inputs/crypto/cash.tt:2", moves[0])
        self.assertIn("CASHOPEN", part)

    def test_tj0168_empty_list_says_so(self):
        """TJ-0168: `--cash-events` with no cash event read printed no
        section at all (the same output as without the flag)."""
        doc, _evs = self._read()
        text = FX.render(doc, None, "canada", width_=160, cash_events=[])
        self.assertIn("CASH EVENTS READ\nNone read from the exports or "
                      ".tt lines.", text)
        self.assertNotIn("CASH EVENTS READ",
                         FX.render(doc, None, "canada", width_=160))


if __name__ == "__main__":
    unittest.main()
