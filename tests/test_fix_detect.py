"""Broker detection by CONTENT first (owner request, 2026-10-04).

Order: an explicit generic mapping (configuration) > the file's content
(each supported export's header signature, taken from the parser's own
required columns) > the file name (cb_/kr_/generic_ prefixes, the words
coinbase/kraken) only when no content signature matched. A file matching
two signatures is an error naming both; `taxjson run` prints how every
file was detected. All data here is synthetic.
"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EX = REPO_ROOT / "examples"
FIX = REPO_ROOT / "tests" / "fixtures"

from taxjson.lib.brokerages import detect as D  # noqa: E402

_QT_H = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
         "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
         "Account #,Activity Type,Account Type\n")
_QT_ROW = ("2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,XEI.TO,"
           "ISHARES COMP,100,10.00,1000.00,9.95,-1009.95,CAD,55500001,"  # pii-ok
           "Trades,Individual\n")

_WB_PRE = (",,,,,,,,,\n"
           "Account Number / Numéro de compte:,,,,,,,55500001,,\n"  # pii-ok
           "Year / Année:,,,,,,,2025,,\n")
# The two Webull Trading Summary layouts: 2024 (9 columns) and 2025 (an
# empty column inserted after Price).
_WB_H24 = ('"Currency\nDevise",Date,"Action Code\nCode d\'action",'
           '"Symbol\nSymbole","Security Description\nDescription des titres",'
           'Type Code of Securities Code de genre de titres,'
           '"Quantity of Securities Quantité\nde titres","Price\nPrix",'
           '"Proceeds of\nDisposition or Settlement Amount Produits de '
           'disposition"\n')
_WB_H25 = _WB_H24.replace('"Price\nPrix",', '"Price\nPrix",,')
_WB_ROW24 = 'USD,15-01-2025,BUY,ZZQ,ZZQ CORP,EQ,10,12.00,"(120.00)"\n'
_WB_ROW25 = 'USD,15-01-2025,BUY,ZZQ,ZZQ CORP,EQ,10,12.00,,"(120.00)"\n'

_RBC_FULL_H = ("Date,Activity,Symbol,Symbol Description,Quantity,Price,"
               "Settlement Date,Account,Value,Currency,Description\n")
_RBC_ROW = ("2025-04-14 00:00:00,Buy,ZZR,ZZR CORP,10,5.00,"
            "2025-04-16 00:00:00,55500001,-50.00,CAD,ZZR CORP - Buy\n")  # pii-ok

_CB_LEGACY = ("You can use this transaction report to inform your likely "
              "tax obligations.\n"
              "Transactions\n"
              "User,Sample User,abc123\n\n"
              "Timestamp,Transaction Type,Asset,Quantity Transacted,"
              "Spot Price Currency,Spot Price at Transaction,Subtotal,"
              "Total (inclusive of fees),Fees,Notes\n"
              "2025-01-18T16:24:11Z,Buy,BTC,0.01,CAD,60000,600,610,10,"
              "Bought 0.01 BTC\n")
_KR_LEDGER = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,"
              "fee,balance\n"
              "LX1,RX1,2025-01-15 10:00:00,deposit,,currency,ZUSD,"
              "spot / main,1000,0,1000\n")


def _sample_texts():
    """(broker, label, text) for every demo, fixture and layout variant."""
    out = []
    for broker, demo in (("ib", "ib_demo.csv"),
                         ("questrade", "questrade_demo.csv"),
                         ("webull", "webull_demo.csv"),
                         ("rbc_direct", "rbc_direct_demo.csv"),
                         ("coinbase", "coinbase_demo.csv"),
                         ("kraken", "kraken_demo.csv")):
        out.append((broker, f"examples/{demo}",
                    (EX / demo).read_text(encoding="utf-8")))
        sample = FIX / broker / "sample.csv"
        out.append((broker, f"fixtures/{broker}",
                    sample.read_text(encoding="utf-8")))
    ib = (EX / "ib_demo.csv").read_text(encoding="utf-8")
    lines = ib.splitlines(keepends=True)
    out += [
        ("ib", "ib trades-first flex",
         "".join(l for l in lines
                 if not l.startswith(("Statement,", "Account Information,")))),
        ("ib", "ib without BrokerName",
         "".join(l for l in lines if "BrokerName" not in l)),
        ("questrade", "questrade fetch-shaped", _QT_H + _QT_ROW),
        ("webull", "webull 2024 9-col", _WB_PRE + _WB_H24 + _WB_ROW24),
        ("webull", "webull 2025 10-col", _WB_PRE + _WB_H25 + _WB_ROW25),
        ("rbc_direct", "rbc 12-col no preamble", _RBC_FULL_H + _RBC_ROW),
        ("rbc_direct", "rbc Activity Export preamble",
         '"Activity Export as of Jan 5, 2026 at 8:59:00 am ET"\n\n'
         '"Account: 55500001 - Margin"\n\n' + _RBC_FULL_H + _RBC_ROW),  # pii-ok
        ("coinbase", "coinbase legacy layout with preamble", _CB_LEGACY),
        ("kraken", "kraken ledger", _KR_LEDGER),
    ]
    return out


def _write(td, name, text, encoding="utf-8"):
    p = Path(td) / name
    p.write_bytes(text.encode(encoding))
    return p


class TestDetectorMatrix(unittest.TestCase):
    """Every sample of every broker against every detector: exactly its
    own detector matches (the signatures are mutually exclusive)."""

    def test_each_sample_matches_only_its_own_detector(self):
        samples = _sample_texts()
        # An RBC "Holdings Export" (positions, not activity) is no
        # trade detector's: tests/test_positions_reports.py.
        self.assertGreaterEqual(len(samples), 19)
        for broker, label, text in samples:
            rows = D.csv_rows(text)
            head = rows[:D.SCAN_ROWS]
            for name, fn in D.DETECTORS.items():
                reason, _hint = fn(rows, head)
                with self.subTest(sample=label, detector=name):
                    if name == broker:
                        self.assertTrue(reason, f"{label} not matched by "
                                                f"its own detector")
                    else:
                        self.assertIsNone(reason, f"{label} also matched "
                                                  f"{name}: {reason}")

    def test_every_supported_parser_has_a_detector(self):
        self.assertEqual(set(D.DETECTORS),
                         {"ib", "questrade", "webull", "rbc_direct",
                          "coinbase", "kraken"})

    def test_utf16_exports_detect(self):
        with tempfile.TemporaryDirectory() as td:
            for broker, label, text in _sample_texts()[:12]:
                p = _write(td, "export.csv", text, "utf-16")
                with self.subTest(label):
                    self.assertEqual(D.detect(p).broker, broker)


class TestContentWithNeutralName(unittest.TestCase):
    def test_each_broker_by_content_named_export_csv(self):
        with tempfile.TemporaryDirectory() as td:
            for broker, label, text in _sample_texts():
                p = _write(td, "export.csv", text)
                det = D.detect(p)
                with self.subTest(label):
                    self.assertEqual(det.broker, broker)
                    self.assertEqual(det.how, "content")
                    self.assertTrue(det.reason.startswith("content: "))
                    self.assertEqual(det.note, "")

    def test_run_detect_broker_agrees(self):
        from taxjson.bin.taxjson_run import detect_broker
        with tempfile.TemporaryDirectory() as td:
            p = _write(td, "statement.csv",
                       (EX / "kraken_demo.csv").read_text(encoding="utf-8"))
            self.assertEqual(detect_broker(p), "kraken")

    def test_reasons_name_the_signature(self):
        with tempfile.TemporaryDirectory() as td:
            ib = D.detect(_write(td, "a.csv", (EX / "ib_demo.csv").read_text()))
            self.assertEqual(ib.reason, 'content: "Statement,Header" preamble')
            kl = D.detect(_write(td, "b.csv", _KR_LEDGER))
            self.assertEqual(kl.reason,
                             "content: ledger columns txid,refid,time…")
            self.assertEqual(
                kl.line("inputs/crypto/b.csv"),
                "inputs/crypto/b.csv → Kraken (content: ledger columns "
                "txid,refid,time…)")


class TestNameFallback(unittest.TestCase):
    def test_prefix_and_word_route_only_without_a_content_match(self):
        with tempfile.TemporaryDirectory() as td:
            for name, want, tok in (("cb_old.csv", "coinbase", "cb_"),
                                    ("kr_ledger.csv", "kraken", "kr_"),
                                    ("my_kraken_export.csv", "kraken",
                                     "kraken"),
                                    ("coinbase-2025.csv", "coinbase",
                                     "coinbase")):
                det = D.detect(_write(td, name, "anything,else\n1,2\n"))
                with self.subTest(name):
                    self.assertEqual(det.broker, want)
                    self.assertEqual(det.how, "name")
                    self.assertEqual(
                        det.reason, f'file name "{tok}" — no content '
                                    f'match')
                    self.assertIn("by its file name", det.note)

    def test_mid_name_kr_is_not_a_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            det = D.detect(_write(td, "ibkr_notes.csv", "a,b\n1,2\n"))
            self.assertIsNone(det.broker)


class TestContentBeatsName(unittest.TestCase):
    def test_conflict_note(self):
        with tempfile.TemporaryDirectory() as td:
            det = D.detect(_write(
                td, "cb_moved.csv",
                (EX / "kraken_demo.csv").read_text(encoding="utf-8")))
            self.assertEqual(det.broker, "kraken")
            self.assertEqual(det.how, "content")
            self.assertIn('suggests Coinbase ("cb_")', det.note)
            self.assertIn("read as Kraken", det.note)
            # An IB export named after a holding (Kraken Robotics).
            det = D.detect(_write(td, "kraken_robotics_2025.csv",
                                  (EX / "ib_demo.csv").read_text()))
            self.assertEqual(det.broker, "ib")
            self.assertIn('suggests Kraken ("kraken")', det.note)

    def test_matching_name_has_no_note(self):
        with tempfile.TemporaryDirectory() as td:
            det = D.detect(_write(td, "kr_trades.csv",
                                  (EX / "kraken_demo.csv").read_text()))
            self.assertEqual((det.broker, det.note), ("kraken", ""))


class TestGenericMappingPrecedence(unittest.TestCase):
    def test_sidecar_mapping_beats_content(self):
        with tempfile.TemporaryDirectory() as td:
            p = _write(td, "generic_ws.csv", (EX / "ib_demo.csv").read_text())
            Path(str(p) + ".toml").write_text("[columns]\n")
            det = D.detect(p)
            self.assertEqual((det.broker, det.how), ("generic", "mapping"))
            self.assertEqual(det.reason, "mapping generic_ws.csv.toml")
            self.assertIn("content matches Interactive Brokers", det.note)

    def test_sidecar_on_any_name_is_a_mapping(self):
        with tempfile.TemporaryDirectory() as td:
            p = _write(td, "wealth.csv", "Date,Type\n2025-01-01,BUY\n")
            Path(str(p) + ".toml").write_text("[columns]\n")
            self.assertEqual(D.detect(p).broker, "generic")

    def test_shared_generic_toml_applies_to_generic_files_only(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "generic.toml").write_text("[columns]\n")
            g = D.detect(_write(td, "generic_a.csv", "Date,Type\n"))
            self.assertEqual((g.broker, g.reason),
                             ("generic", "mapping generic.toml"))
            other = D.detect(_write(td, "statement.csv",
                                    (EX / "ib_demo.csv").read_text()))
            self.assertEqual(other.broker, "ib")

    def test_generic_name_without_mapping_is_only_a_name(self):
        with tempfile.TemporaryDirectory() as td:
            det = D.detect(_write(td, "generic_qt.csv", _QT_H + _QT_ROW))
            self.assertEqual(det.broker, "questrade")
            self.assertIn('suggests generic ("generic_")', det.note)
            det = D.detect(_write(td, "generic_x.csv", "Date,Type\n"))
            self.assertEqual((det.broker, det.how), ("generic", "name"))


class TestAmbiguity(unittest.TestCase):
    _TWO = (_QT_H + _QT_ROW + "\n"
            "Timestamp,Transaction Type,Asset,Quantity Transacted,"
            "Price Currency,Price at Transaction,Subtotal,Total,Fees\n"
            "2025-01-18 16:24:11 UTC,Buy,BTC,0.01,CAD,60000,600,610,10\n")

    def test_two_signatures_is_an_error_naming_both(self):
        with tempfile.TemporaryDirectory() as td:
            p = _write(td, "both.csv", self._TWO)
            with self.assertRaises(D.AmbiguousBroker) as cm:
                D.detect(p)
            msg = str(cm.exception)
            self.assertIn("Questrade", msg)
            self.assertIn("Coinbase", msg)
            self.assertIn("refusing to guess", msg)

    def test_run_stops_on_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, {"both.csv": self._TWO})
            r = _run_cli(root, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("matches 2 broker exports", r.stderr)
            self.assertNotIn("Traceback", r.stderr)

    def test_standalone_tool_refuses_it(self):
        with tempfile.TemporaryDirectory() as td:
            p = _write(td, "both.csv", self._TWO)
            r = _tool(p)
            self.assertEqual(r.returncode, 1)
            self.assertEqual(r.stdout, "")
            self.assertIn("Questrade", r.stderr)


class TestCannotDetect(unittest.TestCase):
    def test_message_advises_header_then_rename(self):
        from taxjson.bin.taxjson_detect_brokerage import cannot_detect_message
        with tempfile.TemporaryDirectory() as td:
            det = D.detect(_write(td, "x.csv", "col_a,col_b\n1,2\n"))
            self.assertIsNone(det.broker)
            msg = cannot_detect_message(det)
            self.assertLess(msg.index("Check the header first"),
                            msg.index("cb_ or kr_"))

    def test_near_miss_is_named(self):
        with tempfile.TemporaryDirectory() as td:
            # An RBC header without its money columns.
            det = D.detect(_write(td, "x.csv",
                                  "Date,Activity,Symbol,Settlement Date\n"))
            self.assertIsNone(det.broker)
            self.assertIn("RBC activity header lacking", det.hint)

    def test_run_message(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, {"x.csv": "col_a,col_b\n1,2\n"})
            r = _run_cli(root, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("cannot detect broker", r.stderr)
            self.assertIn("Check the header first", r.stderr)


class TestSiblingsByContent(unittest.TestCase):
    """The Kraken and Webull parsers pair a file with its folder
    siblings; those are found by the same detection, not by name."""

    def test_kraken_ledger_with_a_neutral_name_is_a_sibling(self):
        from taxjson.lib.brokerages.kraken import KrakenBrokerage
        with tempfile.TemporaryDirectory() as td:
            t = _write(td, "trades.csv", (EX / "kraken_demo.csv").read_text())
            _write(td, "ledger_2025.csv", _KR_LEDGER)
            _write(td, "other.csv", _QT_H + _QT_ROW)
            self.assertEqual([p.name for p in
                              D.same_broker_siblings(t, "kraken")],
                             ["ledger_2025.csv"])
            self.assertIsNotNone(KrakenBrokerage._sibling_ledger_index(t))

    def test_crowded_folder_falls_back_to_names(self):
        # A shared temporary folder of thousands of CSVs is not read file
        # by file; there only the parser's old name test applies.
        with tempfile.TemporaryDirectory() as td:
            t = _write(td, "trades.csv", (EX / "kraken_demo.csv").read_text())
            _write(td, "ledger.csv", _KR_LEDGER)
            _write(td, "kr_ledger.csv", _KR_LEDGER)
            old = D.SIBLING_SCAN_LIMIT
            D.SIBLING_SCAN_LIMIT = 1
            try:
                got = D.same_broker_siblings(
                    t, "kraken", by_name=lambda n: n.startswith("kr_"))
            finally:
                D.SIBLING_SCAN_LIMIT = old
            self.assertEqual([p.name for p in got], ["kr_ledger.csv"])

    def test_webull_siblings(self):
        from taxjson.lib.brokerages.webull import WebullBrokerage
        with tempfile.TemporaryDirectory() as td:
            a = _write(td, "summary_2024.csv", _WB_PRE + _WB_H24 + _WB_ROW24)
            _write(td, "summary_2025.csv", _WB_PRE + _WB_H25 + _WB_ROW25)
            _write(td, "rbc.csv", _RBC_FULL_H + _RBC_ROW)
            self.assertEqual([p.name for p in
                              WebullBrokerage._sibling_exports(a)],
                             ["summary_2025.csv"])


# ------------------------------------------------------------ CLI / run

_CONFIG = """\
[settings]
year = 2025
country = "canada"
base_currency = "CAD"
source_currencies = []
option_grant_timing_since = 2025

