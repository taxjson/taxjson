"""The tjs command audit of 2026-10-27, `taxjson redact` (backlog
TJ-0248, TJ-0249). Synthetic data only: made-up account ids (pii-ok)
and a made-up name."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.bin import taxjson_redact as R
from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent

QT_HEAD = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
           "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
           "Activity Type,Account #,Account Type\n")


def _qt_row(acct, desc="SYNTHETIC CORP"):
    return (f"2024-02-01,2024-02-05,Buy,SYN,{desc},5,10.00,-50.00,0,"
            f"-50.00,CAD,Trades,{acct},Margin\n")


def tjs(*args, cwd=REPO_ROOT):
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run", list(args)),
        cwd=cwd, capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL, timeout=600)


class TestTJ0249NoPlaceholderIsAnInputId(unittest.TestCase):
    """An export holding U1234567 and U9990001 mapped the first to
    U9990001, and the second id's replacement then rewrote that
    placeholder too: both cells became U9990002, two accounts one."""

    def _distinct(self, src, ids):
        out, rep = R.redact_text(src)
        phs = [rep.accounts[i] for i in ids]
        self.assertEqual(len(set(phs)), len(ids), rep.accounts)
        for i, ph in zip(ids, phs):
            # Never ANOTHER id of the input (a placeholder-shaped id may
            # stay itself, as a redacted copy redacted again does).
            self.assertNotIn(ph.upper(), {x.upper() for x in ids} - {i})
            self.assertEqual(out.count(ph), 1, out)
        return out, rep

    def test_one_row_two_accounts(self):
        out, rep = self._distinct("Account,Xfer Account,Symbol,Quantity\n"
                                  "U1234567,U9990001,SYN,1\n",   # pii-ok
                                  ["U1234567", "U9990001"])      # pii-ok
        self.assertNotIn("U1234567", out)                        # pii-ok
        self.assertEqual(rep.unreplaced, {})
        self._distinct("Account,Xfer Account,Symbol,Quantity\n"
                       "U9990001,U1234567,SYN,1\n",          # pii-ok
                       ["U9990001", "U1234567"])             # pii-ok
        self._distinct(QT_HEAD + _qt_row("55500001")         # pii-ok
                       + _qt_row("99900001"),                # pii-ok
                       ["55500001", "99900001"])             # pii-ok

    def test_files_sharing_one_table_stay_apart(self):
        table = {}
        a, _ = R.redact_text(QT_HEAD + _qt_row("U1234567"),  # pii-ok
                             known_ids=table)
        b, _ = R.redact_text(QT_HEAD + _qt_row("U9990001")   # pii-ok
                             + _qt_row("U1234567"),          # pii-ok
                             known_ids=table)
        self.assertEqual(len(set(table.values())), 2, table)
        ph_a, ph_b = table["U1234567"], table["U9990001"]    # pii-ok
        self.assertIn(ph_a, a)
        self.assertEqual((b.count(ph_a), b.count(ph_b)), (1, 1), b)

    def test_a_project_copy_keeps_the_accounts_apart(self):
        # Two accounts' files: the sweep over the whole copy rewrote the
        # first file's placeholder as the second file's id.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for acct, ident in (("margin", "55500001"),      # pii-ok
                                ("margin2", "99900001")):    # pii-ok
                d = root / "inputs" / acct
                d.mkdir(parents=True)
                (d / "q.csv").write_text(QT_HEAD + _qt_row(ident))
            r = tjs("-C", str(root), "redact", "--no-denylist")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            texts = [(root / "inputs_redact" / a / "q.csv").read_text()
                     for a in ("margin", "margin2")]
            ids = [t.splitlines()[1].split(",")[12] for t in texts]
            self.assertNotEqual(ids[0], ids[1], texts)
            # The first account's placeholder is neither its id nor the
            # second account's.
            self.assertNotIn(ids[0], ("55500001", "99900001"))   # pii-ok


class TestTJ0248BareFromName(unittest.TestCase):
    """A Description of "FROM SAM SAMPLE" (no verb before FROM) was
    neither replaced nor listed for review: _NAME_FROM_TO needs "wire
    from", "transfer to" ..., and the upper-case run holds "from",
    statement vocabulary."""

    def _review(self, desc):
        out, rep = R.redact_text(QT_HEAD + _qt_row("55500001", desc))  # pii-ok
        return out, [why for _n, why in rep.review]

    def test_a_bare_sender_or_recipient_is_listed(self):
        for desc in ("FROM SAM SAMPLE", "From Sam Sample", "TO SAM SAMPLE",
                     "FROM SAM SAMPLE sam.sample@example.org"):
            with self.subTest(desc=desc):
                out, why = self._review(desc)
                self.assertIn("name-like words after FROM / TO in column "
                              "'Description'", why)
                # Listed, not guessed at: the text stays for the reader.
                self.assertIn(desc.split()[1], out)
                self.assertNotIn("sam.sample@example.org", out)

    def test_statement_words_after_from_are_not(self):
        for desc in ("FROM MARGIN ACCOUNT", "TO USD CASH", "FROM RRSP",
                     "FROM REDACTED"):
            with self.subTest(desc=desc):
                self.assertEqual(self._review(desc)[1], [])

    def test_check_lists_the_line_and_keeps_the_general_note(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "synthetic.redacted.csv"
            f.write_text(QT_HEAD
                         + "2026-01-05,2026-01-05,Deposits,,FROM SAM SAMPLE,"
                         "0,0,50,0,50,CAD,Deposits,99900001,Margin\n")  # pii-ok
            r = tjs("redact", "--no-denylist", "--check", "--details",
                    str(f))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("line 2: name-like words after FROM / TO in column "
                      "'Description'", r.stdout)
        self.assertIn("free-text Description columns (Description) may "
                      "still name people", r.stdout)


if __name__ == "__main__":
    unittest.main()
