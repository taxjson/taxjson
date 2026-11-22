"""Medium-round regression tests (area: misc).

Pins the tax tables, AMT arithmetic and other amounts the mutation audit
found unpinned (G1-*), plus the misc fixes. All data is synthetic.

Formerly: test_fix_misc"""
import unittest


class TestFxCashSkipsShelteredAccounts(unittest.TestCase):
    """G1-2: a sheltered account's USD round trip stays out of s.39.

    Formerly: test_fix_misc.TestFxCashSkipsShelteredAccounts"""

    def test_sheltered_usd_is_ignored(self):
        import json
        import tempfile
        from pathlib import Path
        from taxjson.pipeline.run import _fx_cash_doc

        def tx(date, net, qty, account):
            return dict(action="BUYSELL", date=date, date_settle=date, time="10:00:00",
                        symbol="AAA.US", quantity=qty, currency="USD",
                        net_amount=net, account=account)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "work"
            work.mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
                '[accounts.margin]\ntype = "taxable"\n'
                '[accounts.rrsp]\ntype = "sheltered"\n'
                '[accounts.tfsa]\ntype = "sheltered"\n')
            (work / "margin_raw.json").write_text(json.dumps({"transactions": [
                tx("2026-01-10", 10000.0, -100, "margin"),
                tx("2026-02-10", 10000.0, 50, "margin")]}))
            # The same round trip, bigger, in each registered account.
            for acct in ("rrsp", "tfsa"):
                (work / f"{acct}_raw.json").write_text(json.dumps({"transactions": [
                    tx("2026-01-10", 50000.0, -500, acct),
                    tx("2026-02-10", 50000.0, 250, acct)]}))
            (work / "to_base.csv").write_text(
                "2026-01-10 12:00:00 USD CAD 1.30\n"
                "2026-02-10 12:00:00 USD CAD 1.40\n")
            ledger, verdict, base, year, country = _fx_cash_doc(root, work)
        self.assertAlmostEqual(ledger["net_gain"], 1000.0, places=2)
        self.assertAlmostEqual(ledger["per_currency"]["USD"]["acquired"], 10000.0, places=2)
        self.assertAlmostEqual(verdict["reportable"], 800.0, places=2)
        self.assertEqual({e["account"] for e in ledger["events"]}, {"margin"})


if __name__ == "__main__":
    unittest.main()
