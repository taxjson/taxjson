"""model/evidence.py: `corp_cash` and `settle_native` as records (step 6b(2)).

As in test_model_evidence: each old reader and writer is copied here as it
was and run against the new one on the writers' own output for generated
inputs and on fuzzed strings; what the writers make reads back to itself;
and a row's two fields come back from a json -> tt -> json round trip as
they were. Where the codecs refuse what the old code took (a settlement's
NaN or infinity, an empty `settle=` field, a `.tt` cash amount that is no
number), write a `.tt` token's numbers in their own spelling (1e5 as
100000) or keep what the old code cut (a typed amount past 10 digits split
by payment day), the oracle says so (the first review of step 6b(2))."""
import math
import random
import re
import unittest
from decimal import Decimal, InvalidOperation, localcontext

from taxjson.bin import taxjson_fx_cash as FX
from taxjson.lib import cash_legs
from taxjson.lib.tt_format import parse_tt_line, tx_to_tt_line
from taxjson.lib import corp_actions as CA
from taxjson.lib import futures as F
from taxjson.model.transaction import TaxTransaction
from taxjson.model import evidence as V
from _support.model_number_strings import NUMBERS, _key


# --- the code as it was (lib/corp_actions, bin/taxjson_fx_cash,
# bin/taxjson_convert_tt, lib/futures before step 6b(2)) --------------------

def old_corp_cash(*parts):
    return '; '.join(f"{float(a):.10g} {str(c).upper()}"
                     + (f" @{rest[0]}" if rest and rest[0] else '')
                     for a, c, *rest in parts
                     if a and abs(float(a)) >= 0.005 and c)


def old_corp_cash_parts(corp_cash):
    out = []
    for part in corp_cash.split(";"):
        bits = part.split()
        paid = ""
        if len(bits) == 3 and bits[2].startswith("@"):
            paid = bits.pop()[1:]
        if len(bits) != 2:
            continue
        try:
            amt = float(bits[0])
        except ValueError:
            continue
        out.append((bits[1].upper(), amt, paid))
    return out


def old_cash_by_day(cc):
    by_day = {}
    for c, a, paid in old_corp_cash_parts(cc):
        by_day.setdefault(paid, []).append(f"{a:.10g} {c}")
    return {paid: "; ".join(parts) for paid, parts in by_day.items()}


_OLD_CORP_CASH_RE = re.compile(r'^-?\d[\d.eE+-]*:[A-Z0-9]+(?::@\d{4}-\d{2}-\d{2})?$')


def old_cash_of_tt(val):
    if not all(_OLD_CORP_CASH_RE.match(p) for p in val.split(';')):
        return "error"
    return '; '.join(p.replace(':', ' ') for p in val.split(';'))


def old_cash_tt(val):
    return ';'.join(p.strip().replace(' ', ':') for p in str(val or '').split(';'))


_LEGS = ("LO", "LC", "SO", "SC")


def old_parse_settle_native(value):
    parts = str(value or "").split()
    if len(parts) not in (4, 5) or parts[0] not in _LEGS:
        return None
    try:
        money, pl = Decimal(parts[1]), Decimal(parts[2])
        rate = Decimal(parts[4]) if len(parts) == 5 else None
    except Exception:
        return None
    return parts[0], money, pl, parts[3], rate


def old_stamp(v, rate):
    """stamp_rate's new text, None where it left the field."""
    v = str(v or "")
    parts = v.split()
    if len(parts) == 4:
        return f"{v} {rate}"
    elif len(parts) == 5 and old_parse_settle_native(v) is not None:
        prior = Decimal(parts[4])
        composed = prior * Decimal(str(rate))
        return " ".join(parts[:4] + [str(composed)])
    return None


def old_part_text(leg, money, net, currency):
    return f"{leg} {money} {net} {(currency or '').upper() or '-'}" if leg else ""


def old_settle_tt(v):
    return ','.join(str(v or '').split())


def old_settle_of_tt(token):
    native = token.split(',')
    return ' '.join(native), old_parse_settle_native(' '.join(native))


def _outcome(fn, *args):
    """What a call gives, or the type of what it raised."""
    try:
        return fn(*args)
    except (ArithmeticError, ValueError) as e:
        return ("raised", type(e))


