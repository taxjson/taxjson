"""Shared test helpers: a built Canada project of several accounts, for
the commands that take an ACCOUNT list (`margin,beta`, lib/account_list).

margin and beta are taxable, each with a superficial loss, a split, a
rename and a `.tt` listing journal (beta's rename with a trade in the old
ticker after it), and a loss of margin's that beta's purchase denies
(XXC.TO: the pooled ACB of one taxpayer); gamma is taxable with a
purchase and no disposition; idle and spare are declared with no inputs
(no books); rrsp and tfsa are sheltered; coins1 and coins2 are crypto
accounts (a Canadian project blends them). Every ticker, coin and
figure is made up.

Shared from: test_account_lists_in_the_book_checks,
test_account_lists_in_renames_and_journals,
test_account_lists_in_wash_sales_and_gain_traces (tests/features/)
"""
from pathlib import Path

from _support.cli_command_runs import _tjs


TOML = """[settings]
year = 2025
country = "canada"
base_currency = "CAD"
source_currencies = ["USD"]
local_timezone = "America/Toronto"

[accounts.margin]
type = "taxable"

[accounts.beta]
type = "taxable"

[accounts.gamma]
type = "taxable"

[accounts.idle]
type = "taxable"

[accounts.spare]
type = "taxable"

[accounts.rrsp]
type = "sheltered"

[accounts.tfsa]
type = "sheltered"

[accounts.coins1]
type = "taxable"
crypto = true

[accounts.coins2]
type = "taxable"
crypto = true
"""

BOOKS = {
    "margin": ("BUYSELL 2025-01-06 10:00:00 ZZQ.TO 10 CAD 100 -1000 0\n"
               "BUYSELL 2025-03-03 10:00:00 ZZQ.TO -10 CAD 80 800 0\n"
               "BUYSELL 2025-03-10 10:00:00 ZZQ.TO 10 CAD 82 -820 0\n"
               "DIVIDEND 2025-04-01 10:00:00 ZZQ.TO 0 CAD 0 12.50 0\n"
               "SPLIT 2025-06-02 09:30:00 ZZQ.TO ZZQ.TO 2\n"
               "BUYSELL 2025-12-31 10:00:00 ZZQ.TO -4 CAD 50 200 0\n"
               "BUYSELL 2025-02-03 10:00:00 ZZG.TO 100 CAD 10 -1000 0\n"
               "JOURNAL 2025-03-05 ZZG.TO ZZG.U.TO 100\n"
               "BUYSELL 2025-02-04 10:00:00 VVA.TO 5 CAD 20 -100 0\n"
               "RENAME 2025-07-02 VVA.TO VVB.TO\n"
               "BUYSELL 2025-04-07 10:00:00 XXC.TO 10 CAD 50 -500 0\n"
               "BUYSELL 2025-05-05 10:00:00 XXC.TO -10 CAD 40 400 0\n"),
    "beta": ("BUYSELL 2025-02-06 10:00:00 YYR.TO 20 CAD 50 -1000 0\n"
             "BUYSELL 2025-05-06 10:00:00 YYR.TO -20 CAD 40 800 0\n"
             "BUYSELL 2025-05-20 10:00:00 YYR.TO 20 CAD 41 -820 0\n"
             "RENAME 2025-08-01 YYR.TO YYS.TO\n"
             "BUYSELL 2025-09-02 10:00:00 YYR.TO 1 CAD 45 -45 0\n"
             "BUYSELL 2025-02-03 10:00:00 WWT.TO 10 CAD 30 -300 0\n"
             "SPLIT 2025-04-01 09:30:00 WWT.TO WWT.TO 3\n"
             "BUYSELL 2025-02-03 10:00:00 ZZH.TO 50 CAD 10 -500 0\n"
             "JOURNAL 2025-04-07 ZZH.TO ZZH.U.TO 50\n"
             "BUYSELL 2025-05-12 10:00:00 XXC.TO 10 CAD 41 -410 0\n"),
    "gamma": "BUYSELL 2025-03-04 10:00:00 WWG.TO 3 CAD 30 -90 0\n",
    "rrsp": "BUYSELL 2025-02-10 10:00:00 XXS.TO 5 CAD 20 -100 0\n",
    "tfsa": "BUYSELL 2025-02-11 10:00:00 XXT.TO 4 CAD 25 -100 0\n",
    "coins1": ("BUYSELL 2025-01-06 10:00:00 SYN 1 CAD 10000 10000 0\n"
               "BUYSELL 2025-06-02 10:00:00 SYN -1 CAD 30000 30000 0\n"),
    "coins2": "BUYSELL 2025-02-03 10:00:00 SYN 1 CAD 20000 20000 0\n",
}

# The broker's positions of margin and beta at the year end, as a
# holdings file for `tjs sanity` (beta's YYR.TO bought after the rename
# left out: one difference to find).
HOLDINGS = """[[holding]]
symbol = "ZZQ.TO"
quantity = 16

[[holding]]
symbol = "ZZG.U.TO"
quantity = 100

[[holding]]
symbol = "VVB.TO"
quantity = 5

[[holding]]
symbol = "ZZH.U.TO"
quantity = 50

[[holding]]
symbol = "YYS.TO"
quantity = 20

[[holding]]
symbol = "WWT.TO"
quantity = 30

[[holding]]
symbol = "XXC.TO"
quantity = 10
"""


def account_list_books(tmp: Path) -> Path:
    """The project in `tmp`/p, built by `tjs run`; its root."""
    root = Path(tmp) / "p"
    root.mkdir(parents=True)
    (root / "taxjson.toml").write_text(TOML, encoding="utf-8")
    for name, text in BOOKS.items():
        (root / "inputs" / name).mkdir(parents=True)
        (root / "inputs" / name / "trades.tt").write_text(text,
                                                          encoding="utf-8")
    r = _tjs(root, "run", "--no-input")
    if r.returncode != 0:
        raise AssertionError(r.stdout[-2000:] + r.stderr[-2000:])
    return root
