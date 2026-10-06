"""Style smoke helper for the output-style work (docs/output-style.md).

Builds the synthetic style projects in tests/fixtures/style/{canada,usa}
once per test process — options, superficial losses (one re-bought in a
registered account), a spin-off (elected), crypto with a send, dividends,
transfers, a sale with no purchase, a T5008/1099-B slip, a holdings file,
a price cache — and runs commands on them as a person piping the output
would (width 120, lib/out.WIDTH; TAXJSON_OFFLINE=1, stdin /dev/null).

    from _style import project, assert_styled

    class TestHarvestStyle(unittest.TestCase):
        def test_layout(self):
            r = project("canada").run("harvest", "--no-ibkr")
            self.assertEqual(r.returncode, 0, r.stderr)
            assert_styled(self, r.stdout)                  # stdout
            assert_styled(self, r.stderr, allow=("taxjson elect ",))

`project("canada", pending=True)` is a copy stopped at the pending
spin-off election (for `elect --pending`, the run's exit 3). Every
fixture is synthetic, amounts under 1,000 where a document shows them.
"""
import atexit
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "style"

# The default elections the builder saves (the countries' taxable ones).
_ELECTION = {"canada": "taxable_deemed_dividend",
             "usa": "taxable_distribution_301"}
# Synthetic current prices for harvest / scan / watch (asof today).
_PRICES = {"QZQ": 22.0, "SAMPA": 31.0, "SAMPB": 10.0, "NVDA": 120.0,
           "MSFT": 420.0, "PARN": 45.0, "SPNC": 30.0, "XYZQ": 55.0,
           "OLDCO": 18.0, "GHOSTQ": 14.0}
_CRYPTO = {"BTC": 90000.0, "ETH": 3000.0}

_built = {}
_tmp = None


def env(**extra) -> dict:
    """The environment a style test runs taxjson in: this checkout's
    code, offline, no TAXJSON_WIDTH (piped = width 120, lib/out.WIDTH)."""
    e = dict(os.environ)
    e.pop("TAXJSON_WIDTH", None)
    e["TAXJSON_OFFLINE"] = "1"
    e["PYTHONPATH"] = str(SRC) + os.pathsep + e.get("PYTHONPATH", "")
    e.update({k: str(v) for k, v in extra.items()})
    return e


class Project:
    def __init__(self, root: Path, country: str):
        self.root = root
        self.country = country

    def run(self, *args, stdin=None, **env_extra):
        """`taxjson -C <project> <args>` run from the project folder
        (relative paths such as inputs/slips/t5008.csv work): a
        CompletedProcess (text)."""
        return subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
             str(self.root), *args], cwd=self.root, capture_output=True,
            text=True, env=env(**env_extra), timeout=900,
            stdin=subprocess.DEVNULL if stdin is None else stdin)