def _finite(t):
    """Whether the codec reads the old reader's facts too: every number
    finite and within a float's range, a rate above 0 (it refuses the
    rest: None; TJ-2841, TJ-2848, TJ-2849)."""
    if t is None:
        return True
    nums = (t[1], t[2]) + ((t[4],) if t[4] is not None else ())
    return (all(d.is_finite() and math.isfinite(float(d)) for d in nums)
            and (t[4] is None or t[4] > 0))


def _facts(f):
    """A SettleNative as the old reader's tuple, its Decimals as written."""
    return None if f is None else (f.leg, str(f.money), str(f.pl), f.currency,
                                   None if f.rate is None else str(f.rate))


def _old_facts(t):
    return None if t is None else (t[0], str(t[1]), str(t[2]), t[3],
                                   None if t[4] is None else str(t[4]))


CURRENCIES = ["USD", "usd", "CAD", "X1", "-", "EUR"]
PAIDS = ["@2025-03-20", "@", "@bad", "x", "@2024-12-31"]
AMOUNTS = NUMBERS + ["7.50", "12.34", "-3", "1e+10", "0.00005", "007"]


def _cash_fuzz(rng):
    parts = []
    for _ in range(rng.randrange(0, 4)):
        toks = [rng.choice(AMOUNTS), rng.choice(CURRENCIES)]
        if rng.random() < 0.4:
            toks.append(rng.choice(PAIDS))
        if rng.random() < 0.05:
            toks = toks[:1]
        parts.append(" ".join(toks))
    sep = rng.choice([";", "; ", " ; ", ";;"])
    return rng.choice(["", " "]) + sep.join(parts) + rng.choice(["", ";", " "])


def _settle_fuzz(rng):
    n = rng.choice([0, 3, 4, 4, 4, 5, 5, 5, 6])
    toks = [rng.choice(["LO", "LC", "SO", "SC", "XX", "lo", "S"])]
    toks += [rng.choice(NUMBERS + ["100.0", "-1E+3", "20000.0"]) for _ in range(2)]
    toks += [rng.choice(CURRENCIES), rng.choice(NUMBERS + ["1.35", "1"]), "x"]
    return rng.choice(["", " "]) + rng.choice([" ", "  "]).join(toks[:n]) + rng.choice(["", " "])


def _amount(rng):
    return rng.choice([rng.uniform(-1e4, 1e4), round(rng.uniform(0, 1e4), 2), 1 / rng.randrange(1, 300),
                       float(rng.randrange(0, 9)), 10 ** rng.uniform(-9, 14), math.inf, math.nan,
                       -0.0, rng.randrange(0, 50)])


