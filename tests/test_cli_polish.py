"""CLI polish (owner requests, 2026-10):

  1. `taxjson` (or `tjs -C DIR`) with no command prints the help page
     and exits 0.
  2. The help page groups the subcommands by what they are for; every
     registered subcommand sits in exactly one group, and the README's
     command table uses the same groups in the same order. Inside a
     project, the other country's commands are hidden (`help --all`
     lists them, marked).
  3. `tjs` is the same program as `taxjson` (usage shows the name it was
     invoked as).
  4. `tjs stats`: win/lose statistics on closed trades per asset
     class, economic P/L before any superficial-loss / wash-sale denial.

All data is synthetic.
"""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
from _style import CapturedWidth
from _support.cli_help_project import REPO_ROOT, _env, _cli, _project


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _as(name, *args, cwd=None):
    """Run the CLI as if invoked through the console script `name`."""
    code = ("import sys; sys.argv = [%r] + %r; "
            "from taxjson.bin.taxjson_run import main; main()"
            % (f"/usr/local/bin/{name}", list(args)))
    return subprocess.run([sys.executable, "-c", code], cwd=cwd or REPO_ROOT,
                          capture_output=True, text=True, env=_env(),
                          stdin=subprocess.DEVNULL, timeout=120)


def _listed(help_text):
    """Command names the help page lists, in order."""
    return re.findall(r"^  ([a-z][a-z0-9-]+)(?:\s|$)", help_text, re.M)


# ------------------------------------------------------------ 1. no command
class TestNoCommandPrintsHelp(unittest.TestCase):
    def test_bare_command_is_help_rc0(self):
        r = _cli()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Set up:", r.stdout)
        self.assertEqual(r.stderr, "")

    def test_dir_only_is_help_rc0(self):
        with tempfile.TemporaryDirectory() as d:
            for args in (["-C", d], ["--dir", d], [f"--dir={d}"]):
                r = _cli(*args)
                self.assertEqual(r.returncode, 0, (args, r.stderr))
                self.assertIn("Summaries:", r.stdout)

    def test_usage_errors_unchanged(self):
        r = _cli("-C")
        self.assertEqual(r.returncode, 2)
        self.assertIn("expected one argument", r.stderr)
        r = _cli("no-such-command")
        self.assertEqual(r.returncode, 2)
        self.assertIn("invalid choice", r.stderr)


# ---------------------------------------------------------------- 2. groups
class TestGroupedHelp(unittest.TestCase):
    def test_every_command_in_exactly_one_group(self):
        from taxjson.cli.parser import _COMMAND_GROUPS, _build_parser
        _p, sub = _build_parser()
        grouped = [n for _t, names in _COMMAND_GROUPS for n in names]
        dupes = sorted({n for n in grouped if grouped.count(n) > 1})
        self.assertEqual(dupes, [], "a command in two groups")
        missing = sorted(set(sub.choices) - set(grouped))
        self.assertEqual(missing, [], "registered but in no group")

    def test_help_lists_each_command_once_under_its_heading(self):
        from taxjson.cli.parser import _COMMAND_GROUPS, _build_parser
        _p, sub = _build_parser()
        with tempfile.TemporaryDirectory() as d:       # outside a project
            out = _cli("--help", cwd=d).stdout
        listed = _listed(out)
        self.assertEqual(sorted(listed), sorted(sub.choices))
        self.assertEqual(len(listed), len(set(listed)))
        heads = [t for t, _n in _COMMAND_GROUPS]
        pos = [out.index(f"\n{t}:\n") for t in heads]
        self.assertEqual(pos, sorted(pos))
        self.assertNotIn("Other commands", out)
        # Each command sits under its own group's heading.
        for i, (title, names) in enumerate(_COMMAND_GROUPS):
            opts = "\noptions:"                 # argparse's heading
            end = pos[i + 1] if i + 1 < len(pos) else out.index(opts)
            section = out[pos[i]:end]
            self.assertEqual(
                [n for n in _listed(section)],
                [n for n in names if n in sub.choices], title)

    def test_readme_table_uses_the_same_groups_in_order(self):
        from taxjson.cli.parser import _COMMAND_GROUPS, _build_parser
        _p, sub = _build_parser()
        text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        start = text.index("### Subcommands")
        end = text.index("\n### ", start + 5) if "\n### " in text[
            start + 5:] else len(text)
        block = text[start:end]
        got = []
        for m in re.finditer(r"^#### (.+)$", block, re.M):
            nxt = block.find("\n#### ", m.end())
            body = block[m.end(): nxt if nxt != -1 else len(block)]
            names = []
            for row in re.findall(r"^\| (.+?) \|", body, re.M):
                for tok in re.findall(r"`([^`]+)`", row):
                    w = tok.replace("tjs ", "", 1).split()[0]
                    if w in sub.choices and w not in names:
                        names.append(w)
            got.append((m.group(1).strip(), names))
        want = [(t, [n for n in names if n in sub.choices])
                for t, names in _COMMAND_GROUPS]
        self.assertEqual(got, want)


