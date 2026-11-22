"""Shared test helpers: a year of the T1A split.

Shared from: test_carry_back_grid
"""
import json

from _support.years_carryover_ledger import _CLAIM, _GAIN_2022, _TOML, _cli


# The books' net for the year, as the sale price that makes it (50 at 10.00).
BOOKS = {100.0: "12.00 600.00", -10.0: "9.80 490.00",
         -100.0: "8.00 400.00", -200.0: "6.00 300.00"}


class _Year:
    def __init__(self, top, books, claim=_CLAIM):
        margin = top / "inputs" / "margin"
        margin.mkdir(parents=True)
        (margin / "t.tt").write_text(
            _GAIN_2022
            + "BUYSELL 2023-02-01 09:30:00 ZZQB.TO 50 CAD 10.00 -500.00 0.00\n"
            + f"BUYSELL 2023-08-01 09:30:00 ZZQB.TO -50 CAD {BOOKS[books]} 0.00\n")
        self.top, self.y = top, top / "2023"
        self.y.mkdir()
        (self.y / "taxjson.toml").write_text(_TOML + claim)
        self.run(self.y, "run", "--no-input")

    @staticmethod
    def run(where, *args):
        r = _cli(where, *args)
        if r.returncode:
            raise AssertionError(f"{args}: {r.stdout[-800:]}{r.stderr[-800:]}")
        return r

    def close(self, filed=None):
        args = ["close-year", "--yes", "--force"]
        if filed is not None:
            csv = self.top / "filed.csv"
            csv.write_text("symbol,date,qty,proceeds,cost,gain\n"
                           f"ZZQF,2023-06-02,10,900,{900 - filed:.2f},{filed:.2f}\n")
            args += ["--filed-dispositions", csv]
        self.run(self.y, *args)

    def lock(self):
        return json.loads((self.y / "filed" / "2023.json").read_text()
                          )["carryforwards"]["net_capital_loss"]

    def row(self):
        doc = json.loads(self.run(self.y, "carryover", "--json").stdout)
        return next(x for x in doc["rows"] if x["year"] == 2023)

    def estimate(self):
        return json.loads(self.run(self.y, "estimate", "--json").stdout)["estimate"]

    def next_year(self):
        y = self.top / "2024"
        if not y.exists():
            y.mkdir()
            (y / "taxjson.toml").write_text(
                _TOML.replace("year = 2023", "year = 2024").replace(
                    "[accounts.margin]", 'prior_year_record = "../2023/filed/'
                    '2023.json"\n\n[accounts.margin]') + _CLAIM)
            self.run(y, "run", "--no-input")
        return y