class TestCorpCash(unittest.TestCase):
    def test_the_reader_is_unchanged(self):
        rng = random.Random(71)
        for _ in range(20_000):
            s = _cash_fuzz(rng)
            new = [(c.currency, float(c.amount), c.paid) for c in V.parse_corp_cash(s)]
            self.assertEqual(_key(new), _key(old_corp_cash_parts(s)), s)
            self.assertEqual(_key(cash_legs._corp_cash_parts(s)), _key(old_corp_cash_parts(s)), s)

    def test_the_writer_is_unchanged_and_reads_back(self):
        rng = random.Random(72)
        for _ in range(20_000):
            parts = []
            for _ in range(rng.randrange(1, 4)):
                part = (_amount(rng), rng.choice(["USD", "cad", "Eur", ""]))
                if rng.random() < 0.5:
                    part += (rng.choice(["2025-03-20", ""]),)
                parts.append(part)
            new = CA._corp_cash(*parts)
            finite = [p for p in parts if not isinstance(p[0], float) or math.isfinite(p[0])]
            self.assertEqual(new, old_corp_cash(*finite), parts)   # no inf (TJ-2851)
            self.assertEqual(V.format_corp_cash(V.parse_corp_cash(new)), new)

    def test_the_split_by_payment_day_keeps_the_amounts_as_read(self):
        # The days, their order and each day's cash as the same-day reader
        # reads it (the old split cut an amount to 10 digits, TJ-2845); the
        # writer's own fields split to the old bytes.
        rng = random.Random(73)
        for _ in range(20_000):
            s = _cash_fuzz(rng)
            new = FX._cash_by_day(s)
            self.assertEqual(list(new), list(old_cash_by_day(s)), s)
            for day, cash in new.items():
                got = [(c, a) for c, a, _p in old_corp_cash_parts(cash)]
                want = [(c, a) for c, a, p in old_corp_cash_parts(s) if p == day]
                self.assertEqual(_key(got), _key(want), s)
        for _ in range(5_000):
            parts = [(_amount(rng), rng.choice(["USD", "cad"]), rng.choice(["2025-03-20", ""]))
                     for _ in range(rng.randrange(1, 4))]
            cash = CA._corp_cash(*parts)
            self.assertEqual(FX._cash_by_day(cash), old_cash_by_day(cash), cash)
        self.assertEqual(FX._cash_by_day("123456789.12 USD @2025-03-20; 2 CAD"),
                         {"2025-03-20": "123456789.12 USD", "": "2 CAD"})
        # One whose digits need an exponent keeps them too (TJ-2850).
        self.assertEqual(FX._cash_by_day("1.2345678901234E+20 USD @2025-03-20"),
                         {"2025-03-20": "1.2345678901234E+20 USD"})

    def test_a_tt_amount_as_written_reads_back(self):
        # The .tt reader keeps an amount as written: "7.50" stays "7.50".
        for s in ("7.50 USD", "1.50 CAD @2024-03-05", "6 USD @2025-03-20", "12345678901 USD",
                  "-0.25 USD; 3 CAD @2025-01-02", ""):
            self.assertEqual(V.format_corp_cash(V.parse_corp_cash(s)), s)
        for s in ("1e5 USD", "0.00005 USD", "007 USD", "5 usd", "5 USD @", "x USD", "5"):
            self.assertNotEqual(V.format_corp_cash(V.parse_corp_cash(s)), s)
        # The .tt reader writes a typed amount as the formatter spells the
        # same number, so the row reads back; it refuses one that is no
        # number (fx-cash skipped it) or would lose digits (TJ-2843).
        for tok, row in (("1e5:USD", "100000 USD"), ("7.5e0:USD", "7.5 USD"),
                         ("07.5:USD", "7.5 USD"), ("0.00005:USD", "5e-05 USD"),
                         ("1.23456789012345e20:USD", "1.23456789012345E+20 USD"),
                         ("1.23456789012e-5:USD", "0.0000123456789012 USD"),
                         ("7.50:USD;12:CAD:@2025-06-05", "7.50 USD; 12 CAD @2025-06-05")):
            self.assertEqual(V.corp_cash_of_tt(tok), row)
            self.assertEqual(V.format_corp_cash(V.parse_corp_cash(row)), row)
            self.assertEqual(V.corp_cash_of_tt(V.corp_cash_tt(row)), row)
        for tok in ("1.2.3:USD", "5:USD;1.2.3:CAD", "1e999999999999999999999:USD",
                    "1E+999999999:USD"):
            with self.assertRaises(V.EvidenceError, msg=tok):
                V.corp_cash_of_tt(tok)

    def test_the_tt_token_is_unchanged(self):
        rng = random.Random(74)
        for _ in range(20_000):
            s = _cash_fuzz(rng)
            self.assertEqual(V.corp_cash_tt(s), old_cash_tt(s), s)
            tok = V.corp_cash_tt(s) if rng.random() < 0.5 else s.replace(" ", rng.choice([":", ""]))
            try:
                new = V.corp_cash_of_tt(tok)
            except V.EvidenceError:
                new = "error"
            old = old_cash_of_tt(tok)
            if new == "error":
                # Refused as the old reader refused it, or a part fx-cash
                # skipped, or one past a float (fx-cash read it as inf).
                self.assertTrue(old == "error" or len(old_corp_cash_parts(old)) != len(tok.split(';'))
                                or any(math.isinf(a) for _c, a, _p in old_corp_cash_parts(old)), tok)
                continue
            # The same numbers fx-cash read, in the formatter's spelling, stable
            # through the token.
            self.assertEqual(_key(old_corp_cash_parts(new)), _key(old_corp_cash_parts(old)), tok)
            self.assertEqual(V.format_corp_cash(V.parse_corp_cash(new)), new, tok)
            self.assertEqual(V.corp_cash_of_tt(V.corp_cash_tt(new)), new, tok)


