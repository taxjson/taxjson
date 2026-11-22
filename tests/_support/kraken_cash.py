"""Shared test helpers: a Kraken ledger read with or without cash.

Shared from: test_fix_d_crypto_drip
"""
import contextlib
import io
import tempfile
from pathlib import Path


_KR_LEDGER_H = ('"txid","refid","time","type","subtype","aclass","asset",'
                '"wallet","amount","fee","balance"\n')


def _kraken(text: str, prefix: str, cash: bool):
    from taxjson.lib.brokerages.kraken import KrakenBrokerage

    class K(KrakenBrokerage):
        stablecoins_as_cash = cash
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / f"{prefix}.csv"
        p.write_text(text)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            return K().parse_file(p)
