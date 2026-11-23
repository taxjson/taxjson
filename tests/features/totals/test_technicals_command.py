"""`tjs technicals` end to end on the demo project (`tjs init --demo`), its
daily history a made-up series per holding written to the project's
history cache (work/.history_cache.json) or handed to the command as an
injected live tier: the default view, --details, the --json document
against its golden copy, a benchmark from the option or the
taxjson.toml key and none, the TAXJSON_OFFLINE refusal, a holding Yahoo
has no history for, a cache served after Yahoo gave nothing or offline
past a week, the benchmark's own lines, blanks the data does not allow,
the missing [fx] extra, and that the key leaves the books and a closed
year as they were. No test reaches the
network: the children run offline, and the in-process runs inject the
tier or hide yfinance.

Regenerate the golden copy (tests/fixtures/technicals/demo.json) after a
change that means to move it:

    TAXJSON_UPDATE_GOLDEN=technicals python -m unittest _hermetic \\
        features.totals.test_technicals_command

All data is synthetic."""
import io
import json
import os
import random
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import _paths
from _style import CapturedWidth, assert_concise
from _support.cli_command_runs import _tjs

GOLDEN = _paths.FIXTURES / "technicals" / "demo.json"
FETCHED = "2025-01-03"           # the day the made-up cache was "fetched"
INDEX = "^ZZQX"                  # a made-up index, the benchmark
NO_QUOTE = "PARNT.US"            # the demo's made-up listing: no history
SHORT = "SAMPG.US"               # the demo's other: 120 sessions, a day old
_WIDTH = CapturedWidth()
_DEMO = {}


def _sessions(last: str, n: int):
    d, out = date.fromisoformat(last), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return out[::-1]


def _series(symbol: str, n: int, last: str = "2025-01-02"):
    """A made-up daily series for `symbol` (the same every run): [day,
    close, high, low, volume] rows."""
    rnd = random.Random(f"technicals:{symbol}")
    close, rows = 20.0 + rnd.random() * 80, []
    for day in _sessions(last, n):
        close *= 1 + rnd.gauss(0.0004, 0.012)
        spread = close * (0.004 + rnd.random() * 0.01)
        rows.append([day, close, close + spread, close - spread,
                     float(rnd.randrange(1000, 90000))])
    return rows


def _held(root: Path):
    """The demo's listed holdings (not options or crypto), by `tjs shares`."""
    from taxjson.lib.option_symbols import is_option_symbol
    from taxjson.lib.price_chain import is_crypto_symbol
    r = _tjs(root, "shares", "--json", "--options")
    assert r.returncode == 0, r.stderr
    return [x["symbol"] for x in json.loads(r.stdout)["rows"]
            if not is_option_symbol(x["symbol"])
            and not is_crypto_symbol(x["symbol"])]


def _cache_doc(symbols):
    from taxjson.lib.price_chain import HISTORY_ADJUSTED, yf_symbol_for
    doc = {}
    for sym in list(symbols) + [INDEX]:
        if sym == NO_QUOTE:
            continue
        rows = (_series(sym, 120, last="2024-12-31") if sym == SHORT
                else _series(sym, 300))
        q = yf_symbol_for(sym)
        doc[f"{sym}|{q}"] = {
            "quote_symbol": q, "fetched": FETCHED, "source": "yfinance",
            "currency": "CAD" if sym.endswith(".TO") or sym == INDEX
            else "USD", "unit": "provider", "adjusted": HISTORY_ADJUSTED,
            "start": "2023-11-30", "bars": rows}
    return doc


def setUpModule():
    _WIDTH.start()
    top = Path(tempfile.mkdtemp(prefix="taxjson-technicals-"))
    _DEMO["top"] = top
    r = _tjs(None, "init", "--demo", str(top / "demo"))
    assert r.returncode == 0, r.stdout + r.stderr
    root = top / "demo" / "2024"
    r = _tjs(root, "run", "--no-input")
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    _DEMO["root"] = root
    _DEMO["held"] = _held(root)
    _DEMO["cache"] = _cache_doc(_DEMO["held"])
    (root / "work" / ".history_cache.json").write_text(
        json.dumps(_DEMO["cache"]), encoding="utf-8")


def tearDownModule():
    _WIDTH.stop()
    shutil.rmtree(_DEMO.get("top", ""), ignore_errors=True)


def _copy(cache: bool = True) -> Path:
    """A copy of the built demo (its own folder), with or without the
    history cache."""
    dst = Path(tempfile.mkdtemp(prefix="taxjson-technicals-")) / "demo"
    shutil.copytree(_DEMO["top"] / "demo", dst, symlinks=True)
    if not cache:
        (dst / "2024" / "work" / ".history_cache.json").unlink()
    return dst / "2024"