class TestSettleNative(unittest.TestCase):
    def test_the_reader_is_unchanged(self):
        rng = random.Random(75)
        for _ in range(20_000):
            s = _settle_fuzz(rng)
            old = old_parse_settle_native(s)
            self.assertEqual(_facts(V.parse_settle_native(s)),
                             _old_facts(old) if _finite(old) else None, s)
        for s in (None, "", "LC 1e999999999999999999999 5 USD"):
            self.assertEqual(_facts(V.parse_settle_native(s)), _old_facts(old_parse_settle_native(s)), s)
        # A NaN or an infinity is no settlement's amount (TJ-2841).
        for s in ("SO 1 2 USD sNaN", "LO NaN 2 USD", "LC 1 -Infinity USD 1.2", "SC 1 2 USD inf",
                  "LO 1 2 USD 1E+999999999", "LO 1E+999999999 2 USD", "LO 1 2 USD 0", "LO 1 2 USD -1.2"):
            self.assertIsNone(V.parse_settle_native(s), s)
            self.assertIsNone(V.stamp_settle_rate(s, Decimal("1.2")) if len(s.split()) == 5 else None)

    def test_the_reader_does_not_follow_the_threads_context(self):
        # The old reader took a context that does not trap InvalidOperation
        # as it found it ("x" read as NaN); the codec refuses "x" in any.
        with localcontext() as ctx:
            ctx.traps[InvalidOperation] = False
            self.assertIsNone(V.parse_settle_native("LC x 5 USD"))
            self.assertEqual(V.parse_settle_native("LC 1 5 USD").money, Decimal(1))

    def test_the_parts_text_is_unchanged_and_reads_back(self):
        rng = random.Random(76)
        for _ in range(20_000):
            leg = rng.choice(list(_LEGS) + [""])
            money = Decimal(repr(_amount(rng))) * Decimal(rng.choice(["1", "0.3", "7"]))
            net = Decimal(str(round(rng.uniform(-1e5, 1e5), rng.randrange(0, 12))))
            cur = rng.choice(["USD", "usd", "", None])
            tx = TaxTransaction(action="BUYSELL", date="2025-01-02", symbol="F:QZXZ5.US",
                                quantity=1.0, currency=cur)
            part = F._part(tx, Decimal(1), Decimal(1), net, True, leg, money)
            self.assertEqual(part.settle_native, old_part_text(leg, money, net, cur))
            if leg and money.is_finite():
                self.assertEqual(V.format_settle_native(V.parse_settle_native(part.settle_native)),
                                 part.settle_native)

    def test_the_stamp_is_unchanged(self):
        rng = random.Random(77)
        for _ in range(20_000):
            v = _settle_fuzz(rng)
            rate = rng.choice([1, Decimal("1.35"), Decimal("0.8"), Decimal("1.2500")])
            want = _outcome(old_stamp, v, rate)
            if len(v.split()) == 5 and not _finite(old_parse_settle_native(v)):
                want = None         # a NaN or an infinity is not stamped (TJ-2841)
            self.assertEqual(_outcome(V.stamp_settle_rate, v, rate), want, v)
            tx = TaxTransaction(action="BUYSELL", date="2025-01-02", symbol="F:QZXZ5.US",
                                settle_native=v)
            F.stamp_rate(tx, rate)
            self.assertEqual(tx.settle_native, v if want is None else want, v)
        # A rate is written as its Decimal's str, so the stamp reads back
        # whatever number type it is given (TJ-2839).
        for rate in (8.5e-05, 1.35, 7, Decimal("8.5E-7")):
            text = V.stamp_settle_rate("LO 1 2 IDR", rate)
            self.assertEqual(V.format_settle_native(V.parse_settle_native(text)), text)
        self.assertEqual(V.stamp_settle_rate("LO 1 2 IDR", 8.5e-05), "LO 1 2 IDR 0.000085")

    def test_the_leg_reads_the_first_token(self):
        rng = random.Random(78)
        for _ in range(20_000):
            v = _settle_fuzz(rng)
            first = (v.split() or [""])[0]
            self.assertEqual(V.settle_leg(v), first if first in V.SETTLE_LEGS else "")
            tx = TaxTransaction(action="BUYSELL", date="2025-01-02", symbol="F:QZXZ5.US",
                                type=F.FUTURES_SETTLEMENT, settle_native=v)
            short = F.engine_pool_key(tx) != tx.symbol
            # The old key read the first character: a leg it knows only now
            # ("SX" or "S" was a short; TJ-2852).
            self.assertEqual(short, first in ("SO", "SC"), v)
            facts = V.parse_settle_native(v)
            if facts is not None:
                # The pool key and the cross-account re-average read one leg.
                self.assertEqual(short, facts.leg[0] == "S", v)
        self.assertEqual(V.settle_leg(""), "")
        self.assertEqual(V.settle_leg(None), "")
        # A settle_native " SO 100 0 USD" (an older release read a .tt
        # `settle=,SO,100,0,USD` so; the reader refuses it now): the short's
        # pool, as the re-average reads it (TJ-2837; the old key read the
        # first character, the long pool).
        tx = TaxTransaction(action="BUYSELL", date="2025-01-02", symbol="F:QZXZ5.US",
                            quantity=-1.0, type=F.FUTURES_SETTLEMENT, settle_native=" SO 100 0 USD")
        self.assertEqual(V.parse_settle_native(tx.settle_native).leg, "SO")
        self.assertEqual(F.engine_pool_key(tx), tx.symbol + F.SHORT_POOL_SUFFIX)

    def test_the_tt_token_is_unchanged(self):
        rng = random.Random(79)
        for _ in range(20_000):
            v = _settle_fuzz(rng)
            self.assertEqual(V.settle_native_tt(v), old_settle_tt(v), v)
            tok = rng.choice([old_settle_tt(v), "," + old_settle_tt(v), v.replace(" ", ",")])
            text, facts = V.settle_native_of_tt(tok)
            old_text, old_facts = old_settle_of_tt(tok)
            if '' in tok.split(',') or not _finite(old_facts):
                old_facts = None    # an empty field or a NaN refused (TJ-2840, TJ-2841)
            if old_facts is not None:   # the currency upper-cased (TJ-2849)
                old_facts = old_facts[:3] + (old_facts[3].upper(),) + old_facts[4:]
            # The facts in the formatter's spelling (the same numbers), the
            # value as written when they do not read.
            want_text = old_text if old_facts is None else V.format_settle_native(
                V.SettleNative(*old_facts))
            self.assertEqual((text, _facts(facts)), (want_text, _old_facts(old_facts)), tok)
        for tok in ("LO,,100,5,USD", ",SO,100,0,USD", "LC,100,5,USD,", "LO,1,2,USD,sNaN",
                    "LO,1,2,USD,0", "LO,1,2,USD,1E+999999999"):
            self.assertIsNone(V.settle_native_of_tt(tok)[1], tok)
        # The currency as the writer writes it: the re-average pools by it (TJ-2849).
        self.assertEqual(V.settle_native_of_tt("LO,100,5,usd,1.35")[0], "LO 100 5 USD 1.35")


