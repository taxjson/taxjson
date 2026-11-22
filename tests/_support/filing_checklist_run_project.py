"""Shared test helpers: a Canadian project of a margin, an RRSP and a crypto account, its checklist context and scripted subcommand results.

Shared from: test_checklist
"""
import json
from datetime import date
from pathlib import Path

from taxjson.lib import checklist as cl


CFG = {"settings": {"year": 2025, "country": "canada", "base_currency": "CAD"},
       "accounts": {"margin": {"type": "taxable", "holdings": ["h.toml"]},
                    "rrsp": {"type": "sheltered"},
                    "crypto": {"type": "taxable", "crypto": True}}}


def _project(root: Path, *, activity_to="2026-02-02", with_reports=True):
    (root / "taxjson.toml").write_text(
        '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\ncountry = "canada"\nbase_currency = "CAD"\n'
        '[accounts.margin]\ntype = "taxable"\nholdings = ["h.toml"]\n'
        '[accounts.rrsp]\ntype = "sheltered"\n'
        '[accounts.crypto]\ntype = "taxable"\ncrypto = true\n')
    for a in ("margin", "rrsp", "crypto"):
        (root / "inputs" / a).mkdir(parents=True)
    (root / "inputs" / "margin" / "m.csv").write_text("x\n")
    (root / "inputs" / "crypto" / "cb_x.csv").write_text("x\n")
    (root / "work").mkdir()
    rows = [{"action": "BUYSELL", "date": "2025-03-03", "date_settle": "2025-03-04",
             "symbol": "X.TO", "quantity": 10, "account": "margin"},
            {"action": "ADJUST", "date": "2025-06-01", "date_settle": "2025-06-01",
             "symbol": "X.TO", "quantity": 0, "account": "margin"},
            {"action": "BUYSELL", "date": activity_to, "date_settle": activity_to,
             "symbol": "X.TO", "quantity": -10, "account": "margin"}]
    (root / "work" / "margin_base.json").write_text(json.dumps({"transactions": rows}))
    (root / "work" / "margin_gains_wash.json").write_text(json.dumps({"transactions": [
        {"date": "2025-05-05", "disallowed_amount": 100.0, "permanently_disallowed": 0.0},
        {"date": "2024-05-05", "disallowed_amount": 0.0, "permanently_disallowed": 999.0}]}))
    # A full run writes a gains file for every taxable account with
    # inputs; wash-reviewed now blocks on one without (S067-11).
    (root / "work" / "crypto_gains.json").write_text(json.dumps({"transactions": []}))
    if with_reports:
        (root / "reports").mkdir()
        (root / "reports" / "margin.sum").write_text("DIAGNOSTICS\nvalidation: 0 error(s)\n")
        # run-clean now requires a report for every account with inputs
        # (S018-02) — a full run writes crypto.sum too.
        (root / "reports" / "crypto.sum").write_text("DIAGNOSTICS\nvalidation: 0 error(s)\n")


class FakeSub:
    """Scripted `taxjson <sub>` results: {first argv token: (code, out, err)}."""
    def __init__(self, table):
        self.table = table
        self.calls = []

    def __call__(self, argv, timeout=900):
        self.calls.append(argv)
        return self.table.get(argv[0], (0, "", ""))


def _ctx(root, table, today=date(2026, 9, 23)):
    return cl.Ctx(root=root, cfg=CFG, year=2025, today=today, run_sub=FakeSub(table))
