"""Shared test helpers: a project of source, base and gains files for the audit.

Shared from: test_audit_command
"""
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from taxjson.bin.taxjson_audit import main as audit_main


def _tx(**kw):
    from taxjson.model.transaction import TaxTransaction
    base = dict(action="BUYSELL", date="2026-01-05", symbol="AAPL.US",
                quantity=10.0, currency="USD", price=100.0,
                net_amount=1000.0, account="margin",
                date_settle="2026-01-06")
    base.update(kw)
    return TaxTransaction(**base).to_dict()


def _write(dirp: Path, name: str, txs) -> Path:
    p = dirp / name
    p.write_text(json.dumps({"transactions": txs,
                             "metadata": {"input_files": [f"{name}.csv"]}}),
                 encoding="utf-8")
    return p


class _Project:
    """Source (USD) + base (CAD) + gains files for a buy/sell pair."""

    def __init__(self, tmp: Path, sell_price=120.0, rate=1.4):
        self.tmp = tmp
        buy = _tx(quantity=10.0, price=100.0, net_amount=1000.0,
                  date="2026-01-05", date_settle="2026-01-06")
        sell = _tx(quantity=-10.0, price=sell_price,
                   net_amount=10 * sell_price,
                   date="2026-02-10", date_settle="2026-02-11")
        self.source = _write(tmp, "src.json", [buy, sell])
        # Converted twins: same ids, CAD amounts (mirrors merge2).
        conv = []
        for t in (buy, sell):
            c = dict(t)
            for f in ("price", "net_amount", "proceeds", "gross_amount"):
                c[f] = float(c.get(f) or 0.0) * rate
            c["currency"] = "CAD"
            conv.append(c)
        self.base = _write(tmp, "base.json", conv)
        self.rates = tmp / "rates.csv"
        lines = []
        from datetime import date, timedelta
        d = date(2026, 1, 1)
        while d < date(2026, 4, 1):
            lines.append(f"{d.isoformat()} 12:00:00 USD CAD {rate}")
            d += timedelta(days=1)
        self.rates.write_text("\n".join(lines), encoding="utf-8")
        # The pipeline's saved gains: run the real engine on the base.
        from taxjson.engine.by_country import get_tax_rules
        from taxjson.lib.tx_load import load_transactions
        rules = get_tax_rules("canada")
        res = rules.compute_gains(load_transactions(self.base))
        self.gains = tmp / "gains.json"
        self.gains.write_text(json.dumps(
            {"transactions": res["transactions"]}, default=str),
            encoding="utf-8")

    def run(self, extra=(), expect=0):
        argv = ["--country", "canada", "--year", "2026",
                "--base", str(self.base), "--rates", str(self.rates),
                "--base-currency", "CAD",
                "--source", str(self.source),
                "--check", str(self.gains)] + list(extra)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = audit_main(argv)
        text = out.getvalue()
        assert rc == expect, (rc, text, err.getvalue())
        return text
