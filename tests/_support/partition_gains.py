"""Shared test helpers: rows as tax transactions and a gains document.

Shared from: test_partition_commands
"""
import json

from tax_rules.dual import tx


def _tx(r):
    """The same row as a TaxTransaction (for gains_both)."""
    return tx(r["action"], r["date"], r["symbol"], r["quantity"],
              r["net_amount"], settle=r["date_settle"],
              account=r["account"], currency=r["currency"])


def _gains_doc(res, account="margin"):
    return json.dumps(dict(res, metadata={"account": account}))
