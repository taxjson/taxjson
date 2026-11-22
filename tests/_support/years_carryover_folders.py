"""Shared test helpers: year folders and rows for tjs carryover.

Shared from: test_fix_tj1495_row
"""
import shutil

from _support.years_carryover_ledger import _TOML, _cli


def _nets(**by_year):
    return {int(y[1:]): {"net": v, "st": 0.0, "lt": v, "dispositions": 1}
            for y, v in by_year.items()}


def _row(ledger, year):
    return next(x for x in ledger["rows"] if x["year"] == year)


def _trades(year, ticker, sale):
    return (f"BUYSELL {year}-02-01 09:30:00 {ticker}.TO 50 CAD 10.00 -500.00 0.00\n"
            f"BUYSELL {year}-08-01 09:30:00 {ticker}.TO -50 CAD {sale / 50:.2f} "
            f"{sale:.2f} 0.00\n")


def _ok(r):
    assert r.returncode == 0, r.stdout[-800:] + r.stderr[-800:]
    return r


def _build_years(top, claims, sales, close=(), all_locks=False):
    """Year folders under `top`: `sales` {year: (ticker, proceeds)} on the 500 each
    bought, `claims` {year: the [carryover] claimed text}, each run, those in `close`
    closed in turn. `all_locks`: each holds the locks before it, not just the prior's."""
    (top / "inputs" / "margin").mkdir(parents=True)
    (top / "inputs" / "margin" / "t.tt").write_text(
        "".join(_trades(y, *sale) for y, sale in sorted(sales.items())))
    for year in sorted(sales):
        d = top / str(year)
        d.mkdir()
        prior = (f'prior_year_record = "../{year - 1}/filed/{year - 1}.json"\n\n'
                 if year > min(sales) else "")
        claim = claims.get(year)
        (d / "taxjson.toml").write_text(
            _TOML.replace("year = 2023", f"year = {year}").replace(
                "[accounts.margin]", prior + "[accounts.margin]")
            + (f"\n[carryover]\nclaimed = {{ {claim} }}\n" if claim else ""))
        if all_locks:
            (d / "filed").mkdir()
            for earlier in range(min(sales), year):
                shutil.copy(top / str(earlier) / "filed" / f"{earlier}.json", d / "filed")
        _ok(_cli(d, "run", "--no-input"))
        if year in close:
            _ok(_cli(d, "close-year", "--yes"))


_SALES = {2021: ("ZZQA", 600.0), 2022: ("ZZQB", 450.0), 2023: ("ZZQC", 520.0)}