[accounts.margin]
type = "taxable"
"""


def _project(tmp, files, acct="margin"):
    root = Path(tmp)
    (root / "taxjson.toml").write_text(_CONFIG)
    d = root / "inputs" / acct
    d.mkdir(parents=True)
    for name, text in files.items():
        (d / name).write_text(text, encoding="utf-8")
    return root


def _env():
    e = dict(os.environ)
    e["TAXJSON_OFFLINE"] = "1"
    e["PYTHONPATH"] = (str(REPO_ROOT / "src") + os.pathsep
                       + e.get("PYTHONPATH", ""))
    return e


def _run_cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True, env=_env(),
        stdin=subprocess.DEVNULL)


def _tool(path):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_detect_brokerage",
         str(path)], capture_output=True, text=True, env=_env())


class TestRunPrintsDetection(unittest.TestCase):
    _GENERIC_CSV = ("Date,Transaction type,Symbol,Quantity,Price,Amount,"
                    "Currency\n2025-02-03,BUY,ZZG.TO,10,5.00,-50.00,CAD\n")

    def test_run_and_fast_list_every_file_once(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, {
                "export.csv": _QT_H + _QT_ROW,
                "kr_activity.csv": _RBC_FULL_H + _RBC_ROW,
                "generic_ws.csv": self._GENERIC_CSV,
            })
            (root / "inputs" / "margin" / "generic_ws.csv.toml").write_text(
                (EX / "generic_wealthsimple.toml").read_text())
            want = [
                "  inputs/margin/export.csv → Questrade (content: "
                "columns Transaction Date,Settlement Date,Action…)",
                "  inputs/margin/generic_ws.csv → generic (mapping "
                "generic_ws.csv.toml)",
                "  inputs/margin/kr_activity.csv → RBC Direct Investing "
                "(content: activity header Date,Activity,Symbol,Symbol "
                "Description…)",
                '    note: kr_activity.csv: the file name suggests Kraken '
                '("kr_"), but its content matches RBC Direct Investing — '
                'read as RBC Direct Investing.',
            ]
            for args in (("run", "--no-input"),
                         ("run", "--fast", "--no-input")):
                r = _run_cli(root, *args)
                with self.subTest(args=args):
                    self.assertEqual(r.returncode, 0, r.stderr[-2000:])
                    out = r.stdout.splitlines()
                    i = out.index("==> margin  (taxable)")
                    self.assertEqual(out[i + 1:i + 5], want)
                    self.assertEqual(sum(ln.startswith("  inputs/")
                                         for ln in out), 3)
            # Persisted for the .sum DIAGNOSTICS (the note only).
            diag = (root / "work" / "margin_detect.diag").read_text()
            self.assertIn("generic_ws.csv → generic", diag)
            summ = (root / "reports" / "margin.sum").read_text()
            self.assertIn('note: kr_activity.csv: the file name suggests',
                          summ)
            self.assertNotIn("→ Questrade", summ)

    def test_console_names_the_file_and_the_diag_masks_it(self):
        """The person's own terminal names each file as it is on disk
        (two exports whose names mask alike must be told apart); the
        saved .diag and .sum mask account-number-like parts (owner,
        2026-10-05)."""
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, {"U5550001_2025.csv":  # pii-ok
                                 _QT_H + _QT_ROW})
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertIn("  inputs/margin/U5550001_2025.csv → "  # pii-ok
                          "Questrade", r.stdout)
            diag = (root / "work" / "margin_detect.diag").read_text()
            self.assertIn("inputs/margin/U5***_2025.csv → Questrade", diag)
            self.assertNotIn("U5550001", diag)  # pii-ok
            for p in (root / "reports").glob("*.sum"):
                self.assertNotIn("U5550001", p.read_text())  # pii-ok


class TestStandaloneTool(unittest.TestCase):
    def test_prints_id_and_reason(self):
        with tempfile.TemporaryDirectory() as td:
            p = _write(td, "export.csv", _KR_LEDGER)
            r = _tool(p)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout, "kraken\n")
            self.assertIn(f"{p} → Kraken (content: ledger columns "
                          f"txid,refid,time…)", r.stderr)

    def test_name_fallback_and_note(self):
        with tempfile.TemporaryDirectory() as td:
            r = _tool(_write(td, "cb_old.csv", "a,b\n1,2\n"))
            self.assertEqual(r.stdout, "coinbase\n")
            self.assertIn('Coinbase (file name "cb_" — no content '
                          'match)', r.stderr)
            self.assertIn("note:", r.stderr)

    def test_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            r = _tool(_write(td, "x.csv", "a,b\n1,2\n"))
            self.assertEqual(r.returncode, 1)
            self.assertIn("unknown", r.stderr)
            self.assertIn("Check the header first", r.stderr)


if __name__ == "__main__":
    unittest.main()
