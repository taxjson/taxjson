"""Shared test helpers: an IB statement with a return of capital, taxjson-brokerage run alone on it, a USD rates file and an offline run.

Shared from: test_partition_engine_inputs
"""


def _usd_rates(path, home, start="2023-01-01"):
    """A hand-written USD->CAD rates file reaching today (the run keeps
    a fresh one offline), and the same rates in the currency cache of
    `home`, the HOME _run_offline runs under: a run that rebuilds the
    file keeps them (backlog TJ-0635)."""
    from datetime import date, timedelta
    from _planted_rates import plant
    d, stop = date.fromisoformat(start), date.today()
    lines = []
    while d <= stop:
        lines.append(f"{d.isoformat()} 12:00:00 USD CAD 1.3500 boc")
        d += timedelta(days=1)
    plant(path, "\n".join(lines) + "\n", home)


def _run_offline(root, home, *args):
    import os
    import subprocess
    import sys
    from tax_rules.dual import SRC
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               HOME=str(home), NO_COLOR="1")
    return subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run",
                           "-C", str(root), *args], capture_output=True,
                          text=True, env=env, stdin=subprocess.DEVNULL,
                          timeout=300)


# An IB return of capital from a US issuer.
_ROC_BODY = ('Statement,Header,Field Name,Field Value\n'
             'Statement,Data,BrokerName,Interactive Brokers\n'
             'Dividends,Header,Currency,Account,Date,Description,Amount\n'
             'Dividends,Data,USD,U5550001,2026-06-30,'  # pii-ok
             'QZRX(US0000000017) Return of Capital USD 0.12 per Share,'
             '24.00\n')


def _standalone_brokerage(*flags):
    """taxjson-brokerage run alone on _ROC_BODY with FLAGS: (the
    process, the actions of the transactions it wrote)."""
    import json
    import os
    import subprocess
    import sys
    import tempfile
    from pathlib import Path
    from tax_rules.dual import SRC
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "ib.csv"
        p.write_text(_ROC_BODY, encoding="utf-8")
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_brokerage",
             "--brokerage", "ib", *flags, str(p)],
            capture_output=True, text=True,
            env=dict(os.environ, PYTHONPATH=str(SRC)))
    txs = []
    if r.returncode == 0:
        data = json.loads(r.stdout)
        txs = data["transactions"] if isinstance(data, dict) else data
    return r, [t["action"] for t in txs]