class TestAPartOfNoUnits(unittest.TestCase):
    """A futures fill of 0 units is a part with no leg ("" settle_native):
    the US warning of a contract long in one broker account and short in
    another read its leg as the first token of " " and raised IndexError."""

    def _part(self, qty, account="b1"):
        return TaxTransaction(action="BUYSELL", date="2025-01-02", symbol="F:QZXZ5.US",
                              quantity=qty, type=F.FUTURES_SETTLEMENT, settle_native="",
                              source_account=account)

    def test_it_is_no_position(self):
        self.assertEqual(F.opposite_broker_positions([self._part(0.0)]), [])
        long_ = TaxTransaction(action="BUYSELL", date="2025-01-02", symbol="F:QZXZ5.US",
                               quantity=1.0, type=F.FUTURES_SETTLEMENT,
                               settle_native="LO 100 0 USD", source_account="b1")
        short = TaxTransaction(action="BUYSELL", date="2025-01-03", symbol="F:QZXZ5.US",
                               quantity=-1.0, type=F.FUTURES_SETTLEMENT,
                               settle_native="SO 100 0 USD", source_account="b2")
        self.assertEqual(F.opposite_broker_positions([long_, self._part(0.0), short]),
                         ["F:QZXZ5.US"])

    def test_a_us_conversion_of_a_zero_fill_runs(self):
        from taxjson.bin.taxjson_convert_currency import process_transactions, reset_fallback_tally
        reset_fallback_tally()
        rows = [TaxTransaction(action="BUYSELL", date="2024-01-02", symbol="F:QZXZ5.US", quantity=1.0,
                               price=100.0, gross_amount=-100.0, net_amount=-100.0, currency="USD",
                               id="a"),
                TaxTransaction(action="BUYSELL", date="2024-01-03", symbol="F:QZXZ5.US", quantity=0.0,
                               price=100.0, currency="USD", id="b")]
        out = process_transactions(rows, "USD", {}, Decimal(1), country="usa")
        # The buy's cost, written here as the cash paid out: its magnitude
        # is the position's money, as the engines read it (backlog TJ-5162).
        self.assertEqual([r.settle_native for r in out], ["LO 100.0 0 USD 1", ""])