def _in_process(root: Path, *argv, fetchers=None, today=FETCHED):
    """cmd_technicals in this process: (exit code, stdout, stderr)."""
    from taxjson.cli.parser import _build_parser
    from taxjson.cli.technicals import cmd_technicals
    args = _build_parser()[0].parse_args(["-C", str(root), "technicals",
                                          *argv])
    out, err, code = io.StringIO(), io.StringIO(), 0
    with redirect_stdout(out), redirect_stderr(err):
        try:
            cmd_technicals(args, fetchers=fetchers, today=today)
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
            if not isinstance(e.code, int) and e.code:
                err.write(str(e.code))
    return code, out.getvalue(), err.getvalue()


def _tier(answers, asked=None):
    def _fetch(pairs, start, end):
        if asked is not None:
            asked.append(dict(pairs))
        return {s: answers[s] for s in pairs if s in answers}
    return _fetch


def _close(a, b, path="$"):
    """Where JSON values `a` and `b` differ (floats to 1e-9), or None."""
    if isinstance(a, float) or isinstance(b, float):
        ok = (isinstance(a, (int, float)) and isinstance(b, (int, float))
              and abs(a - b) <= 1e-9)
        return None if ok else f"{path}: {a!r} != {b!r}"
    if isinstance(a, dict) and isinstance(b, dict):
        if sorted(a) != sorted(b):
            return f"{path}: keys {sorted(a)} != {sorted(b)}"
        for k in a:
            d = _close(a[k], b[k], f"{path}.{k}")
            if d:
                return d
        return None
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path}: {len(a)} items != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            d = _close(x, y, f"{path}[{i}]")
            if d:
                return d
        return None
    return None if a == b else f"{path}: {a!r} != {b!r}"


def _write_cache(root: Path, doc) -> None:
    (root / "work" / ".history_cache.json").write_text(json.dumps(doc),
                                                       encoding="utf-8")


def _answers(skip=()):
    """The made-up cache as a live tier's answers ({symbol: (rows,
    currency)}), but for `skip`."""
    return {k.split("|")[0]: (v["bars"], v["currency"])
            for k, v in _DEMO["cache"].items()
            if k.split("|")[0] not in skip}


def _acts(out: str):
    return [ln for ln in out.splitlines() if ln.startswith("! ")]


def _key(sym: str) -> str:
    return next(k for k in _DEMO["cache"] if k.split("|")[0] == sym)


