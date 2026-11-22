"""Parser, import and dedup fixes of fix round 7 (wave C, group
parsers2): RBC holdings with a CASH ticker, dotted preferred series L
and V, the generic importer's fee-only and zero-net rows and its named
brokers, a Webull class-share MULT, a mapped warrant exercise target
and dedup keeping a copy's income facts. Every fixture is SYNTHETIC:
fake account ids (99900001, U1234567), invented tickers.

Formerly: test_fix_1029_r7c_parsers2
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
import _hermetic  # (a synthetic HOME, offline: tests/_hermetic)
import _paths

REPO = _paths.REPO
ENV = _hermetic.environ(TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0",
                        PYTHONPATH=str(REPO / "src"), NO_COLOR="1")


def _brokerage(td, brokerage, files, *extra):
    """taxjson-brokerage over {name: text} written in `td`: (rc, json or
    None, stderr)."""
    paths = []
    for name, text in files.items():
        p = Path(td) / name
        p.write_text(text, encoding="utf-8")
        if name.endswith(".csv"):
            paths.append(str(p))
    r = subprocess.run(
        python_module_argv("taxjson.bin.taxjson_brokerage",
                           ["--brokerage", brokerage, *extra, *paths]),
        capture_output=True, text=True, env=ENV,
        stdin=subprocess.DEVNULL, check=False)
    try:
        out = json.loads(r.stdout) if r.stdout.strip() else None
    except ValueError:
        out = None
    return r.returncode, out, r.stderr


_GENERIC_FEE_MAP = ('[columns]\ndate="Date"\naction="Action"\n'
                    'symbol="Symbol"\nquantity="Quantity"\nprice="Price"\n'
                    'fee="Fee"\n[defaults]\ncurrency="CAD"\n'
                    '[actions]\nBUY="buy"\n')


class TestGenericUnmappedFee(unittest.TestCase):
    """TJ-0972: an unmapped row whose only cash is its mapped fee is
    UNBOOKED and fails lint.

    Formerly: test_fix_1029_r7c_parsers2.TestGenericUnmappedFee"""

    def _run(self, *extra):
        text = ("Date,Action,Symbol,Quantity,Price,Fee\n"
                "2025-01-02,BUY,SYN.TO,10,10,0\n"
                "2025-02-03,SERVICE,CASH,0,0,100\n")
        with tempfile.TemporaryDirectory() as td:
            return _brokerage(td, "generic", {
                "generic.csv": text, "generic.toml": _GENERIC_FEE_MAP},
                "--country", "canada", *extra)

    def test_fee_only_row_is_unbooked(self):
        rc, _out, err = self._run("--strict")
        self.assertIn("UNBOOKED", err)
        self.assertIn("action 'SERVICE' (not in [actions], no [[classify]] rule) carries a "
                      "quantity/amount/fee", err)

    def test_lint_fails(self):
        rc, _out, err = self._run("--lint")
        self.assertEqual(rc, 1, err)


class TestGenericZeroNetSale(unittest.TestCase):
    """TJ-0989: a sale whose stated net is 0 infers its commission (no
    fee column, large fees allowed).

    Formerly: test_fix_1029_r7c_parsers2.TestGenericZeroNetSale"""

    MAP = ('[columns]\ndate="Date"\naction="Action"\nsymbol="Symbol"\n'
           'quantity="Quantity"\nprice="Price"\namount="Amount"\n'
           '[defaults]\ncurrency="CAD"\n[actions]\nSELL="sell"\nBUY="buy"\n'
           '[options]\nallow_large_fees=true\n')

    def _parse(self, rows, mapping=None):
        text = "Date,Action,Symbol,Quantity,Price,Amount\n" + rows
        with tempfile.TemporaryDirectory() as td:
            return _brokerage(td, "generic", {
                "generic.csv": text, "generic.toml": mapping or self.MAP},
                "--strict")

    def test_zero_net_sale_infers_the_fee(self):
        rc, out, err = self._parse("2025-06-02,SELL,SYN.TO,1,2,0\n")
        self.assertEqual(rc, 0, err)
        t = out["transactions"][0]
        self.assertEqual((t["net_amount"], t["fee"]), (0.0, 2.0))

    def test_without_the_opt_in_it_is_refused(self):
        rc, _out, err = self._parse(
            "2025-06-02,SELL,SYN.TO,1,2,0\n",
            self.MAP.replace("allow_large_fees=true", "allow_large_fees=false"))
        self.assertNotEqual(rc, 0)
        self.assertIn("allow_large_fees", err)

    def test_zero_amount_buy_still_refused(self):
        rc, _out, err = self._parse("2025-06-02,BUY,SYN.TO,1,2,0\n")
        self.assertNotEqual(rc, 0)
        self.assertIn("the amount cell is 0", err)


if __name__ == "__main__":
    unittest.main()
