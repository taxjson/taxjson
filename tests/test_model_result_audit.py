"""Step 6c(6c1): the audit reads gains rows through model/result views.

bin/taxjson_audit (`tjs audit`) reads the engine's records and the saved
gains files' rows (its tie-out, the lots' shares, the manual-reporting
rows, the reconciliation and the totals) through `GainRow(e)` views
over the rows' own dicts. Pinned as test_model_result_cli pins 6c(6b1):
`tjs audit` runs in-process (pipeline/dispatch runs the child in-process),
text and `--json`, in every golden project after its `tjs run`, with
the view and with a raw reader in its place, and the two print the
same; the raw side must have read rows in every project. The saved
files go through read_work_doc's row checks first (TestTheFunnel).
TestTheKeys pins which key each figure reads, which the comparison
cannot see (both sides read the same key)."""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _golden
from _support.view_lock import RawRow
from taxjson.bin import taxjson_audit as AU
from taxjson.cli.main import main as tjs_main
from taxjson.model import money as M

BOOKS = ("demo", "style-canada", "style-usa", "options", "crypto")
ARGVS = (["audit"], ["audit", "--summary"], ["audit", "--all-years"], ["audit", "--no-trace"])


class _Counted(RawRow):
    """RawRow, counting the rows it was made over."""
    __slots__ = ()
    made = 0

    def __init__(self, fields):
        _Counted.made += 1
        super().__init__(fields)


def _tjs(root, argv):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with mock.patch.object(sys, "argv", ["tjs", "-C", str(root), *argv]), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            tjs_main()
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


class TestTheAudit(unittest.TestCase):
    def test_with_and_without_the_view(self):
        for book in BOOKS:
            m = _golden.meta(book)
            with _golden.project_run(book, m) as (root, env, _r, _b), \
                    mock.patch.dict(os.environ, env, clear=True):
                root = root / m.get("run_dir", ".")
                read = 0
                for base in ARGVS:
                    for argv in (base, base + ["--json"]):
                        with self.subTest(book=book, argv=argv):
                            new = _tjs(root, argv)
                            _Counted.made = 0
                            with mock.patch.object(AU, "GainRow", _Counted):
                                old = _tjs(root, argv)
                            read += _Counted.made
                            self.assertEqual(new, old)
                            self.assertIn(new[0], (0, 1), new[2])
                with self.subTest(book=book):
                    self.assertGreater(read, 0, f"the audit read no rows in {book}")


