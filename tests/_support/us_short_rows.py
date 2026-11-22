"""Shared test helpers: a short sale and its cover as two US rows.

Shared from: test_fix_1029_r7e_us
"""
from tax_rules.dual import tx


def _short(date, cover, *, sym="ZQA.US", qty=100, proceeds=1000.0, cost=900.0,
           account="margin"):
    return [tx("BUYSELL", date, sym, -qty, proceeds, account=account),
            tx("BUYSELL", cover, sym, qty, cost, account=account)]
