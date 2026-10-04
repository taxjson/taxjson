"""The grouped help page lists each command on one line; each command's
own -h carries the fuller text (owner request, docs refresh 2026-10)."""
import argparse
import re
import unittest

from taxjson.bin.taxjson_run import _build_parser
from taxjson.lib.country import COMMAND_COUNTRY

# _CappedHelpFormatter's widest page (78 columns) less argparse's 2-column
# margin, less the help column (max_help_position 24).
_HELP_WIDTH = 78 - 2 - 24
# Words for things that are gone: "phantoms" (now missing history), the
# web UI and `serve`, the GUI, the Seeking Alpha / FastGraph exports.
# (`migrate` still names the removed TradingView export's leftover file.)
_STALE = re.compile(r"\b(phantoms?|web ui|serve|seeking ?alpha|fastgraphs?|"
                    r"gui|portoml)\b", re.I)


def _subparsers():
    _p, sub = _build_parser()
    return sub, {a.dest: a for a in sub._choices_actions}


class TestOneLineHelp(unittest.TestCase):

    def test_every_one_liner_fits_one_line(self):
        _sub, acts = _subparsers()
        for name, act in acts.items():
            owner = COMMAND_COUNTRY.get(name)
            marker = {"canada": "(Canada) ", "usa": "(USA) "}.get(owner, "")
            line = marker + (act.help or "")
            with self.subTest(command=name):
                self.assertTrue(act.help, "no one-liner")
                self.assertLessEqual(len(line), _HELP_WIDTH, line)
                self.assertNotIn("\n", act.help)

    def test_every_command_has_a_fuller_description(self):
        sub, acts = _subparsers()
        for name, act in acts.items():
            desc = sub.choices[name].description or ""
            with self.subTest(command=name):
                self.assertGreater(len(desc), len(act.help or ""),
                                   "the -h text should say more than "
                                   "the one-liner")

    def test_no_stale_wording(self):
        sub, acts = _subparsers()
        for name, act in acts.items():
            sp = sub.choices[name]
            texts = [act.help or "", sp.description or ""] + [
                a.help for a in sp._actions
                if a.help and a.help is not argparse.SUPPRESS]
            with self.subTest(command=name):
                self.assertIsNone(_STALE.search(" ".join(texts)))


if __name__ == "__main__":
    unittest.main()