class TestTheFunnel(unittest.TestCase):
    """A saved sale's gain that is not a number is refused in one line
    naming the file, the row and the field, before a view reads it."""

    def test_a_text_gain(self):
        m = _golden.meta("style-canada")
        with _golden.project_run("style-canada", m) as (root, env, _r, _b):
            root = root / m.get("run_dir", ".")
            work = root / "work"
            p = next(q for q in sorted(work.glob("*_gains_wash.json")) if not q.name.startswith(".")
                     and any("gain" in t and not t.get("action")
                             for t in json.loads(q.read_text(encoding="utf-8"))["transactions"]))
            doc = json.loads(p.read_text(encoding="utf-8"))
            sale = next(t for t in doc["transactions"] if "gain" in t and not t.get("action"))
            sale["gain"] = "abc"
            p.write_text(json.dumps(doc), encoding="utf-8")
            seen = []

            class _Seen(RawRow):
                __slots__ = ()

                def __init__(self, fields):
                    seen.append(fields.get("gain"))
                    super().__init__(fields)
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(AU, "GainRow", _Seen):
                for argv in (["audit"], ["audit", "--json"]):
                    with self.subTest(argv=argv):
                        code, _out, err = _tjs(root, argv)
                        self.assertEqual(code, 2, err)
                        self.assertIn(p.name, err)
                        self.assertIn("gain is 'abc', not a number", err)
                        self.assertEqual(err.count("`tjs run`"), 1, err)
                        self.assertEqual(err.count("gain is 'abc'"), 1, err)
                        self.assertIn("not audited — crypto: the audit stopped at the error above", err)
            self.assertNotIn("abc", seen)       # no view over the damaged row

    def test_the_books_a_refusal_leaves_unaudited(self):
        """The audit stops at the first book whose input it cannot read
        (A2-0796: an input every book shares would repeat its line), and
        names that book's accounts and the later books' (TJ-3007,
        TJ-3014); the coverage list still names an account with no books
        (TJ-3015); `--json` keeps its document (TJ-3016); the checklist
        shows the error, not the list (TJ-3014)."""
        from _support.filing_checklist_run_project import _ctx, _project
        from taxjson.lib import checklist as CL
        m = _golden.meta("style-canada")
        with _golden.project_run("style-canada", m) as (root, env, _r, _b), \
                mock.patch.dict(os.environ, env, clear=True):
            root = root / m.get("run_dir", ".")
            (root / "work" / "qt_base.json").unlink()
            p = root / "work" / "margin_gains_wash.json"
            doc = json.loads(p.read_text(encoding="utf-8"))
            next(t for t in doc["transactions"] if "gain" in t)["gain"] = "abc"
            p.write_text(json.dumps(doc), encoding="utf-8")
            code, out, err = _tjs(root, ["audit"])
            self.assertEqual(code, 2, err)
            self.assertEqual(err.count(p.name), 1, err)
            self.assertIn("not audited — margin, crypto: the audit stopped at the error above", err)
            self.assertIn("no books for qt", err)
            self.assertNotIn("RECONCILIATION", out)
            code, out, jerr = _tjs(root, ["audit", "--json"])
            self.assertEqual(code, 2, jerr)
            j = json.loads(out)
            self.assertEqual(j["not_audited"], [{"account": "margin", "reason": "stopped"},
                                                {"account": "crypto", "reason": "not_reached"},
                                                {"account": "qt", "reason": "no_books"}])
            self.assertEqual((j["events"], j["failed"]), ([], True))
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            _project(proj)
            r = CL.d_audit(_ctx(proj, {"audit": (2, "", err)}))
        self.assertEqual(r.status, "blocked")
        self.assertIn("margin_gains_wash.json", r.detail)
        self.assertIn("gain is 'abc'", r.detail)

    def test_a_later_book_refused(self):
        """The books audited before the stop stay in `--json` (TJ-3016)."""
        m = _golden.meta("style-canada")
        with _golden.project_run("style-canada", m) as (root, env, _r, _b), \
                mock.patch.dict(os.environ, env, clear=True):
            root = root / m.get("run_dir", ".")
            p = root / "work" / "crypto_gains_wash.json"
            doc = json.loads(p.read_text(encoding="utf-8"))
            next(t for t in doc["transactions"] if "gain" in t)["gain"] = "abc"
            p.write_text(json.dumps(doc), encoding="utf-8")
            code, out, err = _tjs(root, ["audit", "--json"])
            self.assertEqual(code, 2, err)
            j = json.loads(out)
            self.assertEqual(j["not_audited"], [{"account": "crypto", "reason": "stopped"}])
            self.assertTrue(j["events"])
            self.assertEqual({e["account"] for e in j["events"]} & {"crypto"}, set())
            self.assertTrue(j["failed"])

    def test_an_ambiguous_transfer(self):
        """The engine's refusal of a transfer in a loss window: one
        line, exit 2, as main's guard reports every refusal, and the
        account it left; `--json` adds nothing more (TJ-3008, TJ-3014)."""
        from taxjson.engine.rows import AmbiguousTransferDateError

        class _Rules:
            def __getattr__(self, name):
                return getattr(self.real, name)

            def compute_gains(self, *a, **kw):
                raise AmbiguousTransferDateError("a TRANSFER's ARRIVAL date sits in a loss window")

        m = _golden.meta("options")
        with _golden.project_run("options", m) as (root, env, _r, _b), \
                mock.patch.dict(os.environ, env, clear=True):
            root = root / m.get("run_dir", ".")
            real = AU.get_tax_rules

            def rules(country):
                r = _Rules()
                r.real = real(country)
                return r
            with mock.patch.object(AU, "get_tax_rules", rules):
                for argv in (["audit"], ["audit", "--json"]):
                    with self.subTest(argv=argv):
                        code, _out, err = _tjs(root, argv)
                        self.assertEqual(code, 2, err)
                        self.assertEqual(err, "taxjson-audit: error: a TRANSFER's ARRIVAL date sits in a "
                                              "loss window\ntjs audit: warning: not audited — margin, qt, "
                                              "rbc: the audit stopped at the error above. Fix it, then `tjs "
                                              "audit` again.\n")


