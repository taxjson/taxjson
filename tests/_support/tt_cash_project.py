"""Shared test helpers: a .tt project with cash and instalments.

Shared from: test_fix_filing_b
"""


def _toml(root, year=2025, extra_settings="", accounts=None):
    accounts = accounts or [("margin", "taxable", "")]
    t = (f'[settings]\nyear = {year}\ncountry = "canada"\n'
         f'base_currency = "CAD"\n{extra_settings}')
    for n, ty, more in accounts:
        t += f'[accounts.{n}]\ntype = "{ty}"\n{more}'
    (root / "taxjson.toml").write_text(t)


_TT_MARGIN = ("BUYSELL 2025-01-10 10:00:00 ZZZ.TO 100 CAD 30.00 -3000.00 0.00\n"
              "BUYSELL 2025-03-10 10:00:00 ZZZ.TO -100 CAD 40.00 4000.00 0.00\n")
_TT_CASH = ("BUYSELL 2025-01-10 10:00:00 YYY.TO 100 CAD 30.00 -3000.00 0.00\n"
            "BUYSELL 2025-06-10 10:00:00 YYY.TO -100 CAD 50.00 5000.00 0.00\n")


def _tt_project(root, year=2025, cash=_TT_CASH, extra_settings="",
                instalments=False):
    settings = (f"source_currencies = []\noption_grant_timing_since = 2025\n"
                f'province = "ON"\n{extra_settings}')
    _toml(root, year, settings, [("margin", "taxable", ""),
                                 ("cash", "taxable", "")])
    if instalments:
        with (root / "taxjson.toml").open("a") as f:
            f.write('[instalments]\nbasis = "current_year"\n')
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "inputs" / "cash").mkdir(parents=True)
    (root / "inputs" / "margin" / "m.tt").write_text(_TT_MARGIN)
    (root / "inputs" / "cash" / "c.tt").write_text(cash)
