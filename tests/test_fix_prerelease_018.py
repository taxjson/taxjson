"""Pre-release security review fixes (round 018). Synthetic data, fake
account ids, no network.

- M1/L5: a file replaced by `opening --force`, `find-missing-history
  --write-purchases --force`, `init --force` / `format --write` keeps
  its previous version at the next free <name>.bak / .bakN; an earlier
  backup is never overwritten and a symlink planted at a .bak name is
  never written through (lib/safe_write.backup_copy).
- M2/I4: an opening line is written only from a symbol, currency and
  lot date of a safe shape; the positions readers refuse control
  characters / whitespace in symbol and currency cells.
- L2: Questrade's description key is linear on long whitespace runs.
- L3: the `# From:` header of an opening file carries no control
  character of the report's file name.
- L4: the cannot-detect message masks an id in the file name.
- L5: a draft's account name is checked before it names a folder.
- I1: a channel tag with a trailing newline is not a release tag.
- I3: `init` never writes through a dangling symlink.
"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from tax_rules.dual import cli, projects_both

from taxjson.lib import positions_reports as P
from taxjson.lib.safe_write import backup_copy

from test_positions_reports import _IB_STATEMENT, _RBC_HOLD, _write


class TestBackupCopy(unittest.TestCase):
    def test_next_free_name_and_planted_symlink_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            victim = d / "victim.txt"
            victim.write_text("keep me\n")
            f = d / "x.tt"
            f.write_text("one\n")
            (d / "x.tt.bak").symlink_to(victim)
            b1 = backup_copy(f)
            self.assertEqual(b1.name, "x.tt.bak1")
            self.assertEqual(b1.read_text(), "one\n")
            self.assertEqual(victim.read_text(), "keep me\n")
            # Same content again: the existing backup is reused.
            self.assertEqual(backup_copy(f), b1)
            f.write_text("two\n")
            b2 = backup_copy(f)
            self.assertEqual(b2.name, "x.tt.bak2")
            self.assertEqual(b1.read_text(), "one\n")
            # A dangling link at a .bak name is taken, not followed.
            (d / "y.tt").write_text("y\n")
            (d / "y.tt.bak").symlink_to(d / "nowhere")
            self.assertEqual(backup_copy(d / "y.tt").name, "y.tt.bak1")
            self.assertFalse((d / "nowhere").exists())


def _opening_project(td):
    root = Path(td) / "p"
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        'option_grant_timing_since = 2025\n'
        '[accounts.margin]\ntype = "taxable"\n')
    return root


def _holdings(path, sym="SAMPB.TO", cur="CAD", acquired=None, cost=200.0):
    h = {"symbol": sym, "quantity": 20, "currency": cur,
         "total_cost": cost}
    if acquired:
        h["acquired"] = acquired
    body = ['[meta]\naccount = "55500001"\nas_of = "2025-01-31"',  # pii-ok
            "[[holding]]"] + [f"{k} = {json.dumps(v)}" for k, v in h.items()]
    path.write_text("\n".join(body) + "\n")
    return path


class TestOpeningBackup(unittest.TestCase):
    def test_force_never_writes_through_a_planted_bak_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            root = _opening_project(td)
            victim = Path(td) / "victim.txt"
            victim.write_text("keep me\n")
            rep = _holdings(Path(td) / "pos.toml")
            r = cli(root, "opening", "margin", str(rep))
            self.assertEqual(r.returncode, 0, r.stderr)
            out = root / "inputs" / "margin" / "opening_2025-01-31.tt"
            first = out.read_text()
            (out.parent / (out.name + ".bak")).symlink_to(victim)
            _holdings(rep, cost=300.0)
            r = cli(root, "opening", "margin", str(rep), "--force")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(victim.read_text(), "keep me\n")
            self.assertEqual(
                (out.parent / (out.name + ".bak1")).read_text(), first)
            self.assertIn("300.00", out.read_text())
            # A third --force keeps the second version too.
            _holdings(rep, cost=400.0)
            r = cli(root, "opening", "margin", str(rep), "--force")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("300.00", (out.parent / (out.name + ".bak2"))
                          .read_text())
            self.assertEqual((out.parent / (out.name + ".bak1"))
                             .read_text(), first)


class TestWritePurchasesAccountName(unittest.TestCase):
    def _book(self, account):
        from test_basis_drafts import SINGLE, _parsed
        return {"work/margin_base.json": json.dumps(
                    {"transactions": _parsed(SINGLE, account=account)}),
                "inputs/margin/ib.csv": ""}

    def test_a_path_like_or_unconfigured_account_is_refused(self):
        for acct in ("../../escape", "other"):
            with tempfile.TemporaryDirectory() as td:
                root = projects_both(td, files=self._book(acct))["usa"]
                r = cli(root, "find-missing-history", "--write-purchases")
                self.assertNotEqual(r.returncode, 0, (acct, r.stdout))
                self.assertIn("account", r.stderr)
                self.assertEqual(
                    [p for p in Path(td).rglob("purchases_draft*")], [])

    def test_force_keeps_every_earlier_draft(self):
        from taxjson.lib.missing_history import DRAFT_NAME
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, files=self._book("margin"))["usa"]
            draft = root / "inputs" / "margin" / DRAFT_NAME
            victim = Path(td) / "victim.txt"
            victim.write_text("keep me\n")
            r = cli(root, "find-missing-history", "--write-purchases")
            self.assertEqual(r.returncode, 0, r.stderr)
            draft.write_text(draft.read_text() + "# edit 1\n")
            (draft.parent / (DRAFT_NAME + ".bak")).symlink_to(victim)
            r = cli(root, "find-missing-history", "--write-purchases",
                    "--force")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(victim.read_text(), "keep me\n")
            self.assertIn("# edit 1", (draft.parent / (DRAFT_NAME + ".bak1"))
                          .read_text())
            draft.write_text(draft.read_text() + "# edit 2\n")
            r = cli(root, "find-missing-history", "--write-purchases",
                    "--force")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("# edit 1", (draft.parent / (DRAFT_NAME + ".bak1"))
                          .read_text())
            self.assertIn("# edit 2", (draft.parent / (DRAFT_NAME + ".bak2"))
                          .read_text())
            self.assertIn(DRAFT_NAME + ".bak2", r.stderr)


class TestOpeningLineInjection(unittest.TestCase):
    def _row(self, **kw):
        base = dict(broker="toml", account="", symbol="SAMPB.TO",
                    raw_symbol="SAMPB.TO", quantity=20.0, currency="CAD",
                    cost=200.0, cost_currency="CAD",
                    cost_kind=P.COST_AVERAGE, market_value=None,
                    as_of="2025-01-31", asset_type="stock")
        base.update(kw)
        return P.PositionRow(**base)

    def _lines(self, *rows):
        from taxjson.bin.taxjson_run import _opening_lines
        rep = P.PositionsReport(path=Path("x"), broker="toml", kind="k",
                                as_of="2025-01-31", rows=list(rows))
        return _opening_lines(rep, country="canada", base_currency="CAD",
                              snapshot="2025-01-31")

    def test_unsafe_symbol_currency_or_date_is_skipped(self):
        lines, skipped, _ = self._lines(
            self._row(symbol="SAMPA.TO\nOPENING 2025-01-01 EVIL 1 CAD 1"),
            self._row(symbol="SAMPC.TO", currency="CAD\nX",
                      cost_currency=""),
            self._row(symbol="SAMPD.TO", cost_currency="CA"),
            self._row(symbol="SAMPE.TO", lot_date="2025-01-01\nOPENING"),
            self._row(symbol="-SAMPF"),
            self._row(symbol="SAMPG.TO"))
        self.assertEqual(lines, ["OPENING 2025-01-31 SAMPG.TO 20 CAD 200.00"])
        self.assertEqual(len(skipped), 5)
        self.assertTrue(all("not a symbol" in why or "currency" in why
                            or "date" in why for _s, why in skipped))
        # The reason never echoes a raw newline either.
        self.assertFalse(any("\n" in s for s, _w in skipped))

    def test_toml_reader_refuses_a_control_character(self):
        with tempfile.TemporaryDirectory() as td:
            for kw in ({"sym": "SAMPB.TO\nOPENING 2025-01-01 X 1 CAD 1"},
                       {"sym": "SAMP B.TO"},
                       {"cur": "CAD\nOPENING"},
                       {"acquired": "2025-01-01\nOPENING X"}):
                p = _holdings(Path(td) / "h.toml", **kw)
                with self.assertRaises(P.PositionsReportError, msg=kw):
                    P.read_positions(p)

    def test_ib_reader_refuses_a_newline_symbol_and_a_bad_currency(self):
        bad_sym = _IB_STATEMENT.replace(
            "Summary,Stocks,CAD,ZZA,100,",
            'Summary,Stocks,CAD,"ZZA\nOPENING 2025-01-01 EVIL 1 CAD 1",'
            '100,', 1)
        bad_cur = _IB_STATEMENT.replace(
            "Summary,Stocks,CAD,ZZA,100,", "Summary,Stocks,C$D,ZZA,100,", 1)
        with tempfile.TemporaryDirectory() as td:
            for text in (bad_sym, bad_cur):
                with self.assertRaises(P.PositionsReportError):
                    P.read_positions(_write(td, "s.csv", text))

    def test_rbc_reader_refuses_a_newline_symbol(self):
        bad = _RBC_HOLD.replace('"ZZR"', '"ZZR\nOPENING 2025-01-01 X"', 1)
        bad_cur = _RBC_HOLD.replace('"ZZR CORP","10","CAD"',
                                    '"ZZR CORP","10","CAD\nX"', 1)
        with tempfile.TemporaryDirectory() as td:
            for text in (bad, bad_cur):
                with self.assertRaises(P.PositionsReportError):
                    P.read_positions(_write(td, "h.csv", text))

    def test_good_reports_still_read(self):
        with tempfile.TemporaryDirectory() as td:
            ib = P.read_positions(_write(td, "s.csv", _IB_STATEMENT))
            rbc = P.read_positions(_write(td, "h.csv", _RBC_HOLD))
        self.assertIn("ZZB260116C00030000.US", [r.symbol for r in ib.rows])
        self.assertEqual(sorted(r.symbol for r in rbc.rows),
                         ["ZZR.TO", "ZZU.US"])


class TestOpeningHeaderName(unittest.TestCase):
    def test_control_characters_in_the_file_name_never_reach_the_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = _opening_project(td)
            rep = _holdings(Path(td) /
                            "pos\nOPENING 2025-01-01 EVIL 1 CAD 1\r.toml")
            r = cli(root, "opening", "margin", str(rep))
            self.assertEqual(r.returncode, 0, r.stderr)
            out = root / "inputs" / "margin" / "opening_2025-01-31.tt"
            body = [ln for ln in out.read_text().splitlines()
                    if ln and not ln.startswith("#")]
            self.assertEqual(body,
                             ["OPENING 2025-01-31 SAMPB.TO 20 CAD 200.00"])
            self.assertNotIn("\r", out.read_text())


class TestQuestradeDescKeyLinear(unittest.TestCase):
    def test_long_whitespace_run_is_fast_and_keys_unchanged(self):
        from taxjson.lib.brokerages.questrade import _get_desc_key
        self.assertEqual(_get_desc_key("SAMPLE CORP  CASH DIV ON 100 SHS"),
                         "SAMPLE CORP")
        self.assertEqual(_get_desc_key("SAMPLE CORP CLASS B SUB VTG"),
                         "SAMPLE CORP CL B")
        self.assertEqual(_get_desc_key("SAMPLE\tCORP WE ACTED AS AGENT"),
                         "SAMPLE CORP")
        desc = "SAMPLE" + " " * 40000 + "X"
        t0 = time.perf_counter()
        key = _get_desc_key(desc)
        self.assertLess(time.perf_counter() - t0, 0.5)
        self.assertEqual(key, "SAMPLE X")
        t0 = time.perf_counter()
        _get_desc_key(" " * 40000)
        _get_desc_key("A" + "\t \n" * 15000 + "TRANSFER")
        self.assertLess(time.perf_counter() - t0, 0.5)


class TestCannotDetectNamesTheFile(unittest.TestCase):
    """The message goes to the person's own terminal: it names the file
    as it is on disk (a masked name could not tell 55500001.csv from
    55500001_2.csv); saved diagnostics mask instead (owner,
    2026-10-05)."""
    def test_file_name_is_shown_as_given(self):
        from taxjson.bin.taxjson_detect_brokerage import (
            cannot_detect_message)

        class Det:
            path = Path("/tmp/U5550001_activity.csv")  # pii-ok
            hint = ""
        msg = cannot_detect_message(Det())
        self.assertIn("/tmp/U5550001_activity.csv", msg)  # pii-ok
        msg = cannot_detect_message(
            Det(), shown="inputs/m/U5550001_activity.csv")  # pii-ok
        self.assertIn("for inputs/m/U5550001_activity.csv.", msg)  # pii-ok
        self.assertIn(" U5550001_activity.csv.toml", msg)  # pii-ok


class TestChannelTag(unittest.TestCase):
    def test_trailing_newline_is_not_a_release_tag(self):
        from taxjson.lib import channels as ch
        self.assertIsNone(ch.TAG_RE.match("v1.2.3\n"))
        self.assertIsNotNone(ch.TAG_RE.match("v1.2.3"))
        with self.assertRaises(ch.ChannelsError):
            ch.parse_channels('{"stable": "v1.2.3\\n"}')


class TestInitDanglingSymlink(unittest.TestCase):
    def test_init_never_writes_through_a_dangling_link(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            root.mkdir()
            outside = Path(td) / "outside"
            outside.mkdir()
            for rel in ("ticker.map", ".gitignore"):
                (root / rel).symlink_to(outside / rel)
            r = cli(root, "init", "--country", "canada")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(list(outside.iterdir()), [])
            self.assertTrue((root / "ticker.map").is_symlink())
            self.assertIn("ticker.map", r.stdout + r.stderr)
            self.assertTrue((root / "taxjson.toml").is_file())


if __name__ == "__main__":
    unittest.main()