class TestTheKeys(unittest.TestCase):
    """Each figure the audit prints or keeps is its sale's own, which
    `--json` lists from the engine's records: a view reading another
    key (`raw_gain` for `gain`, `permanently_disallowed` for
    `disallowed_amount`, `proceeds` for the quantity) passed every other
    test. Every golden holds a denied loss, where those differ. The
    no-denial block's "gain" line is not pinned: there every engine
    record's `raw_gain` is its `gain`."""

    @classmethod
    def setUpClass(cls):
        cls.out = {}
        for book in BOOKS:
            m = _golden.meta(book)
            with _golden.project_run(book, m) as (root, env, _r, _b), \
                    mock.patch.dict(os.environ, env, clear=True):
                root = root / m.get("run_dir", ".")
                cls.out[book] = {a: _tjs(root, ["audit", a])[1] for a in ("--json", "--summary", "--no-trace")}

    def _events(self, book):
        evs = json.loads(self.out[book]["--json"])["events"]
        self.assertTrue(any(float(e["disallowed_amount"] or 0) > AU.TIE
                            and abs(float(e["raw_gain"]) - float(e["gain"])) > AU.TIE
                            and not float(e["permanently_disallowed"] or 0) for e in evs),
                        f"{book} holds no denied loss to tell the keys apart")
        return evs

    def test_every_tie_out_field_has_a_view(self):
        """`_figure` reads a field by its JSON key (TJ-3012): every field
        the tie-out sums is one GainRow reads."""
        for f in AU.TIE_MONEY_FIELDS + AU.TIE_LOT_FIELDS + ("qty",):
            with self.subTest(f=f):
                self.assertEqual(float(AU._figure(AU.GainRow({f: 1.5}), f)), 1.5)

    def test_the_saved_files_figures(self):
        for book in BOOKS:
            for ev in self._events(book):
                t = ev["tie_out"]
                if not t.get("ties"):
                    continue
                with self.subTest(book=book, id=ev["id"][:12]):
                    tol = 0.01 + 0.00005 * t["records"]
                    self.assertAlmostEqual(t["pipeline_gain"], float(ev["gain"] or 0), delta=tol)
                    self.assertAlmostEqual(t["pipeline_disallowed"], float(ev["disallowed_amount"] or 0),
                                           delta=tol)

    def test_the_summary_lines(self):
        for book in BOOKS:
            evs = self._events(book)
            lines = [ln for ln in self.out[book]["--summary"].split("\n") if " qty=" in ln]
            self.assertEqual(len(lines), len(evs), book)
            for ev, ln in zip(evs, lines):
                with self.subTest(book=book, line=ln):
                    self.assertTrue(ln.startswith(ev["id"][:6]), ln)
                    m = re.search(r" qty=\s*(\S+) gain=\s*(\S+)(.*)$", ln)
                    self.assertEqual(m.group(1), f"{float(ev['qty'] or 0):,.4f}")
                    # The line's gain is the gains file's figure cut by shown
                    # (half up since step 6h(4): style-canada's -101.925 is
                    # -101.93, options' -521.505 is -521.51), not the format's
                    # half-even cut of the binary float.
                    self.assertEqual(m.group(2), f"{M.shown(float(ev['gain'] or 0), 2):+,.2f}")
                    dis = float(ev["disallowed_amount"] or 0)
                    # The denied amount too (style-canada's 305.775 is 305.78).
                    self.assertEqual(f"WASH+{M.shown(dis, 2):.2f}" in m.group(3), dis > AU.TIE)
                    self.assertEqual("WASH+" in m.group(3), dis > AU.TIE)

    def test_the_denied_loss_lines(self):
        def line(block, label, value):
            return re.search(rf"^\s+{label}\s+{re.escape(AU._fmt(value))}(\s|$)", block, re.M)
        for book in BOOKS:
            evs = self._events(book)
            blocks = re.split(r"^EVENT \d+/\d+ ", self.out[book]["--no-trace"], flags=re.M)[1:]
            self.assertEqual(len(blocks), len(evs), book)
            for ev, b in zip(evs, blocks):
                with self.subTest(book=book, id=ev["id"][:12]):
                    self.assertIn(f"#{ev['id'][:12]}", b.split("\n", 1)[0])
                    if float(ev["disallowed_amount"] or 0) > AU.TIE or ev.get("is_wash_sale"):
                        self.assertTrue(line(b, "raw gain", float(ev["raw_gain"])), b)
                        self.assertTrue(line(b, "allowed gain", float(ev["gain"] or 0)), b)
                    else:
                        self.assertTrue(line(b, "gain", float(ev["gain"] or 0)), b)

    def test_the_reconciliation_lines(self):
        """A saved record the re-run did not make, and a sale the saved
        file lacks, are named with their gain (not raw_gain)."""
        m = _golden.meta("options")
        with _golden.project_run("options", m) as (root, env, _r, _b), \
                mock.patch.dict(os.environ, env, clear=True):
            root = root / m.get("run_dir", ".")
            evs = json.loads(_tjs(root, ["audit", "--json"])[1])["events"]
            ev = next(e for e in evs if abs(float(e["raw_gain"]) - float(e["gain"])) > AU.TIE)
            p = root / "work" / "margin_gains_wash.json"
            doc = json.loads(p.read_text(encoding="utf-8"))
            rows = doc["transactions"]
            self.assertTrue(any(r.get("id") == ev["id"] for r in rows))
            made = dict(next(r for r in rows if r.get("id") == ev["id"]), id="ab" * 20, gain=12.34, raw_gain=56.78)
            doc["transactions"] = [r for r in rows if r.get("id") != ev["id"]] + [made]
            p.write_text(json.dumps(doc), encoding="utf-8")
            fails = " | ".join(json.loads(_tjs(root, ["audit", "--json"])[1])["reconciliation_failures"])
            self.assertIn(f"disposition id {'ab' * 6} ({made['symbol']} {made['date']}, gain 12.34) that", fails)
            self.assertIn(f"engine disposition {ev['id'][:12]} ({ev['symbol']} {ev['date']}, gain "
                          f"{float(ev['gain']):,.2f}) is MISSING", fails)


if __name__ == "__main__":
    unittest.main()
