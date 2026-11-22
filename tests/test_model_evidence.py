"""model/evidence.py: the packed evidence fields as records (step 6b(1)).

The readers and writers moved to the codecs keep their behaviour: each old
reader and writer is copied here as it was and run against the new one on
the writers' own output for generated inputs and on fuzzed strings; and
`format(parse(s)) == s` for what the writers make."""
import math
import random
import unittest
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, localcontext

from taxjson.lib.fx_cash_v2 import _refund_legs
from taxjson.lib.purchase_drafts import broker_lot_floats
from taxjson.model import evidence as V
from _support.model_number_strings import _key, NUMBERS


# --- the code as it was (lib/missing_history, lib/fx_cash_v2,
# ib_extractor, lib/opening, lib/core before step 6b) ----------------------

def old_parse_broker_lots(text):
    text = str(text or '').strip()
    if not text or text == 'invalid':
        return None
    out = []
    for part in text.split(';'):
        bits = part.split()
        if len(bits) != 3:
            return None
        try:
            d = date.fromisoformat(bits[0]).isoformat()
            q, c = float(bits[1]), float(bits[2])
        except ValueError:
            return None
        if q <= 0 or c < 0:
            return None
        out.append((d, q, c))
    return out or None


def old_refunds(raw):
    refunds = []
    for part in raw.split(";"):
        bits = part.split()
        if len(bits) != 2:
            continue
        try:
            datetime.strptime(bits[0], "%Y-%m-%d")
            refunds.append((bits[0], float(bits[1])))
        except ValueError:
            continue
    return refunds


def old_fold(existing, day, amount):
    parts = [x for x in str(existing or '').split('; ') if x]
    parts.append(f"{day} {round(float(amount), 10)!r}")
    return '; '.join(sorted(parts, key=lambda x: (x.split()[0], float(x.split()[1]))))


def old_cut_leg(leg):
    """A leg as the ledger read it: its four tokens, the numbers floats;
    'error' where unpacking or float() raised."""
    try:
        cday, qty, px, action = leg.split()
        return (cday, float(qty), float(px), action)
    except ValueError:
        return "error"


DATES = ["2024-01-02", "2024-1-2", "20240102", "2024-02-30", "bad", "2025-12-31"]
ACTIONS = ["BUYSELL", "ASSIGN", "X"]


def _fuzz(rng, tokens_per_part):
    parts = []
    for _ in range(rng.randrange(0, 4)):
        n = rng.choice(tokens_per_part)
        toks = [rng.choice(DATES)] + [rng.choice(NUMBERS) for _ in range(max(0, n - 1))]
        if n >= 4:
            toks[3] = rng.choice(ACTIONS)
        parts.append(" ".join(toks[:n]))
    sep = rng.choice([";", "; ", " ; ", ";;"])
    return rng.choice(["", " "]) + sep.join(parts) + rng.choice(["", ";", " "])