class TestCountryAwareHelp(unittest.TestCase):
    ONE_COUNTRY = ("t1135", "instalments", "option-boundary", "amt",
                   "slip-audit")

    def _project(self, d, country):
        base = "USD" if country == "usa" else "CAD"
        Path(d, "taxjson.toml").write_text(
            f'[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\ncountry = "{country}"\n'
            f'base_currency = "{base}"\n[accounts.margin]\n'
            f'type = "taxable"\n')

    def test_usa_project_hides_canadian_commands(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d, "usa")
            out = _cli("-C", d).stdout
            listed = _listed(out)
            for c in self.ONE_COUNTRY:
                self.assertNotIn(c, listed)
            self.assertIn("6 commands hidden for USA — `tjs help "
                          "--all` lists every command.",
                          " ".join(out.split()))
            self.assertIn("form-export", listed)     # generic, stays
            # -h and `help` show the same page.
            self.assertEqual(_cli("-C", d, "-h").stdout, out)
            self.assertEqual(_cli("-C", d, "help").stdout, out)
            full = _cli("-C", d, "help", "--all").stdout
            for c in self.ONE_COUNTRY:
                self.assertIn(c, _listed(full))
            self.assertRegex(full, r"\n  t1135 +\(Canada\) ")
            self.assertNotIn("hidden", full)
            # A hidden command still gets the dispatcher's refusal.
            r = _cli("-C", d, "t1135")
            self.assertEqual(r.returncode, 1)
            self.assertIn("is Canada-only", r.stderr)

    def test_canada_project_lists_everything_unmarked(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d, "canada")
            out = _cli("-C", d).stdout
            for c in self.ONE_COUNTRY:
                self.assertIn(c, _listed(out))
            self.assertNotRegex(out, r"\n  [a-z0-9-]+ +\((Canada|USA)\) ")
            self.assertNotIn("hidden", out)

    def test_outside_a_project_marks_one_country_commands(self):
        with tempfile.TemporaryDirectory() as d:
            out = _cli(cwd=d).stdout
            for c in self.ONE_COUNTRY:
                self.assertIn(c, _listed(out))
            self.assertRegex(out, r"\n  instalments +\(Canada\) ")
            self.assertNotIn("hidden", out)


