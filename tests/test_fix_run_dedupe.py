"""`taxjson run` shows each parser message once (owner request).

With two or more taxable equity accounts the run reads every one of
them first (the transfer evidence pre-pass: a transfer-in that pairs
with another account's transfer-out is not an arrival from outside the
books), then builds each account's books. The books' pass used to parse
every account again under the default full rebuild, printing the
parser's warnings and counts a second time. The second pass now reuses
the parse the first pass made in this run when its command and every
file it reads are unchanged, so each message appears once — and a
message only the first pass can show (a refusal that stops the run)
still shows. Every fixture is synthetic."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STYLE = REPO / "tests" / "fixtures" / "style"
EXAMPLES = REPO / "examples"

_TOML = {
    "canada": """[settings]
base_currency  = "CAD"
country        = "canada"
source_currencies = ["USD"]
tax_date       = "settle"
year           = 2024
option_grant_timing_since = 2024
""",
    "usa": """[settings]
base_currency  = "USD"
country        = "usa"
source_currencies = []
tax_date       = "trade"
year           = 2024
""",
}

# A file the IB detector routes to the IB parser that holds no rows.
_EMPTY_IB = ("Statement,Header,Field Name,Field Value\n"
             "Trades,Header,DataDiscriminator,Asset Category,Currency,"
             "Symbol,Date/Time,Quantity,T. Price,Proceeds,Comm/Fee,Basis,"
             "Realized P/L,Code\n")


def _env():
    e = dict(os.environ)
    e.pop("TAXJSON_WIDTH", None)            # piped: a person's width 100
    e["TAXJSON_OFFLINE"] = "1"
    e["PYTHONPATH"] = str(REPO / "src") + os.pathsep + e.get(
        "PYTHONPATH", "")
    return e


def _messages(text):
    """The console's message blocks (a labelled line and its two-space
    continuations), steps left out."""
    out, cur = [], None
    for ln in text.splitlines():
        if ln.startswith("  ") and cur is not None:
            cur.append(ln)
            continue
        if cur is not None:
            out.append("\n".join(cur))
        cur = None if ln.startswith("==> ") else [ln]
    if cur is not None:
        out.append("\n".join(cur))
    return out


class _Project(unittest.TestCase):
    def make(self, country, b_files=("questrade_demo.csv",),
             extra_accounts=""):
        tmp = tempfile.mkdtemp(prefix="taxjson_dedupe_")
        self.addCleanup(shutil.rmtree, tmp, True)
        root = Path(tmp) / country
        (root / "inputs" / "a").mkdir(parents=True)
        (root / "inputs" / "b").mkdir(parents=True)
        shutil.copy(STYLE / "canada" / "inputs" / "margin" / "ib_demo.csv",
                    root / "inputs" / "a" / "ib_demo.csv")
        for f in b_files:
            if f == "ib_empty.csv":
                (root / "inputs" / "b" / f).write_text(_EMPTY_IB)
            elif country == "usa":
                # A US project has no CAD rates offline: its USD rows.
                lines = (EXAMPLES / f).read_text().splitlines(True)
                (root / "inputs" / "b" / f).write_text("".join(
                    [lines[0]] + [ln for ln in lines[1:]
                                  if ",USD," in ln]))
            else:
                shutil.copy(EXAMPLES / f, root / "inputs" / "b" / f)
        (root / "taxjson.toml").write_text(
            _TOML[country] + '\n[accounts.a]\ntype = "taxable"\n'
            '\n[accounts.b]\ntype = "taxable"\n' + extra_accounts)
        return root

    def run_tj(self, root, *args):
        return subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
             str(root), "run", "--no-input", *args], cwd=root,
            capture_output=True, text=True, env=_env(), timeout=900,
            stdin=subprocess.DEVNULL)


class TestEachParserMessageOnce(_Project):
    def test_two_taxable_accounts(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                root = self.make(country)
                r = self.run_tj(root)
                self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
                text = r.stdout + r.stderr
                # The first pass ran (the case this pins).
                self.assertIn("first pass: transfers between your "
                              "accounts", r.stdout)
                # The parser's ATTENTION warning, and each file's count,
                # exactly once.
                self.assertEqual(text.count(
                    "Warning: ib_demo.csv: the statement has no Cash "
                    "Report"), 1, text)
                self.assertEqual(text.count("Info: ib_demo.csv: 9 tax "
                                            "objects"), 1, text)
                self.assertEqual(len(re.findall(
                    r"(?m)^Info: questrade_demo\.csv: \d+ tax objects$",
                    text)), 1, text)
                # One parse step per account: the books' pass reused
                # the first pass's parse.
                self.assertEqual(len(re.findall(r"(?m)^==> Reading ",
                                                r.stdout)), 2, r.stdout)
                # No message block shown twice, on either stream.
                for stream in (r.stdout, r.stderr):
                    blocks = _messages(stream)
                    dups = sorted({b for b in blocks
                                   if blocks.count(b) > 1})
                    self.assertEqual(dups, [], stream)
                # The account's .diag keeps the warning once, as before.
                diag = (root / "work" / "a_ib.json.diag").read_text()
                self.assertEqual(diag.count("no Cash Report"), 1, diag)

    def test_reused_parse_is_the_same_file(self):
        """`run` (first pass reused) and `run --account a` (no first
        pass: one account named) write the same parse and .diag."""
        root = self.make("canada")
        self.assertEqual(self.run_tj(root).returncode, 0)
        both = {p: (root / "work" / p).read_bytes()
                for p in ("a_ib.json", "a_ib.json.diag",
                          "a_ib_transfers.json", "b_questrade.json")
                if (root / "work" / p).exists()}
        self.assertIn("a_ib.json.diag", both)
        r = self.run_tj(root, "--account", "a")
        self.assertEqual(r.returncode, 0, r.stderr)
        for p, data in both.items():
            self.assertEqual((root / "work" / p).read_bytes(), data, p)


class TestCryptoFirstPass(_Project):
    """Two crypto accounts: every one is parsed first so a send pairs
    with its arrival on the other exchange; the books' pass reuses
    those parses."""

    def test_two_crypto_accounts(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                tmp = tempfile.mkdtemp(prefix="taxjson_dedupe_")
                self.addCleanup(shutil.rmtree, tmp, True)
                root = Path(tmp) / country
                src = STYLE / country / "inputs" / "crypto"
                for acct, f in (("cb", "coinbase_demo.csv"),
                                ("kr", "kraken_demo.csv")):
                    (root / "inputs" / acct).mkdir(parents=True)
                    shutil.copy(src / f, root / "inputs" / acct / f)
                zone = ("America/Toronto" if country == "canada"
                        else "America/New_York")
                (root / "taxjson.toml").write_text(
                    _TOML[country] + f'local_timezone = "{zone}"\n'
                    + "".join(f'\n[accounts.{a}]\ntype = "taxable"\n'
                              f"crypto = true\n" for a in ("cb", "kr")))
                r = self.run_tj(root)
                self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
                self.assertIn("first pass: sends between your accounts",
                              r.stdout)
                self.assertEqual(len(re.findall(r"(?m)^==> Reading ",
                                                r.stdout)), 2, r.stdout)
                for stream in (r.stdout, r.stderr):
                    blocks = _messages(stream)
                    dups = sorted({b for b in blocks
                                   if blocks.count(b) > 1})
                    self.assertEqual(dups, [], stream)
                if country == "usa":
                    self.assertEqual(r.stdout.count(
                        "wash-sale rule NOT applied to crypto"), 1)


class TestFirstPassMessagesStillShow(_Project):
    def test_empty_file_warned_once(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                root = self.make(country, b_files=("questrade_demo.csv",
                                                   "ib_empty.csv"))
                r = self.run_tj(root)
                text = r.stdout + r.stderr
                self.assertEqual(text.count(
                    "inputs/b/ib_empty.csv parsed to 0 transactions"), 1,
                    text)

    def test_strict_refusal_in_the_first_pass(self):
        """A refusal the first pass makes stops the run and is shown."""
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                root = self.make(country, b_files=("questrade_demo.csv",
                                                   "ib_empty.csv"))
                r = self.run_tj(root, "--strict")
                self.assertNotEqual(r.returncode, 0, r.stdout)
                self.assertIn("first pass", r.stdout)
                self.assertNotIn("==> b  (taxable)\n", r.stdout)
                self.assertEqual(r.stderr.count(
                    "inputs/b/ib_empty.csv parsed to 0 transactions"), 1,
                    r.stderr)

    def test_account_without_inputs_warned_once(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                root = self.make(country, extra_accounts=(
                    '\n[accounts.c]\ntype = "taxable"\n'))
                (root / "inputs" / "c").mkdir()
                r = self.run_tj(root)
                text = r.stdout + r.stderr
                self.assertEqual(text.count("skipping account 'c'"), 1,
                                 text)


if __name__ == "__main__":
    unittest.main()