class TestJsonTtJson(unittest.TestCase):
    """A row's corp_cash and settle_native come back from its .tt line as
    they were, for what the writers make."""

    def test_corp_cash(self):
        rng = random.Random(80)
        for _ in range(3_000):
            parts = [(rng.choice([rng.uniform(-1e4, 1e4), round(rng.uniform(0, 1e4), 2),
                                  1 / rng.randrange(1, 300), 10 ** rng.uniform(-2, 12)]),
                      rng.choice(["USD", "cad"]), rng.choice(["2025-03-20", ""]))
                     for _ in range(rng.randrange(1, 4))]
            cash = CA._corp_cash(*parts)
            if not cash:
                continue
            row = dict(action="BUYSELL", date="2025-01-02", date_settle="2025-01-02",
                       time="10:00:00", symbol="ZZQ.TO", quantity=1.0, currency="CAD", price=5.0,
                       gross_amount=-5.0, net_amount=-5.0, corp_event_id="e1", corp_cash=cash)
            back = parse_tt_line(tx_to_tt_line(row), "margin")
            self.assertEqual(back["corp_cash"], cash)

    def test_settle_native(self):
        rng = random.Random(81)
        for _ in range(3_000):
            qty = rng.choice([1.0, -1.0, 2.0, -3.0])
            leg = rng.choice(["LO", "SC"] if qty > 0 else ["LC", "SO"])
            money = Decimal(str(round(rng.uniform(0, 1e5), 2))) * Decimal(rng.choice(["1", "0.3"]))
            pl = Decimal(str(round(rng.uniform(-1e4, 1e4), rng.randrange(0, 6))))
            text = V.format_settle_native(V.settle_native(leg, money, pl, "usd"))
            text = V.stamp_settle_rate(text, rng.choice([1, Decimal("1.35")]))
            row = dict(action="BUYSELL", date="2025-01-02", date_settle="2025-01-02",
                       time="10:00:00", symbol="F:QZXZ5.US", quantity=qty, currency="USD",
                       price=0.0, gross_amount=0.0, net_amount=float(pl),
                       type=F.FUTURES_SETTLEMENT, settle_native=text, multiplier=5.0)
            back = parse_tt_line(tx_to_tt_line(row), "margin")
            self.assertEqual(back["settle_native"], text)


class TestATtLineReadsBack(unittest.TestCase):
    """A .tt line's two fields, as the loader holds them, read back through
    their codecs to the text the row carries, whatever the spelling of the
    line's numbers (the .tt reader writes them as the codecs spell them,
    TJ-2843)."""

    def test_the_rows_fields_read_back(self):
        from taxjson.lib.tx_load import coerce_transaction_row
        for line in ("BUYSELL 2025-01-02 10:00:00 ZZQ.TO 1 CAD 5 5 0 corp=e1 cash=1e5:USD;07.5:CAD",
                     "BUYSELL 2025-01-02 10:00:00 F:QZXZ5.US 1 USD 0 0 0 x5 "
                     "type=futures_settlement settle=LO,1e5,0,USD"):
            row = coerce_transaction_row(parse_tt_line(line, "margin"), 0, "t")
            self.assertTrue(row.corp_cash or row.settle_native, line)
            self.assertEqual(V.format_corp_cash(V.parse_corp_cash(row.corp_cash)), row.corp_cash)
            facts = V.parse_settle_native(row.settle_native)
            self.assertEqual("" if facts is None else V.format_settle_native(facts),
                             row.settle_native)


class TestTheRecords(unittest.TestCase):
    def test_frozen_and_slotted(self):
        for rec in (V.CorpCash(Decimal(1), "USD"), V.parse_settle_native("LO 1 2 USD")):
            self.assertFalse(hasattr(rec, "__dict__"))
            with self.assertRaises(AttributeError):
                rec.currency = "CAD"
        self.assertEqual(V.parse_corp_cash("7.50 usd @2025-03-20"),
                         (V.CorpCash(Decimal("7.50"), "USD", "2025-03-20"),))
        self.assertEqual(V.parse_settle_native("SC 100.0 -5 - 1.35"),
                         V.SettleNative("SC", Decimal("100.0"), Decimal(-5), "-", Decimal("1.35")))


if __name__ == "__main__":
    unittest.main()
