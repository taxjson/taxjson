"""A broker export with one or two leading UTF-8 BOMs (a spreadsheet's
"CSV UTF-8" save, or a re-save that adds its own BOM in front of the one
it kept) is detected and parsed exactly like the file without them."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.brokerages.base import decode_broker_text
from taxjson.lib.brokerages.detect import detect_broker

ROOT = Path(__file__).resolve().parent.parent
BOM = b"\xef\xbb\xbf"
DEMOS = ("questrade", "rbc_direct", "ib", "webull", "kraken", "coinbase")


def _parse(path: Path, broker: str):
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), TAXJSON_OFFLINE="1",
               TAXJSON_LOCAL_TZ="America/Toronto")
    p = subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_brokerage",
         "--brokerage", broker, str(path)],
        capture_output=True, text=True, env=env, cwd=path.parent)
    return p.returncode, p.stdout


class TestDecode(unittest.TestCase):
    def test_every_leading_bom_is_dropped(self):
        for n in (0, 1, 2, 3):
            self.assertEqual(decode_broker_text(BOM * n + b"A,B\n"), "A,B\n")

    def test_utf16_with_a_second_bom(self):
        raw = b"\xff\xfe" + "﻿A,B\n".encode("utf-16-le")
        self.assertEqual(decode_broker_text(raw), "A,B\n")


class TestDemoExportsWithBoms(unittest.TestCase):
    def test_detected_and_parsed_alike(self):
        for broker in DEMOS:
            src = ROOT / "examples" / f"{broker}_demo.csv"
            raw = src.read_bytes()
            if raw.startswith(BOM):
                raw = raw[len(BOM):]
            results = []
            for n in (0, 1, 2):
                with self.subTest(broker=broker, boms=n), \
                        tempfile.TemporaryDirectory() as td:
                    f = Path(td) / f"{broker}_demo.csv"
                    f.write_bytes(BOM * n + raw)
                    self.assertEqual(detect_broker(f), broker)
                    rc, out = _parse(f, broker)
                    self.assertEqual(rc, 0)
                    results.append(len(json.loads(out)) if out.strip() else 0)
            self.assertEqual(len(set(results)), 1, (broker, results))
            self.assertGreater(results[0], 0, broker)


if __name__ == "__main__":
    unittest.main()