class TestTheViews(unittest.TestCase):
    def test_default_view(self):
        r = _tjs(_DEMO["root"], "technicals", "--benchmark", INDEX,
                 env_extra={"TAXJSON_WIDTH": "100"})
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn("TECHNICALS — ", out)
        self.assertIn("adjusted for splits and dividends", out)
        self.assertIn(f"from the history cache, fetched {FETCHED}", out)
        self.assertIn(f"Benchmark:  {INDEX} (--benchmark), last close "
                      f"2025-01-02\n", out)
        self.assertIn("Left out:   1 option contract, 2 crypto", out)
        head = next(ln for ln in out.splitlines() if ln.startswith("SYMBOL"))
        self.assertEqual(head.split(), ["SYMBOL", "QTY", "LAST", "DATE",
                                        "SMA20", "SMA50", "SMA200", "RSI",
                                        "BETA", "ALPHA"])
        rows = {ln.split()[0]: ln for ln in out.splitlines()
                if ln.split() and ln.split()[0] in _DEMO["held"]}
        self.assertEqual(set(rows), set(_DEMO["held"]))
        self.assertEqual(rows[NO_QUOTE].split(), [NO_QUOTE, "100"])
        # The short series: its last close a session older, marked; its
        # 200-session average blank.
        self.assertIn("2024-12-31*", rows[SHORT])
        # A last close of the newest day itself is not marked.
        self.assertEqual([s for s, ln in rows.items() if "*" in ln], [SHORT])
        n = len(_DEMO["cache"])
        self.assertEqual(_acts(out), [
            # Offline, the cache (of FETCHED, long before today) serves
            # every series: one line says how old, and names the one
            # with none.
            f"! TAXJSON_OFFLINE set: {n} series cached {FETCHED}, 1 uncached "
            f"({NO_QUOTE}) — unset it to fetch",
            f"! DATE marked *: last close older than 2025-01-02 for 1 symbol "
            f"({SHORT}) — tjs technicals --details",
            # The index is in CAD (_cache_doc): the US listings with a beta.
            "! 3 rows not in CAD, the benchmark's currency: BETA and ALPHA "
            "include the exchange rate's moves"])
        self.assertIn("More: tjs technicals --details", out)
        self.assertNotIn("OTHER STATISTICS", out)
        assert_concise(self, out, r.stderr,
                       legend=["SMA: % from the 20/50/200-close mean",
                               "BETA: cov/var"])
        self.assertIn("Blank: too little history, or not computable.", out)

    def test_details(self):
        r = _tjs(_DEMO["root"], "technicals", "--benchmark", INDEX,
                 "--details")
        self.assertEqual(r.returncode, 0, r.stderr)
        for heading in ("OTHER STATISTICS", "HISTORY", "NOTES",
                        "DEFINITIONS"):
            self.assertIn(f"\n{heading}\n", r.stdout)
        self.assertIn(f"- {SHORT}: blank: 120 closes, too few for SMA200, 1Y, "
                      f"HIGH52, LOW52, VOL, MAXDD; BETA, ALPHA: 119 daily "
                      f"returns on dates the benchmark has too, 252 needed.",
                      r.stdout)
        self.assertIn("- Left out, option contracts: ", r.stdout)
        self.assertIn("- Left out, crypto: ", r.stdout)
        self.assertIn(f"{INDEX} (benchmark)", r.stdout)
        for name in ("LAST", "SMA20, SMA50, SMA200", "RSI", "BETA", "ALPHA",
                     "HIGH52, LOW52", "VOL", "1M, 3M, 1Y", "MAXDD", "ATR14",
                     "AVGVOL20", "Blank"):
            self.assertIn(f"\n- {name}: ", r.stdout)
        self.assertIn("the last 253 closes (252 returns)", r.stdout)
        self.assertNotIn("More: ", r.stdout)

    def test_json_against_its_golden_copy(self):
        from taxjson.lib.json_contract import SCHEMAS
        r = _tjs(_DEMO["root"], "technicals", "--benchmark", INDEX, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["schema_version"], SCHEMAS["technicals"])
        if os.environ.get("TAXJSON_UPDATE_GOLDEN") == "technicals":
            GOLDEN.parent.mkdir(parents=True, exist_ok=True)
            GOLDEN.write_text(json.dumps(doc, indent=1, sort_keys=True)
                              + "\n", encoding="utf-8")
        want = json.loads(GOLDEN.read_text(encoding="utf-8"))
        self.assertIsNone(_close(doc, want))
        rows = {x["symbol"]: x for x in doc["rows"]}
        self.assertEqual(rows[NO_QUOTE]["history"], "offline")
        self.assertEqual(set(rows[NO_QUOTE]["stats"].values()), {None})
        self.assertTrue(rows[SHORT]["stale"])
        self.assertIsNone(rows[SHORT]["stats"]["sma200_pct"])
        self.assertIsNotNone(rows[SHORT]["stats"]["sma50_pct"])
        self.assertEqual(rows[SHORT]["blank"]["sma200_pct"], "history")
        self.assertEqual(rows[SHORT]["blank"]["beta"], "paired")
        self.assertEqual({x["cache_reason"] for x in doc["rows"]
                          if x["history"] == "ok"}, {"offline"})
        self.assertEqual(doc["benchmark"]["cache_reason"], "offline")
        self.assertEqual(doc["left_out"]["options"],
                         ["SAMPW250117C00030000.US"])
        # No figure is a negative zero.
        self.assertNotIn("-0.0,", r.stdout)
        self.assertNotRegex(r.stdout, r"-0\.0\s*[,}\n]")

    def test_the_benchmark_against_itself(self):
        full = sorted(s for s in _DEMO["held"] if s not in (NO_QUOTE, SHORT))
        r = _tjs(_DEMO["root"], "technicals", "--benchmark", full[0],
                 "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = {x["symbol"]: x for x in json.loads(r.stdout)["rows"]}
        self.assertEqual(rows[full[0]]["stats"]["beta"], 1.0)
        self.assertEqual(rows[full[0]]["stats"]["alpha_pct"], 0.0)
        self.assertIsNotNone(rows[full[1]]["stats"]["beta"])

    def test_no_benchmark(self):
        r = _tjs(_DEMO["root"], "technicals", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        self.assertIsNone(doc["benchmark"])
        self.assertEqual({x["stats"]["beta"] for x in doc["rows"]}, {None})
        self.assertEqual({x["stats"]["alpha_pct"] for x in doc["rows"]},
                         {None})
        self.assertEqual({x["blank"].get("beta") for x in doc["rows"]
                          if x["history"] == "ok"}, {"no-benchmark"})
        self.assertIn('no benchmark (`benchmark = "SYMBOL"` under '
                      '[settings], or --benchmark): BETA and ALPHA blank',
                      r.stderr)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
        # The key to set is shown whole at any width.
        for width in ("100", "60"):
            with self.subTest(width=width):
                t = _tjs(_DEMO["root"], "technicals",
                         env_extra={"TAXJSON_WIDTH": width})
                self.assertEqual(t.returncode, 0, t.stderr)
                self.assertNotIn("BETA", t.stdout)
                self.assertTrue(t.stderr.startswith("Info: no benchmark"),
                                t.stderr)
                self.assertIn('`benchmark = "SYMBOL"`',
                              " ".join(t.stderr.split()))
                self.assertIn("--benchmark", t.stderr)
                if width == "100":
                    assert_concise(self, t.stdout, t.stderr)

    def test_the_settings_key(self):
        root = _copy()
        cfg = root / "taxjson.toml"
        text = cfg.read_text(encoding="utf-8")
        self.assertEqual(text.count("\n[settings]\n"), 1)
        cfg.write_text(text.replace("\n[settings]\n",
                                    f'\n[settings]\nbenchmark = "{INDEX}"\n'),
                       encoding="utf-8")
        r = _tjs(root, "technicals", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["benchmark"]["symbol"], INDEX)
        self.assertEqual(doc["benchmark"]["given_by"], "[settings] benchmark")
        self.assertNotIn("no benchmark", r.stderr)
        # The option wins over the key.
        full = sorted(s for s in _DEMO["held"] if s not in (NO_QUOTE, SHORT))
        r = _tjs(root, "technicals", "--json", "--benchmark", full[0])
        self.assertEqual(json.loads(r.stdout)["benchmark"]["symbol"], full[0])

    def test_a_benchmark_with_no_history(self):
        r = _tjs(_DEMO["root"], "technicals", "--benchmark", "^ZZQY",
                 env_extra={"TAXJSON_WIDTH": "100"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Benchmark:  ^ZZQY (--benchmark): no daily history, "
                      "so BETA and ALPHA are blank", r.stdout)
        self.assertNotIn("BETA: cov/var", r.stdout)
        head = next(ln for ln in r.stdout.splitlines()
                    if ln.startswith("SYMBOL"))
        self.assertEqual(head.split()[-1], "RSI")
        # Offline: named with the holdings TAXJSON_OFFLINE left without.
        self.assertIn(f"! TAXJSON_OFFLINE set: {len(_DEMO['cache']) - 1} "
                      f"series cached {FETCHED}, 2 uncached ({NO_QUOTE} +1 "
                      f"more) — unset it to fetch", _acts(r.stdout))
        d = _tjs(_DEMO["root"], "technicals", "--benchmark", "^ZZQY",
                 "--details")
        self.assertIn("^ZZQY (benchmark)", d.stdout)
        j = json.loads(_tjs(_DEMO["root"], "technicals", "--benchmark",
                            "^ZZQY", "--json").stdout)
        self.assertEqual(j["benchmark"]["history"], "offline")
        self.assertEqual({x["stats"]["beta"] for x in j["rows"]}, {None})

    def test_a_benchmark_a_month_behind(self):
        """A benchmark whose series ends a month before the rows': the
        header shows its last close, marked, and one line says BETA and
        ALPHA end there."""
        root = _copy()
        doc = dict(_DEMO["cache"])
        doc["^ZZQM|^ZZQM"] = dict(doc[_key(INDEX)], quote_symbol="^ZZQM",
                                  bars=_series("^ZZQM", 280,
                                               last="2024-12-02"))
        _write_cache(root, doc)
        r = _tjs(root, "technicals", "--benchmark", "^ZZQM",
                 env_extra={"TAXJSON_WIDTH": "100"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Benchmark:  ^ZZQM (--benchmark), last close "
                      "2024-12-02*\n", r.stdout)
        self.assertIn("! BETA and ALPHA end on 2024-12-02: the benchmark's "
                      "last close is older than the rows' (2025-01-02)",
                      _acts(r.stdout))
        j = json.loads(_tjs(root, "technicals", "--benchmark", "^ZZQM",
                            "--json").stdout)
        self.assertEqual(j["benchmark"]["last_date"], "2024-12-02")
        self.assertTrue(any(x["stats"]["beta"] is not None
                            for x in j["rows"]))

    def test_a_bare_word_benchmark_is_refused(self):
        r = _tjs(_DEMO["root"], "technicals", "--benchmark", "ZZQX")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--benchmark ZZQX: not a listing taxjson can spell",
                      r.stderr)
        self.assertEqual(r.stdout, "")

    def test_no_open_positions(self):
        from taxjson.cli import technicals as TC
        empty = ([], {k: [] for k in TC.LEFT_OUT})
        with mock.patch.object(TC, "_holdings", return_value=empty):
            code, out, err = _in_process(_DEMO["root"], "--benchmark",
                                         INDEX)
            self.assertEqual(code, 0, err)
            self.assertEqual(out.strip().splitlines()[-1],
                             "No open positions.")
            self.assertEqual(len(out.strip().splitlines()), 2, out)
            self.assertNotIn("Closes:", out)
            self.assertNotIn("SMA:", out)
            self.assertNotIn("SYMBOL", out)
            self.assertNotIn("Benchmark:", out)
            self.assertNotIn("benchmark", err)
            code, out, err = _in_process(_DEMO["root"], "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["rows"], [])


class TestBlanksTheDataDoesNotAllow(unittest.TestCase):
    def test_a_flat_series_without_high_low_or_volume(self):
        """A listing whose close never moves, with no high, low or volume:
        RSI, ATR14 and AVGVOL20 are blank and NOTES says why; as the
        benchmark, its returns do not vary, so every BETA is blank."""
        root = _copy()
        doc = dict(_DEMO["cache"])
        flat = "BCE.TO"
        doc[_key(flat)] = dict(doc[_key(flat)], bars=[
            [b[0], 50.0, None, None, None] for b in doc[_key(flat)]["bars"]])
        _write_cache(root, doc)
        r = _tjs(root, "technicals", "--benchmark", INDEX, "--details")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"- {flat}: blank: RSI: no close moved; ATR14: fewer "
                      f"than 15 sessions in a row with a high and a low; "
                      f"AVGVOL20: a session of the last 20 has no volume.",
                      r.stdout)
        j = json.loads(_tjs(root, "technicals", "--benchmark", INDEX,
                            "--json").stdout)
        row = next(x for x in j["rows"] if x["symbol"] == flat)
        self.assertEqual(row["blank"], {"rsi14": "flat",
                                        "atr14_pct": "no-high-low",
                                        "avg_volume_20": "no-volume"})
        self.assertEqual(row["stats"]["sma20_pct"], 0.0)
        r = _tjs(root, "technicals", "--benchmark", flat, "--details")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("BETA, ALPHA: the benchmark's returns do not vary.",
                      r.stdout)
        j = json.loads(_tjs(root, "technicals", "--benchmark", flat,
                            "--json").stdout)
        self.assertEqual({x["blank"].get("beta") for x in j["rows"]
                          if x["sessions"] > 252},
                         {"flat-benchmark"})


class TestWithoutTheHistory(unittest.TestCase):
    def test_offline_with_no_cache_refuses(self):
        r = _tjs(_copy(cache=False), "technicals")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
        self.assertIn("TAXJSON_OFFLINE is set and work/.history_cache.json "
                      "holds no daily history", r.stderr)

    def test_offline_a_week_old_cache_is_said(self):
        """Offline, a cache entry older than 7 calendar days gets a line
        with its fetch day; a newer one does not."""
        n = len(_DEMO["cache"])
        for today, said in (("2025-01-06", False), ("2025-01-10", False),
                            ("2025-01-11", True)):
            with self.subTest(today=today):
                code, out, err = _in_process(_DEMO["root"], "--benchmark",
                                             INDEX, today=today)
                self.assertEqual(code, 0, err)
                line = (f"! TAXJSON_OFFLINE set: {n} series cached {FETCHED}, "
                        f"1 uncached ({NO_QUOTE}) — unset it to fetch")
                plain = (f"! 1 symbol not in the history cache, "
                         f"TAXJSON_OFFLINE set ({NO_QUOTE}) — unset it to "
                         f"fetch")
                self.assertIn(line if said else plain, _acts(out))

    def test_some_symbols_came_back(self):
        """Yahoo answers for all but two holdings: the one cached is served
        from its older entry, said with the day; the one with none gets the
        ticker.map QUOTE advice (a spelling, since others came back)."""
        root = _copy()
        asked = []
        code, out, err = _in_process(root, "--benchmark", INDEX,
                                     fetchers=[_tier(_answers({"AAPL.US"}),
                                                     asked)],
                                     today="2025-01-10")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(asked[0]), len(_DEMO["held"]) + 1)
        acts = _acts(out)
        self.assertIn(f"! Yahoo gave no answer: 1 series from the cache of "
                      f"{FETCHED} (AAPL.US) — tjs technicals --details", acts)
        self.assertIn(f"! no daily history for {NO_QUOTE} — add a ticker.map "
                      f"line: QUOTE {NO_QUOTE} YAHOO_SYMBOL", acts)
        self.assertIn("Yahoo Finance (2025-01-10) and the history cache, "
                      f"fetched {FETCHED}", out)

    def test_two_symbols_with_none_are_named(self):
        root = _copy()
        doc = dict(_DEMO["cache"])
        del doc[_key("AAPL.US")]
        _write_cache(root, doc)
        code, out, err = _in_process(
            root, fetchers=[_tier(_answers({"AAPL.US"}))], today="2025-01-10")
        self.assertEqual(code, 0, err)
        self.assertIn("! no daily history for 2 symbols (AAPL.US +1 more) — "
                      "ticker.map QUOTE lines: tjs technicals --details",
                      _acts(out))

    def test_nothing_came_back(self):
        """Every symbol asked gave nothing (the network down): the cache
        serves what it holds with its day, and no line suggests a QUOTE
        spelling."""
        root = _copy()
        n = len(_DEMO["cache"])
        for tier in (_tier({}), _raising):
            with self.subTest(tier=tier):
                code, out, err = _in_process(root, "--benchmark", INDEX,
                                             fetchers=[tier],
                                             today="2025-01-10")
                self.assertEqual(code, 0, err)
                self.assertEqual(_acts(out)[0],
                                 f"! Yahoo gave no answer: {n} series cached "
                                 f"{FETCHED}, 1 uncached ({NO_QUOTE}) — "
                                 f"network down?")
                self.assertNotIn("QUOTE", "\n".join(_acts(out)))
                self.assertNotIn("Traceback", err)
        # The tier that raised: one warning line, the exception named.
        self.assertIn("the daily-history tier failed (RuntimeError: "
                      "no route to host)", err)
        self.assertEqual(len([ln for ln in err.splitlines()
                              if "tier failed" in ln]), 1, err)

    def test_the_benchmark_yahoo_has_nothing_for(self):
        """The benchmark has a line of its own: no QUOTE advice for an
        index, nor when nothing came back; a listing's QUOTE line."""
        cases = (("^ZZQY", _answers(), "! no daily history for the benchmark "
                  "^ZZQY from Yahoo Finance — check the index's spelling "
                  "there"),
                 ("ZZQB.TO", _answers(), "! no daily history for the "
                  "benchmark ZZQB.TO — add a ticker.map line: QUOTE ZZQB.TO "
                  "YAHOO_SYMBOL"),
                 ("^ZZQY", {}, "! no daily history for the benchmark ^ZZQY: "
                  "Yahoo gave no answer — network down?"))
        for bench, answers, line in cases:
            with self.subTest(bench=bench, answers=bool(answers)):
                code, out, err = _in_process(_copy(), "--benchmark", bench,
                                             fetchers=[_tier(answers)],
                                             today="2025-01-10")
                self.assertEqual(code, 0, err)
                self.assertIn(line, _acts(out))
                self.assertIn(f"Benchmark:  {bench} (--benchmark): no daily "
                              f"history", out)

    def test_a_cached_benchmark_yahoo_did_not_answer_for(self):
        """The benchmark served from the cache after Yahoo gave nothing for
        it: its last close in the header, BETA from that series, and the
        cache's line naming it; never "no daily history" or a QUOTE line
        (another spelling would change the key, and lose the entry)."""
        n = len(_DEMO["cache"])
        cases = (("ZZQB.TO", _answers()), ("^ZZQY", _answers()),
                 ("^ZZQY", {}))
        for bench, answers in cases:
            with self.subTest(bench=bench, answers=bool(answers)):
                root = _copy()
                doc = dict(_DEMO["cache"])
                doc[f"{bench}|{bench}"] = dict(
                    doc[_key(INDEX)], quote_symbol=bench,
                    bars=_series(bench, 300))
                _write_cache(root, doc)
                code, out, err = _in_process(root, "--benchmark", bench,
                                             fetchers=[_tier(answers)],
                                             today="2025-01-10")
                self.assertEqual(code, 0, err)
                acts = _acts(out)
                self.assertIn(f"Benchmark:  {bench} (--benchmark), last "
                              f"close 2025-01-02\n", out)
                self.assertIn("BETA: cov/var", out)
                joined = "\n".join(acts)
                self.assertNotIn("for the benchmark", joined)
                self.assertNotIn(f"QUOTE {bench}", joined)
                self.assertNotIn("spelling there", joined)
                if answers:
                    self.assertIn(f"! Yahoo gave no answer: 1 series from the "
                                  f"cache of {FETCHED} ({bench}) — tjs "
                                  f"technicals --details", acts)
                else:
                    # INDEX's entry is not asked for: the benchmark's is.
                    self.assertEqual(acts[0], (
                        f"! Yahoo gave no answer: {n} series cached "
                        f"{FETCHED}, 1 uncached ({NO_QUOTE}) — network "
                        f"down?"))

    def test_a_live_fetch_is_cached_with_its_day(self):
        root = _copy(cache=False)
        code, out, err = _in_process(root, "--benchmark", INDEX, "--json",
                                     fetchers=[_tier(_answers())],
                                     today="2025-01-06")
        self.assertEqual(code, 0, err)
        doc = json.loads(out)
        self.assertEqual({x["source"] for x in doc["rows"]
                          if x["history"] == "ok"}, {"yfinance"})
        self.assertEqual({x["cache_reason"] for x in doc["rows"]}, {None})
        self.assertEqual(doc["benchmark"]["fetched"], "2025-01-06")
        cached = json.loads((root / "work" / ".history_cache.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual({v["fetched"] for v in cached.values()},
                         {"2025-01-06"})
        self.assertEqual(len(cached), len(_DEMO["cache"]))
        # The figures are the cached series' figures.
        r = _tjs(_DEMO["root"], "technicals", "--benchmark", INDEX, "--json")
        want = {x["symbol"]: x["stats"] for x in json.loads(r.stdout)["rows"]}
        self.assertIsNone(_close({x["symbol"]: x["stats"]
                                  for x in doc["rows"]}, want))
        # A second run that day asks only for the one with none, and as
        # the others came back today, its line is the spelling's.
        asked = []
        code, out, err = _in_process(root, "--benchmark", INDEX,
                                     fetchers=[_tier({}, asked)],
                                     today="2025-01-06")
        self.assertEqual(code, 0, err)
        self.assertEqual(asked, [{NO_QUOTE: NO_QUOTE.split(".")[0]}])
        self.assertIn(f"! no daily history for {NO_QUOTE} — add a ticker.map "
                      f"line: QUOTE {NO_QUOTE} YAHOO_SYMBOL", _acts(out))
        code, out, err = _in_process(root, "--benchmark", INDEX, "--json",
                                     fetchers=[_tier({})], today="2025-01-06")
        self.assertEqual({x["cache_reason"] for x in json.loads(out)["rows"]
                          if x["history"] == "ok"}, {"today"})

    def _no_extra(self):
        from taxjson.lib import price_chain as PC
        return (mock.patch.dict(os.environ, {"TAXJSON_OFFLINE": "0"}),
                mock.patch.dict(sys.modules, {"yfinance": None}),
                mock.patch.object(PC, "_yf_history_fetcher",
                                  side_effect=AssertionError("asked")))

    def test_the_missing_fx_extra_with_a_cache(self):
        a, b, c = self._no_extra()
        n = len(_DEMO["cache"])
        with a, b, c:
            code, out, err = _in_process(_DEMO["root"], "--benchmark", INDEX,
                                         "--details", today="2026-01-05")
        self.assertEqual(code, 0, err)
        self.assertIn(f"! no [fx] extra: {n} series cached {FETCHED}, 1 "
                      f"uncached ({NO_QUOTE}) — tjs technicals --details",
                      _acts(out))
        self.assertIn("TAXJSON_EXTRAS=fx", out)

    def test_the_missing_fx_extra_with_no_cache(self):
        a, b, c = self._no_extra()
        with a, b, c:
            code, out, err = _in_process(_copy(cache=False))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("daily history needs Yahoo Finance (the [fx] extra), "
                      "which is not installed", err)
        self.assertIn("TAXJSON_EXTRAS=fx", err)


def _raising(pairs, start, end):
    raise RuntimeError("no route to host")


class TestTheKeyIsNoRunInput(unittest.TestCase):
    """`[settings] benchmark` is read by `tjs technicals` only: setting it
    leaves the books fresh and a closed year's frozen inputs unchanged
    (lib/checklist _PLANNING_SETTINGS)."""

    @staticmethod
    def _project():
        root = Path(tempfile.mkdtemp(prefix="taxjson-technicals-key-")) / "p"
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            '[accounts.margin]\ntype = "taxable"\n', encoding="utf-8")
        (root / "inputs" / "margin" / "questrade.csv").write_text(
            "Transaction Date,Settlement Date,Action,Symbol,Description,"
            "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
            "Account #,Activity Type,Account Type\n"
            "2025-01-15 09:30:00 AM,2025-01-16 12:00:00 AM,Buy,ZZQA.TO,D,"
            "100,10.00,1000.00,0.00,-1000.00,CAD,1,Trades,Individual\n"
            "2025-06-20 10:15:00 AM,2025-06-23 12:00:00 AM,Sell,ZZQA.TO,D,"
            "-60,15.00,900.00,0.00,900.00,CAD,1,Trades,Individual\n",
            encoding="utf-8")
        return root

    @staticmethod
    def _set_key(root: Path):
        cfg = root / "taxjson.toml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace(
            "[settings]\n", f'[settings]\nbenchmark = "{INDEX}"\n', 1),
            encoding="utf-8")

    def test_the_books_stay_fresh(self):
        root = self._project()
        self.addCleanup(shutil.rmtree, root.parent, True)
        r = _tjs(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self._set_key(root)
        r = _tjs(root, "list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("changed", r.stderr)
        self.assertNotIn("changed", r.stdout)
        self.assertEqual(_tjs(root, "sum").returncode, 0)

    def test_a_closed_year_stays_ok(self):
        root = self._project()
        self.addCleanup(shutil.rmtree, root.parent, True)
        self.assertEqual(_tjs(root, "run", "--no-input").returncode, 0)
        r = _tjs(root, "close-year", "--yes")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self._set_key(root)
        r = _tjs(root, "check-filed")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("filed 2025: OK", r.stdout)
        r = _tjs(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertNotIn("frozen", r.stdout + r.stderr)
        r = _tjs(root, "check-filed")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("frozen", r.stdout + r.stderr)


    def test_the_checklist_verdicts_stay_fresh(self):
        """No check reads the key either (lib/checklist
        _VIEW_ONLY_SETTINGS): `tjs checklist` keeps its verdicts; a
        planning key a check reads (province) re-asks them."""
        root = self._project()
        self.addCleanup(shutil.rmtree, root.parent, True)
        self.assertEqual(_tjs(root, "run", "--no-input").returncode, 0)
        _tjs(root, "checklist", "--run")
        self._set_key(root)
        r = _tjs(root, "checklist")
        self.assertIn("Checked ", r.stdout)
        self.assertNotIn("Since then", r.stdout)
        cfg = root / "taxjson.toml"
        cfg.write_text(cfg.read_text(encoding="utf-8").replace(
            "year = 2025\n", 'year = 2025\nprovince = "ON"\n', 1),
            encoding="utf-8")
        r = _tjs(root, "checklist")
        self.assertIn("Since then the inputs changed (taxjson.toml (planning "
                      "settings))", r.stdout)

    def test_the_digests_do_not_see_the_key(self):
        """With the key set or not, the run's and the checks' digests of
        taxjson.toml are the same (and so are a project's from before the
        key, which has none)."""
        from taxjson.lib import checklist as C
        top = Path(tempfile.mkdtemp(prefix="taxjson-technicals-digest-"))
        self.addCleanup(shutil.rmtree, top, True)
        base = ('[settings]\nyear = 2025\ncountry = "canada"\n'
                'province = "ON"\nholdings_dir = "holdings"\n'
                '[estimate]\nother_income = 100\n'
                '[accounts.margin]\ntype = "taxable"\n')
        digests = []
        for text in (base, base.replace("[settings]\n", '[settings]\n'
                                        f'benchmark = "{INDEX}"\n')):
            cfg = top / f"{len(digests)}.toml"
            cfg.write_text(text, encoding="utf-8")
            digests.append((C._run_config_digest(cfg),
                            C._planning_config_digest(cfg)))
        self.assertEqual(digests[0], digests[1])
        self.assertIsNotNone(digests[0][1])


class TestWiring(unittest.TestCase):
    def test_stale_books_warn_as_list_does(self):
        from taxjson.cli.main import _RUN_STATE_BANNER_CMDS
        self.assertIn("list", _RUN_STATE_BANNER_CMDS)
        self.assertIn("technicals", _RUN_STATE_BANNER_CMDS)

    def test_the_settings_key_is_checked(self):
        from taxjson.lib.config_check import settings_problems
        for bad in (5, "", "XYZQ.TO ABCX.TO", ["XYZQ.TO"]):
            with self.subTest(value=bad):
                probs = settings_problems(
                    {"settings": {"country": "canada", "benchmark": bad}})
                self.assertTrue(any("[settings] benchmark must be one symbol"
                                    in p for p in probs), probs)
        cfg = {"settings": {"country": "canada", "benchmark": " XYZQ.TO "}}
        self.assertEqual(settings_problems(cfg), [])
        self.assertEqual(cfg["settings"]["benchmark"], "XYZQ.TO")

    def test_an_act_line_names_its_first_item(self):
        from taxjson.cli.technicals import _act_named
        line = _act_named("x" * 80, ["ZZQA.TO", "ZZQB.TO"], "y" * 10)
        self.assertIn("(ZZQA.TO +1 more)", line)
        self.assertIn("(ZZQA.TO, ZZQB.TO)", _act_named("x", ["ZZQA.TO",
                                                             "ZZQB.TO"], "y"))

    def test_a_close_of_the_newest_day_is_not_marked(self):
        from taxjson.cli.technicals import _older
        self.assertFalse(_older("2025-01-02", "2025-01-02"))
        self.assertTrue(_older("2025-01-01", "2025-01-02"))
        self.assertFalse(_older(None, "2025-01-02"))
        self.assertFalse(_older("2025-01-02", None))
        code, out, err = _in_process(_DEMO["root"], "--benchmark", INDEX,
                                     "--json")
        self.assertEqual(code, 0, err)
        doc = json.loads(out)
        self.assertEqual([x["symbol"] for x in doc["rows"] if x["stale"]],
                         [SHORT])
        self.assertEqual(doc["latest_close_date"], "2025-01-02")

    def test_no_negative_zero_in_json(self):
        import math
        from taxjson.cli.technicals import _num
        for v in (-0.0, -0.00001, -1e-12):
            with self.subTest(v=v):
                self.assertEqual(math.copysign(1.0, _num(v, 4)), 1.0)
        self.assertEqual(_num(-0.5, 4), -0.5)
        self.assertIsNone(_num(None, 4))


if __name__ == "__main__":
    unittest.main()
