"""`taxjson checklist`'s default view (docs/output-style.md): the list
(every step with its state; an open step's commands; how and why for
the next step and those needing attention), then the summary: the
counts per section as a table, one `! ` line per item needing attention
or blocked, and, last, the next step; `--all` / `--details` adds every
step's how, commands and why. On the synthetic style projects at
TAXJSON_WIDTH=100."""
import json
import re
import unittest

from _style import CapturedWidth, assert_styled, project

from taxjson.lib import checklist as cl
from taxjson.lib import out

# Read as a person would at a 100-column terminal.
_WIDTH = CapturedWidth("100")


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class TestChecklistDefault(unittest.TestCase):

    def _check(self, country):
        p = project(country)
        r = p.run("checklist", "--run", TAXJSON_WIDTH=100)
        self.assertEqual(r.returncode, 1, r.stderr)
        doc = json.loads(p.run("checklist", "--run", "--json").stdout)
        open_ = [s for s in doc["steps"]
                 if s["effective"] in ("attention", "blocked")]
        self.assertTrue(open_)
        assert_styled(self, r.stdout, 100)
        lines = r.stdout.splitlines()
        # The list first: every section's heading, every step on a line
        # of its own with its mark and number.
        for k, sec in enumerate(cl.SECTIONS, 1):
            self.assertIn(f"{k}. {sec.upper()}", lines, sec)
        for s in doc["steps"]:
            self.assertRegex(r.stdout, rf"(?m)^  \[.\] {s['step']:>2}\. "
                                       rf"{s['id']} ")
        # How and why for the next step and those needing attention
        # only (--all: every step).
        attention = sum(1 for s in doc["steps"]
                        if s["effective"] == "attention")
        self.assertLessEqual(r.stdout.count("Why: "), attention + 1)
        self.assertLess(r.stdout.count("Why: "),
                        sum(1 for s in doc["steps"] if s["why"]))
        # Then the summary: the counts table, a row per section, after
        # the last step ...
        last_step = max(i for i, ln in enumerate(lines)
                        if re.match(r"^  \[.\] +\d+\. ", ln))
        kinds = dict((ln, k) for k, ln in out.classify(r.stdout))
        for k, sec in enumerate(cl.SECTIONS, 1):
            row = next((ln for ln in kinds if ln.startswith(f"{k}. {sec} ")),
                       None)
            self.assertIsNotNone(row, (sec, r.stdout))
            self.assertEqual(kinds[row], "table", row)
            self.assertGreater(lines.index(row), last_step, row)
        # ... one `! ` line per item needing attention or blocked (items
        # blocked for one reason share a line), each within the width ...
        acts = out.act_lines(r.stdout)
        self.assertLessEqual(len(acts), len(open_), r.stdout)
        for ln in acts:
            self.assertLessEqual(len(ln), 100, ln)
        flat = " ".join(acts)
        for s in open_:
            self.assertIn(f"{s['step']} {s['id']}", flat, r.stdout)
            if s["effective"] == "attention":
                self.assertTrue(any(a.startswith(f"! {s['step']} {s['id']}")
                                    for a in acts), (s["id"], r.stdout))
        # ... and the next step last.
        nxt = doc["next"]
        self.assertEqual(lines[-1], f"Next (step {nxt['step']}, "
                                    f"{nxt['id']}): {nxt['do']}")
        self.assertIn("tjs checklist --all", r.stdout)
        # One progress step, not one per check.
        self.assertEqual(r.stderr.count("==> "), 1, r.stderr)
        return r

    def test_canada(self):
        self._check("canada")

    def test_usa(self):
        self._check("usa")

    def test_all_and_details_are_the_full_list(self):
        p = project("canada")
        full = p.run("checklist", "--run", "--all", TAXJSON_WIDTH=100)
        det = p.run("checklist", "--run", "--details", TAXJSON_WIDTH=100)
        # The two runs may straddle a minute: the header's HH:MM is each
        # run's own time.
        def unclocked(s):
            return re.sub(r"\b\d{2}:\d{2}\b", "HH:MM", s)
        self.assertEqual(unclocked(full.stdout), unclocked(det.stdout))
        flat = " ".join(full.stdout.split())
        for phrase in ("1. SET UP", "[!] 10. missing-history",
                       "tjs find-missing-history", "Why: ",
                       "tjs checklist --walk"):
            self.assertIn(phrase, flat)
        # A step per check there.
        self.assertGreater(full.stderr.count("==> Checking "), 3)

    def test_blocked_for_one_reason_is_one_line(self):
        rs = [cl.Result("run-clean", "blocked", cl.CONFIG_FIRST),
              cl.Result("audit", "blocked", cl.CONFIG_FIRST),
              cl.Result("sanity", "blocked", cl.CONFIG_FIRST),
              cl.Result("configure", "attention", "bad type")]
        text = cl.render_summary(rs, 2025, "canada", width_=100)
        acts = out.act_lines(text)
        self.assertEqual(len(acts), 2, text)
        self.assertTrue(acts[0].startswith("! 3 blocked ("), acts)
        self.assertIn("+1 more", acts[0])
        self.assertTrue(acts[1].startswith("! 3 configure: bad type"), acts)

    def test_act_line_fits(self):
        line = cl._act_line("10 missing-history", "word " * 40,
                            "tjs find-missing-history")
        self.assertLessEqual(len(line), cl.ACT_WIDTH)
        self.assertTrue(line.endswith("... — tjs find-missing-history"))
        self.assertEqual(
            cl._act_line("27 check-dates", "1 impossible date(s) — "
                         "`taxjson check-dates`", "tjs check-dates"),
            "! 27 check-dates: 1 impossible date(s) — tjs check-dates")


class TestLongFormSubcommands(unittest.TestCase):
    """The detectors that read a sub-command's TEXT ask for its long
    form (--details), whatever its default view leaves out."""

    def test_text_readers_pass_details(self):
        from taxjson.bin.taxjson_run import _DETAILS_CMDS
        import inspect
        src = inspect.getsource(cl)
        for cmd in ("sanity", "find-missing-history", "audit",
                    "check-filed"):
            self.assertIn(f'ctx.sub("{cmd}", LONG_FORM)', src)
            self.assertIn(cmd, _DETAILS_CMDS)
        self.assertIn('ctx.sub("elect", "--pending", LONG_FORM)', src)
        self.assertIn("elect", _DETAILS_CMDS)


if __name__ == "__main__":
    unittest.main()
