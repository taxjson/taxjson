"""In-kind moves between a taxable and a registered account (owner-
approved feature; tax-logic CA-INKIND-01..06 / US-INKIND-01..03).

A taxable account's transfer-out paired with a registered account's
transfer-in of the same security and quantity (or the reverse) is an
in-kind contribution (withdrawal): Canada books it in the taxable account
as a sale (purchase) at fair market value — a contribution's loss is
denied for good (s.40(2)(g)(iv)) and the plan's purchase counts for
s.54; the US warns about a contribution (cash only) and books a
distribution at fair market value.

Every fixture is synthetic: invented tickers, amounts and account ids.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent
from tax_rules.dual import cli, settings_for


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


# ---------------------------------------------------------------- fixtures

def _ib(trades=(), xfers=(), *, cur="CAD", exch="TSE", sym="QZK",
        acct="U5550001"):                                       # pii-ok
    """A synthetic IB activity statement: `trades` (date, qty, price),
    `xfers` (date, qty, market value)."""
    head = ('Statement,Header,Field Name,Field Value\n'
            'Statement,Data,BrokerName,Interactive Brokers\n'
            'Statement,Data,Title,Activity Statement\n'
            'Statement,Data,Period,"January 1, 2025 - December 31, 2025"\n'
            'Financial Instrument Information,Header,Asset Category,'
            'Symbol,Description,Conid,Security ID,Underlying,Listing Exch,'
            'Multiplier,Expiry,Delivery Month,Type,Strike,Code\n'
            f'Financial Instrument Information,Data,Stocks,{sym},'
            f'"{sym} SAMPLE CORP",999000101,,,{exch},1,,,COMMON,,\n')
    out = head
    if trades:
        out += ('Trades,Header,DataDiscriminator,Asset Category,Currency,'
                'Account,Symbol,Date/Time,Quantity,T. Price,C. Price,'
                'Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n')
        for d, q, p in trades:
            out += (f'Trades,Data,Order,Stocks,{cur},{acct},{sym},'
                    f'"{d}, 10:00:00",{q},{p},0,{-q * p},0,0,0,0,'
                    f'{"O" if q > 0 else "C"}\n')
    if xfers:
        out += ('Transfers,Header,Asset Category,Currency,Symbol,Date,Type,'
                'Direction,Xfer Company,Xfer Account,Qty,Xfer Price,'
                'Market Value,Realized P/L,Cash Amount,Code\n')
        for d, q, mv in xfers:
            out += (f'Transfers,Data,Stocks,{cur},{sym},{d},ACATS,'
                    f'{"Out" if q < 0 else "In"},Other Broker,5550009,'  # pii-ok
                    f'{q},0,{mv if q > 0 else -mv},0,0,\n')
    return out


_QT = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Account #,Activity Type,Account Type\n")


def _qt(date, action, qty, price=0.0, *, cur="CAD", acct="55500002",
        act="Trades", sym="QZK", desc="QZK SAMPLE CORP"):    # pii-ok
    net = -qty * price
    return (f"{date} 09:30:00 AM,{date} 12:00:00 AM,{action},{sym},{desc},"
            f"{qty},{price:.2f},{abs(qty) * price:.2f},0.00,{net:.2f},"
            f"{cur},{acct},{act},Individual\n")


def _xfer_qt(date, qty, **kw):
    return _qt(date, "TF6", qty, act="Transfers",
               desc="QZK SAMPLE CORP TRANSFER", **kw)


def _project(td, files, *, accounts=None, country="canada", name="p"):
    root = Path(td) / name
    root.mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        settings_for(country, year=2025, source_currencies=[])
        + (accounts or ('[accounts.margin]\ntype = "taxable"\n'
                        '[accounts.rrsp]\ntype = "sheltered"\n'
                        'transfers = true\n')))
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def _run(test, root, *args, ok=True):
    r = cli(root, "run", "--no-input", *args)
    if ok:
        test.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
    return r


def _flat(r):
    return " ".join((r.stdout + r.stderr).split())


def _filing(root):
    return json.loads(cli(root, "sum", "--json").stdout)["filing"]["totals"]


def _contribution(td, mv, *, trades=((("2025-01-10", 100, 10.0)),),
                  extra_tt=None, name="p"):
    """margin (IB) holds QZK.TO and moves 100 out on 03-14 (IB market
    value `mv`); rrsp (Questrade) receives 100 on 03-17."""
    files = {"inputs/margin/ib.csv": _ib(trades,
                                         [("2025-03-14", -100, mv)]),
             "inputs/rrsp/questrade.csv": _QT
             + _xfer_qt("2025-03-17", 100)}
    if extra_tt:
        files["inputs/margin/inkind.tt"] = extra_tt
    return _project(td, files, name=name)


# ---------------------------------------------------------------- Canada

class TestCanadaContribution(unittest.TestCase):

    @rule("CA-INKIND-01", "CA-INKIND-02", "CA-INKIND-06")
    def test_gain_is_booked_at_the_ib_market_value(self):
        with tempfile.TemporaryDirectory() as td:
            root = _contribution(td, 1500)
            r = _run(self, root)
            flat = _flat(r)
            self.assertEqual(flat.count("in-kind move(s) between your "
                                        "taxable and registered accounts"),
                             1, flat)
            self.assertIn("contribution margin → rrsp: 100 QZK.TO on "
                          "2025-03-14 at 1,500.00 CAD (Interactive Brokers "
                          "market value on margin's transfer row): gain "
                          "500.00 CAD", flat)
            t = _filing(root)
            self.assertEqual((t["proceeds"], t["acb"], t["gain"]),
                             (1500.0, 1000.0, 500.0))
            self.assertEqual(t["denied_contribution"], 0.0)
            # The taxable books no longer hold the shares (no short, no
            # holdings left in margin).
            hold = (root / "reports" / "margin_holdings.toml").read_text()
            self.assertNotIn("QZK", hold)
            # `taxjson transfers` names both legs.
            j = json.loads(cli(root, "transfers", "--json").stdout)
            self.assertEqual(sorted({t["arrival"] for t in j["transfers"]}),
                             ["in-kind contribution"])

    @rule("CA-INKIND-03")
    def test_loss_is_denied_for_good_and_never_added_to_an_acb(self):
        with tempfile.TemporaryDirectory() as td:
            # A repurchase in June and its sale in August: their gain is
            # on their own cost (500), not bumped by the denied loss.
            root = _contribution(td, 600, trades=(
                ("2025-01-10", 100, 10.0), ("2025-06-02", 100, 5.0),
                ("2025-08-01", -100, 7.0)))
            r = _run(self, root)
            flat = _flat(r)
            self.assertIn("at 600.00 CAD (Interactive Brokers market value "
                          "on margin's transfer row): loss 400.00 CAD "
                          "DENIED for good (s.40(2)(g)(iv)) — no ACB "
                          "addition", flat)
            t = _filing(root)
            self.assertEqual(t["gain"], 200.0)
            self.assertEqual(t["denied"], 0.0)          # not superficial
            self.assertEqual(t["permanently_denied"], 0.0)
            self.assertEqual(t["denied_contribution"], 400.0)
            s = " ".join(cli(root, "sum").stdout.split())
            self.assertIn("Denied: contribution to a registered plan — "
                          "400.00", s)
            fx = json.loads(cli(root, "form-export", "--json").stdout)
            row = [x for x in fx["rows"] if x["symbol"] == "QZK.TO"][0]
            self.assertEqual(row["denied_contribution"], 400.0)
            self.assertIn("s.40(2)(g)(iv)", row["notes"])

    @rule("CA-INKIND-04")
    def test_a_loss_ten_days_before_is_superficial_and_lost(self):
        with tempfile.TemporaryDirectory() as td:
            # 200 bought; 100 sold at a loss on 03-04; the other 100 go
            # into the RRSP on 03-14/03-17 — the plan's purchase replaces
            # the loss (default transfer policy: a plain transfer-in
            # would not).
            root = _contribution(td, 600, trades=(
                ("2025-01-10", 200, 10.0), ("2025-03-04", -100, 6.0)))
            _run(self, root)
            t = _filing(root)
            self.assertEqual(t["gain"], 0.0)
            self.assertEqual(t["denied"], 400.0)
            self.assertEqual(t["permanently_denied"], 400.0)
            self.assertEqual(t["denied_contribution"], 400.0)

    @rule("CA-INKIND-06")
    def test_an_inkind_line_wins(self):
        with tempfile.TemporaryDirectory() as td:
            root = _contribution(
                td, 1500,
                extra_tt="INKIND 2025-03-14 QZK.TO -100 CAD 12  # FMV\n")
            r = _run(self, root)
            self.assertIn("at 1,200.00 CAD (INKIND line inkind.tt:1): gain "
                          "200.00 CAD", _flat(r))
            self.assertEqual(_filing(root)["gain"], 200.0)

    @rule("CA-INKIND-01")
    def test_a_move_between_two_taxable_accounts_is_not_in_kind(self):
        with tempfile.TemporaryDirectory() as td:
            # margin -> rrsp and margin -> cash on the same day: the
            # same-quantity cash leg is closer, so it is your own move.
            files = {"inputs/margin/ib.csv": _ib(
                        [("2025-01-10", 100, 10.0)],
                        [("2025-03-14", -100, 1500)]),
                     "inputs/cash/questrade.csv": _QT
                     + _xfer_qt("2025-03-14", 100, acct="55500003"),  # pii-ok
                     "inputs/rrsp/questrade.csv": _QT
                     + _xfer_qt("2025-03-20", 100)}
            root = _project(td, files, accounts=(
                '[accounts.margin]\ntype = "taxable"\n'
                '[accounts.cash]\ntype = "taxable"\n'
                '[accounts.rrsp]\ntype = "sheltered"\ntransfers = true\n'))
            r = _run(self, root)
            self.assertNotIn("in-kind", _flat(r))
            self.assertFalse((root / "work" / "in_kind.json").exists())


class TestCanadaDeclaredMove(unittest.TestCase):

    @rule("CA-INKIND-01", "CA-INKIND-04")
    def test_a_plan_outside_the_project_is_declared_by_its_line(self):
        with tempfile.TemporaryDirectory() as td:
            # One taxable account; its 03-14 transfer-out went to a TFSA
            # at another broker, declared by the INKIND line. The plan's
            # purchase still makes the 03-04 loss superficial.
            files = {"inputs/margin/ib.csv": _ib(
                        [("2025-01-10", 200, 10.0),
                         ("2025-03-04", -100, 6.0)],
                        [("2025-03-14", -100, 600)]),
                     "inputs/margin/inkind.tt":
                        "INKIND 2025-03-14 QZK.TO -100 CAD 6 plan=tfsa\n"}
            root = _project(td, files,
                            accounts='[accounts.margin]\ntype = "taxable"\n')
            r = _run(self, root)
            self.assertIn("contribution margin → a TFSA plan outside the "
                          "project: 100 QZK.TO on 2025-03-14 at 600.00 CAD "
                          "(INKIND line inkind.tt:1): loss 400.00 CAD "
                          "DENIED for good", _flat(r))
            t = _filing(root)
            self.assertEqual((t["gain"], t["permanently_denied"],
                              t["denied_contribution"]),
                             (0.0, 400.0, 400.0))


class TestCanadaWithdrawal(unittest.TestCase):

    def _root(self, td, plan):
        files = {"inputs/" + plan + "/questrade.csv": _QT
                 + _qt("2025-01-06", "Buy", 50, 8.0)
                 + _xfer_qt("2025-05-01", -50),
                 "inputs/margin/ib.csv": _ib(
                     [("2025-07-02", -50, 20.0)],
                     [("2025-05-02", 50, 750)])}
        return _project(td, files, accounts=(
            '[accounts.margin]\ntype = "taxable"\n'
            f'[accounts.{plan}]\ntype = "sheltered"\ntransfers = true\n'))

    @rule("CA-INKIND-05")
    def test_rrsp_withdrawal_costs_its_fair_market_value(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td, "rrsp")
            r = _run(self, root)
            flat = _flat(r)
            self.assertIn("withdrawal rrsp → margin: 50 QZK.TO on "
                          "2025-05-02 at 750.00 CAD", flat)
            self.assertIn("the shares' ACB; that value is income on your "
                          "T4RSP (not booked)", flat)
            # Sold at 1000: the gain is on the 750 cost, not a short.
            t = _filing(root)
            self.assertEqual((t["proceeds"], t["acb"], t["gain"]),
                             (1000.0, 750.0, 250.0))
            self.assertNotIn("no cost", flat)
            self.assertNotIn("Short position", flat)

    @rule("CA-INKIND-05")
    def test_tfsa_withdrawal_is_not_taxed(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td, "tfsa")
            r = _run(self, root)
            self.assertIn("the shares' ACB; no tax on the withdrawal",
                          _flat(r))
            self.assertEqual(_filing(root)["gain"], 250.0)


class TestCanadaYahooValue(unittest.TestCase):
    """No market value on either row (Questrade both sides): Yahoo's
    close, here from the close cache (TAXJSON_OFFLINE is set by `cli`)."""

    def _root(self, td):
        files = {"inputs/margin/questrade.csv": _QT
                 + _qt("2025-01-06", "Buy", 100, 10.0, acct="55500001")  # pii-ok
                 + _xfer_qt("2025-03-14", -100, acct="55500001"),        # pii-ok
                 "inputs/rrsp/questrade.csv": _QT
                 + _xfer_qt("2025-03-14", 100)}
        return _project(td, files)

    @rule("CA-INKIND-06")
    def test_cached_close_is_an_estimate(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td)
            (root / "work").mkdir()
            (root / "work" / ".close_cache.json").write_text(json.dumps({
                "QZK.TO@2025-03-14": {"price": 13.0, "day": "2025-03-14",
                                      "currency": "CAD",
                                      "asof": "2025-03-20"}}))
            r = _run(self, root)
            self.assertIn("at 1,300.00 CAD (Yahoo close 2025-03-14, "
                          "ESTIMATED: split-adjusted, from the close "
                          "cache): gain 300.00 CAD", _flat(r))
            self.assertEqual(_filing(root)["gain"], 300.0)

    @rule("CA-INKIND-06")
    def test_offline_with_no_cached_close_stops_and_names_the_line(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td)
            r = _run(self, root, ok=False)
            self.assertEqual(r.returncode, 1)
            flat = _flat(r)
            self.assertIn("cannot be valued: TAXJSON_OFFLINE is set", flat)
            self.assertIn("INKIND 2025-03-14 QZK.TO -100 CAD <price per "
                          "share>", flat)
            # The named line is the fix.
            (root / "inputs" / "margin" / "v.tt").write_text(
                "INKIND 2025-03-14 QZK.TO -100 CAD 11\n")
            _run(self, root)
            self.assertEqual(_filing(root)["gain"], 100.0)


# ---------------------------------------------------- Canada and the USA

class TestContributionByCountry(unittest.TestCase):
    """The same move under each country: Canada books the sale at fair
    market value; a US retirement account takes cash only — warned, not
    booked."""

    def _root(self, td, country):
        cur, exch = (("CAD", "TSE") if country == "canada"
                     else ("USD", "NYSE"))
        files = {"inputs/margin/ib.csv": _ib(
                     [("2025-01-10", 100, 10.0)],
                     [("2025-03-14", -100, 1500)], cur=cur, exch=exch),
                 "inputs/plan/ib.csv": _ib(
                     (), [("2025-03-14", 100, 1500)], cur=cur, exch=exch,
                     acct="U5550002")}                          # pii-ok
        return _project(td, files, country=country, name=country,
                        accounts=('[accounts.margin]\ntype = "taxable"\n'
                                  '[accounts.plan]\ntype = "sheltered"\n'
                                  'transfers = true\n'))

    @rule("CA-INKIND-02")
    @rule_absent("CA-INKIND-02", country="usa")
    @rule("US-INKIND-01")
    @rule_absent("US-INKIND-01", country="canada")
    def test_contribution(self):
        with tempfile.TemporaryDirectory() as td:
            ca = self._root(td, "canada")
            r = _run(self, ca)
            self.assertIn("contribution margin → plan: 100 QZK.TO on "
                          "2025-03-14 at 1,500.00 CAD", _flat(r))
            self.assertIn("the plan is not named", _flat(r))
            self.assertEqual(_filing(ca)["gain"], 500.0)
            us = self._root(td, "usa")
            r = _run(self, us)
            flat = _flat(r)
            self.assertIn("contribution margin → plan: 100 QZK.US on "
                          "2025-03-14: NOT booked — an IRA, Roth, 401(k) "
                          "or HSA takes contributions in cash only", flat)
            # Not booked: no sale, the shares stay in margin's books.
            self.assertEqual(_filing(us)["gain"], 0.0)
            self.assertIn("QZK", (us / "reports" / "margin_holdings.toml")
                          .read_text())
            # --strict stops on it.
            r = cli(us, "run", "--no-input", "--strict")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("in-kind move(s) or INKIND line(s) not booked",
                          _flat(r))


class TestUsDistribution(unittest.TestCase):

    @rule("US-INKIND-02", "US-INKIND-03")
    def test_ira_distribution_in_kind_has_its_fair_value_basis(self):
        with tempfile.TemporaryDirectory() as td:
            files = {"inputs/ira/ib.csv": _ib(
                         [("2025-01-06", 50, 8.0)],
                         [("2025-05-01", -50, 750)], cur="USD",
                         exch="NYSE", acct="U5550002"),           # pii-ok
                     "inputs/margin/ib.csv": _ib(
                         [("2025-07-02", -50, 20.0)],
                         [("2025-05-01", 50, 750)], cur="USD",
                         exch="NYSE")}
            root = _project(td, files, country="usa", accounts=(
                '[accounts.margin]\ntype = "taxable"\n'
                '[accounts.ira]\ntype = "sheltered"\ntransfers = true\n'))
            r = _run(self, root)
            flat = _flat(r)
            self.assertIn("withdrawal ira → margin: 50 QZK.US on "
                          "2025-05-01 at 750.00 USD (Interactive Brokers "
                          "market value on margin's transfer row): the "
                          "shares' basis; the taxable amount is on your "
                          "Form 1099-R (not booked)", flat)
            t = _filing(root)
            self.assertEqual((t["proceeds"], t["cost"], t["gain"]),
                             (1000.0, 750.0, 250.0))


# ---------------------------------------------------------------- units

class TestPairing(unittest.TestCase):

    def _leg(self, acct, qty, date, plan=""):
        from taxjson.lib.in_kind import Leg
        return Leg(account=acct, broker="ib", key="QZK.TO", plan=plan,
                   row={"action": "TRANSFER", "symbol": "QZK.TO",
                        "quantity": qty, "date": date})

    @rule("CA-INKIND-01")
    def test_closest_leg_of_the_same_quantity_wins(self):
        from taxjson.lib import in_kind as IK
        legs = [self._leg("margin", -100, "2025-03-14"),
                self._leg("rrsp", 100, "2025-03-16", "rrsp"),
                self._leg("tfsa", 100, "2025-03-25", "tfsa"),
                self._leg("rrsp", -40, "2025-04-01", "rrsp"),
                self._leg("margin", 40, "2025-04-20")]      # 19 days
        moves = IK.pair(legs)
        self.assertEqual([(m.kind, m.registered.account) for m in moves],
                         [("contribution", "rrsp")])

    @rule("CA-INKIND-01")
    def test_a_journal_inside_each_account_is_not_a_move(self):
        # ticker.map joins two listings: each account journals its own
        # units out of one and into the other on the same day. The two
        # accounts' legs must not pair with each other.
        legs = [self._leg("margin", -300, "2025-09-25"),
                self._leg("margin", 300, "2025-09-25"),
                self._leg("rrsp", -300, "2025-09-25", "rrsp"),
                self._leg("rrsp", 300, "2025-09-25", "rrsp")]
        from taxjson.lib import in_kind as IK
        self.assertEqual(IK.pair(legs), [])

    @rule("CA-INKIND-01")
    def test_inkind_line_declares_a_move_to_a_plan_outside(self):
        from taxjson.lib import in_kind as IK
        legs = [self._leg("margin", -100, "2025-03-14")]
        moves = IK.pair(legs)
        self.assertEqual(moves, [])
        probs = IK.apply_lines(
            moves, [("margin", {"date": "2025-03-15", "symbol": "QZK.TO",
                                "quantity": -100.0, "currency": "CAD",
                                "total": 900.0, "plan": "tfsa",
                                "source": "a.tt:1"}),
                    ("margin", {"date": "2025-06-15", "symbol": "QZK.TO",
                                "quantity": 10.0, "currency": "CAD",
                                "total": 90.0, "plan": "",
                                "source": "a.tt:2"})], legs)
        self.assertEqual(len(moves), 1)
        m = moves[0]
        self.assertEqual((m.kind, m.plan, m.fmv, m.date, m.registered),
                         ("contribution", "tfsa", 900.0, "2025-03-15",
                          None))
        self.assertEqual(len(probs), 1)
        self.assertIn("a.tt:2 matches no transfer row of margin", probs[0])
        rows = IK.booked_rows(moves, "canada")["margin"]
        self.assertEqual((rows[0]["quantity"], rows[0]["net_amount"],
                          rows[0]["type"]),
                         (-100.0, 900.0, "in_kind_contribution"))


class TestInkindLine(unittest.TestCase):

    @rule("CA-INKIND-06")
    def test_line_forms(self):
        from taxjson.bin.taxjson_convert_tt import parse_inkind_line as p
        self.assertIsNone(p("BUYSELL 2025-01-01 09:30:00 A.TO 1 CAD 1 1"))
        ln = p("INKIND 2025-03-14 qzk.to -100 cad 0 1250.50 plan=rrsp")
        self.assertEqual((ln["symbol"], ln["quantity"], ln["currency"],
                          ln["total"], ln["plan"]),
                         ("QZK.TO", -100.0, "CAD", 1250.5, "rrsp"))
        self.assertEqual(p("INKIND 2025-03-14 QZK.TO 10 CAD 12.5")["total"],
                         125.0)
        for bad in ("INKIND 2025-03-14 09:30:00 QZK.TO -1 CAD 1",
                    "INKIND 2025-03-14 QZK.TO 0 CAD 1",
                    "INKIND 2025-03-14 QZK.TO -1 CAD 0",
                    "INKIND 2025-03-14 QZK.TO -10 CAD 12 500",
                    "INKIND 2025-03-14 QZK.TO -1 CAD 1 plan=margin",
                    "INKIND 2025-13-14 QZK.TO -1 CAD 1"):
            with self.assertRaises(ValueError, msg=bad):
                p(bad)

    @rule("CA-INKIND-06")
    def test_the_books_never_see_the_line(self):
        from taxjson.bin.taxjson_convert_tt import tt_to_json
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "a.tt"
            f.write_text("INKIND 2025-03-14 QZK.TO -100 CAD 12\n"
                         "BUYSELL 2025-01-10 09:30:00 QZK.TO 100 CAD 10 "
                         "1000 0\n")
            rows = tt_to_json(f, "margin")["transactions"]
            self.assertEqual([r["action"] for r in rows], ["BUYSELL"])


class TestCloseLookup(unittest.TestCase):

    @rule("CA-INKIND-06")
    def test_last_close_on_or_before_the_day_and_cached(self):
        from taxjson.lib import price_chain as PC
        calls = []

        def fetch(pairs, start, end):
            calls.append((pairs, start, end))
            return {"QZK.TO": {"2025-03-13": 12.0, "2025-03-14": 12.5,
                               "2025-03-17": 99.0}}
        with tempfile.TemporaryDirectory() as td:
            cp = Path(td) / ".close_cache.json"
            c = PC.close_on("QZK.TO", "QZK.TO", "2025-03-16",
                            cache_path=cp, fetchers=[fetch])
            self.assertEqual((c.price, c.day, c.currency),
                             (12.5, "2025-03-14", "CAD"))
            self.assertEqual(calls[0][1:], ("2025-03-09", "2025-03-16"))
            # Served from the cache next time, even offline.
            c = PC.close_on("QZK.TO", "QZK.TO", "2025-03-16",
                            cache_path=cp, offline=True)
            self.assertEqual((c.price, c.source), (12.5, "cache"))
            with self.assertRaises(PC.OfflineCloseMissing):
                PC.close_on("QZK.TO", "QZK.TO", "2025-04-16",
                            cache_path=cp, offline=True)


if __name__ == "__main__":
    unittest.main()