class TestBrokerLots(unittest.TestCase):
    def test_the_reader_is_unchanged(self):
        rng = random.Random(61)
        for _ in range(20_000):
            s = _fuzz(rng, [2, 3, 3, 3, 4])
            self.assertEqual(_key(broker_lot_floats(s)), _key(old_parse_broker_lots(s)), s)
        for s in ("invalid", " invalid ", "", None, "2024-01-02 1 1"):
            self.assertEqual(_key(broker_lot_floats(s)), _key(old_parse_broker_lots(s)), s)

    def test_the_writer_is_unchanged_and_reads_back(self):
        rng = random.Random(62)
        for _ in range(20_000):
            lots = []
            for _ in range(rng.randrange(1, 4)):
                qty = rng.choice([rng.uniform(1e-6, 1e6), float(rng.randrange(1, 5000)),
                                  10 ** rng.uniform(-11, 9)])
                basis = rng.choice([rng.uniform(0, 1e7), round(rng.uniform(0, 1e5), 2),
                                    10 ** rng.uniform(-3, 15)])
                lots.append(("2024-03-01", qty, basis))
            old = ""
            for d, q, b in lots:
                lot = f"{d} {q:.10g} {b:.2f}"
                old = f"{old};{lot}" if old else lot
            new = None
            for d, q, b in lots:
                new = V.add_broker_lot(new, V.broker_lot(d, q, b))
            self.assertEqual(new, old)
            self.assertEqual(V.format_broker_lots(V.broker_lot(d, q, b) for d, q, b in lots), old)
            parsed = V.parse_broker_lots(new)
            self.assertEqual(V.format_broker_lots(parsed), new)
            self.assertEqual([str(lot.quantity) for lot in parsed][0],
                             str(Decimal(new.split(";")[0].split()[1])))

    def test_a_lot_holds_the_decimals_of_its_tokens(self):
        lots = V.parse_broker_lots("2025-02-05 50 20001.00;2025-03-01 1e-05 0.10")
        self.assertEqual(lots, (V.BrokerLot("2025-02-05", Decimal("50"), Decimal("20001.00")),
                                V.BrokerLot("2025-03-01", Decimal("1e-05"), Decimal("0.10"))))
        self.assertIsNone(V.parse_broker_lots(V.BROKER_LOTS_INVALID))


class TestCommissionRefunds(unittest.TestCase):
    def test_the_reader_is_unchanged(self):
        rng = random.Random(63)
        for _ in range(20_000):
            s = _fuzz(rng, [1, 2, 2, 2, 3])
            new = [(r.date, float(r.amount)) for r in V.parse_commission_refunds(s)]
            self.assertEqual(_key(new), _key(old_refunds(s.strip())), s)

    def test_the_ledgers_legs_are_unchanged(self):
        legs = [("USD", -100.0, 99.5, ("2025-01-02", "2025-01-02", "10:00:00"))]
        row = {"currency": "USD", "commission_refunds": "2025-03-24 1.5; 2025-04-01 0.25"}
        self.assertEqual(_refund_legs(row, legs),
                         [("USD", -101.75, 99.5, ("2025-01-02", "2025-01-02", "10:00:00")),
                          ("USD", 1.5, None, ("2025-03-24", "2025-03-24", "")),
                          ("USD", 0.25, None, ("2025-04-01", "2025-04-01", ""))])

    def test_the_writer_is_unchanged_and_reads_back(self):
        rng = random.Random(64)
        for _ in range(5_000):
            old = new = ""
            for _ in range(rng.randrange(1, 5)):
                day = f"2025-0{rng.randrange(1, 10)}-{rng.randrange(10, 29)}"
                amount = rng.choice([rng.uniform(-50, 50), round(rng.uniform(0, 20), 2),
                                     1 / rng.randrange(1, 300), float(rng.randrange(0, 9)),
                                     1.2345678901234568e+16])
                old = old_fold(old, day, amount)
                new = V.add_commission_refund(new, V.commission_refund(day, amount))
                self.assertEqual(new, old)
            self.assertEqual(V.format_commission_refunds(V.parse_commission_refunds(new)), new)

    def test_the_fold_keeps_what_the_field_held(self):
        # A part the ledger would skip is kept as written; one the old
        # sort could not order raises as it did; a NaN or an infinity is
        # written as the f-string wrote it.
        for held, amount in (("2025-03-24 1.5; 20250325 0.25", 0.5), ("2025-03-24 nan", 1.0),
                             ("", math.inf), ("2025-01-02 5", math.nan), ("x", 1.0),
                             ("2025-01-02 1.5; 2025-01-03 abc", 1.0)):
            try:
                old = old_fold(held, "2025-04-01", amount)
            except (IndexError, ValueError) as e:
                with self.assertRaises(type(e)):
                    V.add_commission_refund(held, V.commission_refund("2025-04-01", amount))
                continue
            self.assertEqual(V.add_commission_refund(held, V.commission_refund("2025-04-01", amount)),
                             old)