# ------------------------------------------------------------------ 3. tjs
class TestTjsAlias(unittest.TestCase):
    def test_console_scripts(self):
        text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        target = '"taxjson.bin.taxjson_run:main"'
        self.assertRegex(text, rf"(?m)^taxjson = {re.escape(target)}$")
        self.assertRegex(text, rf"(?m)^tjs = {re.escape(target)}$")

    def test_usage_says_tjs_however_it_was_invoked(self):
        r = _as("tjs", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("usage: tjs "), r.stdout[:80])
        r = _as("tjs", "stats", "-h")
        self.assertTrue(r.stdout.startswith("usage: tjs stats "),
                        r.stdout[:80])
        r = _as("tjs", "no-such-command")
        self.assertEqual(r.returncode, 2)
        self.assertIn("tjs: error", r.stderr)
        r = _as("taxjson", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("usage: tjs "), r.stdout[:80])

    def test_docs_and_installer_mention_it(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("`tjs`", readme)
        inst = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('"$BIN/tjs"', inst)


# ---------------------------------------------------------------- 4. stats
class TestStats(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.grant = _project(cls._tmp.name, "grant",
                             extra="option_grant_timing_since = 2025\n")
        cls.close = _project(cls._tmp.name, "close",
                             extra='option_premium_timing = "close"\n')
        for root in (cls.grant, cls.close):
            r = _cli("-C", str(root), "run", "--no-input")
            assert r.returncode == 0, r.stderr[-3000:]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _json(self, root, *args):
        r = _cli("-C", str(root), "stats", *args, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    @rule(R.CA_RPT_16)
    def test_each_class(self):
        doc = self._json(self.grant)
        c = doc["classes"]
        # Long shares: the assignment sale (premium taken back out: 200 x
        # (45 - 40)) and the superficial loss counted in full.
        self.assertEqual(c["long-shares"]["trades"], 2)
        self.assertEqual(c["long-shares"]["net_pl"], 800.0)
        self.assertEqual(c["long-shares"]["largest_loss"], -200.0)
        self.assertEqual(c["short-shares"]["net_pl"], 500.0)
        self.assertEqual((c["long-options"]["wins"],
                          c["long-options"]["losses"]), (1, 1))
        self.assertEqual(c["futures"]["net_pl"], 2000.0)
        self.assertEqual(c["crypto"]["net_pl"], 5000.0)
        w = c["written-options"]
        # 990 bought back, 495 expired, 600 assigned, -200 partial
        # buy-back, 600 expired rest — one trade per closing row.
        self.assertEqual((w["trades"], w["wins"], w["losses"]), (5, 4, 1))
        self.assertEqual(w["net_pl"], 2485.0)
        self.assertEqual(w["largest_win"], 990.0)
        self.assertEqual(w["avg_loss"], -200.0)
        self.assertEqual(w["profit_factor"], round(2685.0 / 200.0, 4))
        self.assertEqual(w["win_rate"], 0.8)
        t = doc["total"]
        self.assertEqual(t["trades"], 12)
        self.assertEqual(t["net_pl"], 10785.0)
        # The denial is reported apart, never subtracted.
        self.assertEqual(doc["denied_total"], 100.0)
        self.assertEqual(doc["denied_count"], 1)
        self.assertEqual(doc["assigned_written_options"], 1)
        self.assertEqual(doc["accounts"], ["coins", "margin"])
        self.assertFalse(doc["sheltered"])
        self.assertEqual(doc["currency"], "CAD")
        self.assertIn("before any superficial-loss denial", doc["basis"])
        self.assertEqual(c["short-shares"]["profit_factor"], None)

    @rule(R.CA_RPT_16)
    def test_same_stats_under_either_premium_timing(self):
        g, c = self._json(self.grant), self._json(self.close)
        self.assertEqual(g["classes"], c["classes"])
        self.assertEqual(g["total"], c["total"])
        self.assertEqual((g["option_premium_timing"],
                          c["option_premium_timing"]), ("grant", "close"))

    def test_text_report(self):
        r = _cli("-C", str(self.grant), "stats")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn("CLOSED-TRADE STATISTICS — tax year 2026", out)
        self.assertIn("before any superficial-loss denial", out)
        self.assertNotIn("wash", out.lower())
        for cls in ("long-shares", "short-shares", "long-options",
                    "written-options", "futures", "crypto", "TOTAL"):
            self.assertRegex(out, rf"\n{cls} +\d")
        self.assertIn("Denied by the superficial-loss rule (NOT "
                      "subtracted above): 100.00 CAD", out)
        self.assertRegex(out, r"written-options +5 +4 +1 +80\.0% +2,485\.00")

    def test_sheltered_only_when_named(self):
        doc = self._json(self.grant, "tfsa")
        self.assertTrue(doc["sheltered"])
        self.assertEqual(doc["accounts"], ["tfsa"])
        self.assertEqual(doc["total"]["net_pl"], 20.0)
        self.assertNotIn("tfsa", self._json(self.grant)["accounts"])
        out = _cli("-C", str(self.grant), "stats", "tfsa").stdout
        self.assertIn("registered, not taxable events", out)

    def test_year_and_all_history(self):
        self.assertEqual(self._json(self.grant, "2026")["total"]["trades"],
                         12)
        self.assertEqual(self._json(self.grant, "--all-history")["scope"],
                         "all history")
        self.assertEqual(
            self._json(self.grant, "--all-history", "margin")["accounts"],
            ["margin"])
        r = _cli("-C", str(self.grant), "stats", "2026", "--all-history")
        self.assertEqual(r.returncode, 2)
        r = _cli("-C", str(self.grant), "stats", "nosuch")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no [accounts.nosuch]", r.stderr)

    def test_refuses_without_books(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(d, "fresh")
            r = _cli("-C", str(root), "stats")
            self.assertEqual(r.returncode, 1)
            self.assertIn("run `tjs run` first", r.stderr)
            self.assertNotIn("Traceback", r.stderr)


class TestTradeStatsUnits(unittest.TestCase):
    """lib/trade_stats on hand-made rows."""

    @staticmethod
    def _r(date, sym, qty, net, action="BUYSELL", rid=None, price=None):
        return {"action": action, "date": date, "date_settle": date,
                "time": "10:00:00", "symbol": sym, "quantity": qty,
                "net_amount": net, "account": "m", "id": rid or
                f"{date}{sym}{qty}",
                "price": abs(net / qty) if price is None and qty else
                (price or 0.0)}

    def test_one_close_of_two_writes_is_one_trade(self):
        from taxjson.lib.trade_stats import written_option_trades
        o = "QZQ260320C00050000.US"
        rows = [self._r("2026-01-02", o, -1, 200), self._r("2026-01-05", o,
                                                          -1, 300),
                self._r("2026-02-02", o, 2, 100)]
        tr = written_option_trades(rows, lambda r: r["date"])["trades"]
        self.assertEqual(len(tr), 1)
        self.assertAlmostEqual(tr[0]["pnl"], 400.0)
        self.assertEqual(tr[0]["kind"], "bought back")

    def test_partial_closes_one_trade_each(self):
        from taxjson.lib.trade_stats import written_option_trades
        o = "QZQ260320C00050000.US"
        rows = [self._r("2026-01-02", o, -5, 1000),
                self._r("2026-02-02", o, 2, 600),
                self._r("2026-03-20", o, 3, 0.0, price=0.0)]
        tr = written_option_trades(rows, lambda r: r["date"])["trades"]
        self.assertEqual([(t["kind"], round(t["pnl"], 2)) for t in tr],
                         [("bought back", -200.0), ("expired", 600.0)])

    def test_selling_a_held_option_is_not_a_write(self):
        from taxjson.lib.trade_stats import written_option_trades
        o = "QZQ260320P00050000.US"
        rows = [self._r("2026-01-02", o, 2, 200),
                self._r("2026-02-02", o, -3, 450),     # close 2, write 1
                self._r("2026-03-02", o, 1, 50)]
        tr = written_option_trades(rows, lambda r: r["date"])["trades"]
        self.assertEqual(len(tr), 1)
        self.assertAlmostEqual(tr[0]["pnl"], 100.0)

    def test_put_premium_leaves_the_later_sale(self):
        from taxjson.lib.trade_stats import compute
        o = "QZQ260320P00050000.US"
        book = [self._r("2026-01-02", o, -1, 200),
                self._r("2026-03-20", o, 1, 0.0, action="ASSIGN", price=0.0),
                self._r("2026-03-20", "QZQ.US", 100, 5000, action="ASSIGN"),
                self._r("2026-05-01", "QZQ.US", -100, 5100, rid="sale")]
        gains = {"m": {"transactions": [
            {"id": "sale", "date": "2026-05-01", "symbol": "QZQ.US",
             "qty": 100, "gain": 300.0, "raw_gain": 300.0,
             "direction": "LONG", "account": "m"}]}}
        res = compute(gains, {"m": book}, lambda d: True,
                      lambda r: r["date"])
        self.assertEqual(res["classes"]["written-options"]["net_pl"], 200.0)
        self.assertEqual(res["classes"]["long-shares"]["net_pl"], 100.0)
        self.assertEqual(res["total"]["net_pl"], 300.0)


class TestStatsDualCountry(unittest.TestCase):
    """The same book in a Canadian and a US project: the same trades and
    economic P/L; each country's own rule name for the denial."""

    BOOK = """\
BUYSELL 2026-01-05 10:00:00 ABC.{x} 100 {c} 40.00 4000.00 0.00
BUYSELL 2026-02-02 10:00:00 ABC260320C00050000.{x} -1 {c} 4.00 400.00 0.00
BUYSELL 2026-03-05 10:00:00 ABC260320C00050000.{x} 1 {c} 2.00 200.00 0.00
BUYSELL 2026-04-06 10:00:00 ABC.{x} -100 {c} 45.00 4500.00 0.00
BUYSELL 2026-02-03 10:00:00 LOS.{x} 100 {c} 10.00 1000.00 0.00
BUYSELL 2026-03-03 10:00:00 LOS.{x} -100 {c} 8.00 800.00 0.00
BUYSELL 2026-03-24 10:00:00 LOS.{x} 50 {c} 8.00 400.00 0.00
"""

    @rule(R.CA_RPT_16)
    @rule(R.US_RPT_12)
    def test_both_countries(self):
        with tempfile.TemporaryDirectory() as d:
            docs, outs = {}, {}
            for country, x, c in (("canada", "TO", "CAD"),
                                  ("usa", "US", "USD")):
                root = _project(d, country, country=country, books={
                    "margin": self.BOOK.format(x=x, c=c)})
                r = _cli("-C", str(root), "run", "--no-input")
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                docs[country] = json.loads(
                    _cli("-C", str(root), "stats", "--json").stdout)
                outs[country] = _cli("-C", str(root), "stats").stdout
            ca, us = docs["canada"], docs["usa"]
            self.assertEqual(ca["classes"], us["classes"])
            self.assertEqual(ca["total"]["net_pl"], 500.0 + 200.0 - 200.0)
            self.assertEqual(ca["denied_total"], us["denied_total"])
            self.assertGreater(us["denied_total"], 0)
            self.assertIn("superficial-loss", outs["canada"])
            self.assertNotIn("wash", outs["canada"].lower())
            self.assertIn("wash-sale", outs["usa"])
            self.assertNotIn("superficial", outs["usa"].lower())
            self.assertEqual(us["currency"], "USD")
            self.assertIsNone(us["option_premium_timing"])


if __name__ == "__main__":
    unittest.main()