def _check(r, what):
    if r.returncode not in (0, 3):
        raise RuntimeError(f"style project: {what} exited {r.returncode}:"
                           f"\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
    return r


def _price_cache(p: Project) -> None:
    today = datetime.date.today().isoformat()
    sfx, cur = (".TO", "CAD") if p.country == "canada" else (".US", "USD")
    doc = {f"{s}{sfx}": {"price": v, "asof": today, "source": "yfinance",
                         "currency": cur} for s, v in _PRICES.items()}
    doc.update({s: {"price": v, "asof": today, "source": "yfinance",
                    "currency": cur} for s, v in _CRYPTO.items()})
    # The written call still open in the books (harvest --options).
    doc[f"QZQ250117C00040000{sfx}"] = {"price": 0.05, "asof": today,
                                       "source": "ibkr", "currency": cur}
    (p.root / "work").mkdir(exist_ok=True)
    (p.root / "work" / ".price_cache.json").write_text(json.dumps(doc))


def _build(country: str, pending: bool) -> Project:
    global _tmp
    if _tmp is None:
        _tmp = tempfile.mkdtemp(prefix="taxjson_style_")
        atexit.register(shutil.rmtree, _tmp, True)
    root = Path(_tmp) / f"{country}{'_pending' if pending else ''}"
    shutil.copytree(FIXTURES / country, root)
    p = Project(root, country)
    _check(p.run("run", "--no-input"), "run (pending)")
    if pending:
        return p
    agg = json.loads((root / "work" / "pending_elections.json").read_text())
    for acct, doc in sorted(agg["accounts"].items()):
        for ev in doc["pending"]:
            _check(p.run("elect", acct, "--set",
                         f"{ev['event_id']}={_ELECTION[country]}"),
                   "elect --set")
    if country == "canada":
        # Decide the crypto send (the US project keeps it pending).
        sends = json.loads(p.run("crypto-sends", "--json").stdout or "{}")
        for acct, d in (sends.get("accounts") or {}).items():
            for s in d.get("sends") or []:
                _check(p.run("crypto-sends", acct, "--set",
                             f"{s['id']}=gift", "--note", "synthetic"),
                       "crypto-sends --set")
    r = _check(p.run("run", "--no-input"), "run")
    if r.returncode:
        raise RuntimeError(f"style project: run exited {r.returncode}:\n"
                           f"{r.stderr[-2000:]}")
    _price_cache(p)
    return p


def project(country: str = "canada", pending: bool = False) -> Project:
    """The built synthetic project (built once per process; treat it as
    read-only, or copy p.root first for a command that writes)."""
    key = (country, pending)
    if key not in _built:
        _built[key] = _build(country, pending)
    return _built[key]


# The width a style test's command runs at: piped, lib/out.WIDTH.
PIPE_WIDTH = 120


def assert_styled(tc, text: str, width: int = PIPE_WIDTH, allow=()) -> None:
    """Fail `tc` with the out.lint problems of `text` (lines over
    `width` that are not table rows or contain an `allow` substring,
    blank-line runs, a leading/trailing blank line, retired prefixes — a
    captured lower-case `note:` / `warning:` / `error:` label at the
    start of a line included)."""
    from taxjson.lib.out import lint
    probs = lint(text, width, allow)
    if probs:
        tc.fail("output style:\n  " + "\n  ".join(probs[:20])
                + "\n--- output ---\n" + text[:4000])


def assert_console(tc, text: str, width: int = PIPE_WIDTH,
                   allow=()) -> None:
    """Fail `tc` unless every non-blank line of `text` (the run's
    console, or a command's stderr messages) starts with `==> `,
    `Info: `, `Warning: `, `Error: ` or continues the entry on the line
    above, flush-left; a blank line only (and always) after an entry of
    more than one line, none at the end; no ATTENTION word, no
    detection detail (out.console_lint; docs/output-style.md, The run's
    console)."""
    from taxjson.lib.out import console_lint
    probs = console_lint(text, width, allow)
    if probs:
        tc.fail("console style:\n  " + "\n  ".join(probs[:20])
                + "\n--- output ---\n" + text[:4000])


# A message label in its captured (width 0) form at the start of a line:
# shown to a person it is `Info:` / `Warning:` / `Error:` (lib/out).
_CAPTURED_LABEL = re.compile(
    r"(?m)^[ \t]*(?:(?:taxjson|tjs)[\w -]*: )?"
    r"(?:note|warning|error|NOTE|WARNING|ERROR|Note):")


def assert_labelled(tc, text: str) -> None:
    """Fail `tc` when a line of `text` (a person's stdout or stderr)
    starts with a captured message label instead of `Info:` /
    `Warning:` / `Error:` — wherever the line is, however long."""
    bad = [m.group(0).strip() + " ..." for m in
           re.finditer(_CAPTURED_LABEL.pattern + r".*", text)]
    if bad:
        tc.fail("captured labels shown to a person:\n  "
                + "\n  ".join(bad[:20]) + "\n--- output ---\n" + text[:4000])