class TestCutLegs(unittest.TestCase):
    def test_the_reader_is_unchanged_leg_by_leg(self):
        rng = random.Random(65)
        for _ in range(20_000):
            s = _fuzz(rng, [3, 4, 4, 4, 5])
            old_legs = [leg for leg in s.split(';') if leg]
            self.assertEqual(list(V.split_cut_legs(s)), old_legs)
            for leg in old_legs:
                try:
                    c = V.parse_cut_leg(leg)
                    new = (c.date, float(c.quantity), float(c.price), c.action)
                except V.EvidenceError:
                    new = "error"
                self.assertEqual(_key(new), _key(old_cut_leg(leg)), leg)

    def test_the_writer_is_unchanged_and_reads_back(self):
        rng = random.Random(66)
        for _ in range(20_000):
            legs = [("2024-12-27", rng.choice([float(rng.randrange(-500, 500) or 1),
                                               rng.uniform(-1e4, 1e4), 1.2345678901234568e+16]),
                     rng.choice([rng.uniform(0, 1e3), round(rng.uniform(0, 100), 2), 5.0,
                                 math.nan, math.inf]),
                     rng.choice(ACTIONS[:2])) for _ in range(rng.randrange(1, 4))]
            held = rng.choice(["", "2024-12-20 1.0 2.0 BUYSELL"])
            old = ";".join(filter(None, [held] + [f"{d} {q} {p} {a}" for d, q, p, a in legs]))
            new = V.join_cut_legs(held, [V.cut_leg(d, q, p, a) for d, q, p, a in legs])
            self.assertEqual(new, old)
            self.assertEqual(V.format_cut_legs(V.parse_cut_legs(new)),
                             new)

    def test_a_non_finite_or_long_number_reads_back(self):
        for s in ("2024-12-27 100.0 nan BUYSELL", "2024-12-27 -inf inf ASSIGN",
                  "2024-12-27 1.2345678901234568e+16 5.0 BUYSELL"):
            self.assertEqual(V.format_cut_legs(V.parse_cut_legs(s)), s)
        for s in ("2025-01-02 nan", "2025-01-02 1.2345678901234568e+16; 2025-01-03 inf"):
            self.assertEqual(V.format_commission_refunds(V.parse_commission_refunds(s)), s)
        self.assertEqual(V.join_cut_legs(None, []), "")   # lib/opening's _get gives '' for None

    def test_a_bad_leg_raises_an_evidence_error(self):
        for leg in ("2024-12-27 100.0 5.0", "2024-12-27 x 5.0 ASSIGN", "2024-12-27 1 sNaN ASSIGN"):
            with self.assertRaises(V.EvidenceError):
                V.parse_cut_leg(leg)
        self.assertTrue(issubclass(V.EvidenceError, ValueError))


class TestATokenPastDecimalsExponents(unittest.TestCase):
    def test_it_is_the_float_it_reads_as_in_any_context(self):
        for trapped in (True, False):
            with localcontext() as ctx:
                ctx.traps[InvalidOperation] = trapped
                (r,) = V.parse_commission_refunds("2025-01-02 1e999999999999999999999")
                self.assertEqual(float(r.amount), math.inf)
                (lot,) = V.parse_broker_lots("2025-01-02 10 1e-999999999999999999999")
                self.assertEqual(float(lot.cost), 0.0)
                self.assertEqual(float(V.parse_cut_leg("2025-01-02 -1e999999999999999999999 1 X")
                                       .quantity), -math.inf)


class TestTheRecords(unittest.TestCase):
    def test_frozen_slotted_and_exact(self):
        lot = V.BrokerLot("2024-01-02", Decimal("1"), Decimal("2.00"))
        self.assertFalse(hasattr(lot, "__dict__"))
        with self.assertRaises(AttributeError):
            lot.cost = Decimal(3)
        self.assertEqual(float(V.commission_refund("2025-01-02", 0.1).amount), 0.1)
        self.assertTrue(math.isnan(float(V.parse_broker_lots("2024-01-02 nan 1")[0].quantity)))


if __name__ == "__main__":
    unittest.main()
