"""The fetch plugin's writers (GitHub issues #13 and #18).

#13: write_private and _questrade_token_write staged every write in ONE
fixed `<target>.part`: a second writer unlinked the first one's temp and
created its own there, so the first rename published the second's
unfinished (empty) file and the second rename failed. Each write now has
a temp file of its own (lib/safe_write); the read-merge-write of a
project's fetched files is serialized by a project lock
(work/.fetch.lock) and the shared Questrade token's read-refresh-save by
a lock beside the token file. The interleavings are driven with events,
never timing.

#18: --trim-overlap's backup name was chosen with Path.exists(), which
is False for a dangling symlink, and shutil.copy2 then wrote the
original CSV to the link's target outside the project. Backups now go
through safe_write.backup_copy (a link at a .bak name is skipped, never
followed; the copy is owner-only).

No network, never the real token file: every token path is a temporary
file named through $QUESTRADE_TOKEN_FILE. All data synthetic.
"""
# First: puts the plugin, the core and its test helpers on sys.path and
# registers the plugin's entry point when it is not pip-installed.
import _support  # noqa: F401
import io
import os
import stat
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from taxjson_fetch import api as F
from taxjson_fetch import command as C

_WAIT = 30          # seconds: a safety net only; every step is an event


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.lstat().st_mode)


class _Interleave:
    """Pause the writer threads named A and B inside os.fdopen (their
    temp file is open, nothing written yet) until released."""

    def __init__(self):
        self.opened = {"A": threading.Event(), "B": threading.Event()}
        self.go = {"A": threading.Event(), "B": threading.Event()}
        self.real = os.fdopen

    def fdopen(self, fd, *a, **kw):
        name = threading.current_thread().name
        if name in self.opened:
            self.opened[name].set()
            if not self.go[name].wait(_WAIT):
                raise RuntimeError(f"writer {name} never released")
        return self.real(fd, *a, **kw)


class TestOverlappingWriters(unittest.TestCase):
    """#13: the issue's interleaving, for each writer."""

    def _race(self, write, dest: Path, text_a: str, text_b: str,
              expect_a: str, expect_b: str):
        il = _Interleave()
        errors = {}

        def run(name, text):
            try:
                write(dest, text)
            except BaseException as e:              # reported below
                errors[name] = e

        a = threading.Thread(target=run, args=("A", text_a), name="A")
        b = threading.Thread(target=run, args=("B", text_b), name="B")
        with mock.patch("os.fdopen", il.fdopen):
            a.start()
            self.assertTrue(il.opened["A"].wait(_WAIT))
            b.start()
            self.assertTrue(il.opened["B"].wait(_WAIT))
            il.go["A"].set()
            a.join(_WAIT)
            self.assertNotIn("A", errors)
            # A returned: the destination is A's complete file.
            self.assertEqual(dest.read_text(encoding="utf-8"), expect_a)
            il.go["B"].set()
            b.join(_WAIT)
        self.assertEqual(errors, {})
        self.assertEqual(dest.read_text(encoding="utf-8"), expect_b)
        self.assertEqual(_mode(dest), 0o600)
        # Neither writer left a temp file behind.
        self.assertEqual(sorted(p.name for p in dest.parent.iterdir()),
                         [dest.name])

    def test_write_private(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "inputs" / "margin" / "questrade_2025.csv"
            out.parent.mkdir(parents=True)
            a = "Transaction Date,Symbol\n2025-01-02,ZZA\n"
            b = "Transaction Date,Symbol\n2025-01-03,ZZB\n"
            self._race(F.write_private, out, a, b, a, b)

    def test_questrade_token_write(self):
        with tempfile.TemporaryDirectory() as td:
            tok = Path(td) / "synthetic_token"
            self._race(C._questrade_token_write, tok, "SYNTHA", "SYNTHB",
                       "SYNTHA\n", "SYNTHB\n")

    def test_a_planted_link_at_the_old_fixed_name_is_left_alone(self):
        with tempfile.TemporaryDirectory() as td:
            tok = Path(td) / "synthetic_token"
            victim = Path(td) / "victim"
            victim.write_text("keep")
            (Path(td) / "synthetic_token.part").symlink_to(victim)
            C._questrade_token_write(tok, "SYNTHC")
            self.assertEqual(tok.read_text(), "SYNTHC\n")
            self.assertEqual(victim.read_text(), "keep")


class TestTokenRefreshSerialized(unittest.TestCase):
    """#13: two fetches sharing one rotating Questrade token. Each
    refresh kills the token it used, so the second must read the token
    the first SAVED, not the one the first read."""

    def test_second_refresh_waits_and_uses_the_rotated_token(self):
        chain = {"SYNTH1": "SYNTH2", "SYNTH2": "SYNTH3"}
        used = []
        in_refresh = threading.Event()
        release_a = threading.Event()
        b_waiting = threading.Event()
        results, errors = {}, {}

        def fake_refresh(token, http):
            used.append(token)
            if threading.current_thread().name == "A":
                in_refresh.set()
                if not release_a.wait(_WAIT):
                    raise RuntimeError("A never released")
            if token not in chain:
                raise RuntimeError("token already used")
            return {"refresh_token": chain[token],
                    "access_token": "AT", "api_server": "x"}

        with tempfile.TemporaryDirectory() as td:
            tok = Path(td) / "synthetic_token"
            tok.write_text("SYNTH1\n")
            os.chmod(tok, 0o600)

            def run(name, on_wait):
                try:
                    results[name] = C._qt_open_session(
                        tok, http=None, on_wait=on_wait)
                except BaseException as e:          # reported below
                    errors[name] = e

            env = {"QUESTRADE_TOKEN_FILE": str(tok),
                   "QUESTRADE_REFRESH_TOKEN": ""}
            with mock.patch.dict("os.environ", env), \
                    mock.patch.object(F, "qt_refresh", fake_refresh):
                a = threading.Thread(target=run, name="A",
                                     args=("A", None))
                b = threading.Thread(target=run, name="B",
                                     args=("B", b_waiting.set))
                a.start()
                self.assertTrue(in_refresh.wait(_WAIT))
                b.start()
                # B finds the token locked while A is mid-refresh.
                self.assertTrue(b_waiting.wait(_WAIT))
                self.assertEqual(used, ["SYNTH1"])
                release_a.set()
                a.join(_WAIT)
                b.join(_WAIT)
            self.assertEqual(errors, {})
            self.assertEqual(used, ["SYNTH1", "SYNTH2"])
            self.assertEqual(tok.read_text(), "SYNTH3\n")
            self.assertEqual(results["A"][0]["refresh_token"], "SYNTH2")
            self.assertEqual(results["B"][0]["refresh_token"], "SYNTH3")

    def test_no_token_is_none_and_a_failed_refresh_names_the_token(self):
        with tempfile.TemporaryDirectory() as td:
            tok = Path(td) / "synthetic_token"
            env = {"QUESTRADE_TOKEN_FILE": str(tok),
                   "QUESTRADE_REFRESH_TOKEN": ""}
            with mock.patch.dict("os.environ", env):
                self.assertEqual(C._qt_open_session(tok, http=None),
                                 (None, ""))

                def dead(token, http):
                    raise RuntimeError("401")
                with mock.patch.object(F, "qt_refresh", dead):
                    with self.assertRaises(C.QtAuthError) as cm:
                        C._qt_open_session(tok, http=None,
                                           explicit="SYNTHX")
            self.assertEqual(cm.exception.token, "SYNTHX")
            self.assertFalse(tok.exists())


class TestProjectFetchLock(unittest.TestCase):
    """#13: one `taxjson fetch` per project at a time — the existing
    CSV is read, merged and written back under work/.fetch.lock."""

    def test_second_fetch_waits_for_the_first(self):
        from taxjson.lib.safe_write import file_lock
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work = root / "work"
            work.mkdir()
            said = []
            waiting = threading.Event()
            entered = threading.Event()

            def say(msg):
                said.append(msg)
                if "another `taxjson fetch`" in msg:
                    waiting.set()

            req = SimpleNamespace(args=SimpleNamespace(), root=root,
                                  work=work, config={}, accounts=[],
                                  say=say, json=False)

            def fake_run(request):
                entered.set()
                return {}

            with mock.patch.object(C, "_run", fake_run):
                with file_lock(work / ".fetch.lock"):
                    t = threading.Thread(target=C.run, args=(req,))
                    t.start()
                    self.assertTrue(waiting.wait(_WAIT))
                    self.assertFalse(entered.is_set())
                t.join(_WAIT)
            self.assertTrue(entered.is_set())
            self.assertEqual(_mode(work / ".fetch.lock"), 0o600)


_QT_ROWS = ("Transaction Date,Symbol,Quantity\n"
            "2025-01-02,ZZSYN,1\n2025-02-02,ZZSYN,2\n")


class TestTrimBackupLinks(unittest.TestCase):
    """#18: a symlink at <export>.bak is never followed."""

    def _project(self, td):
        base = Path(td)
        project = base / "project"
        project.mkdir()
        source = project / "synthetic.csv"
        source.write_text(_QT_ROWS)
        return base, source

    def test_dangling_backup_link_is_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            base, source = self._project(td)
            outside = base / "outside.csv"
            link = source.with_name(source.name + ".bak")
            link.symlink_to(outside)
            backups = []
            self.assertEqual(C._qt_trim_file(source, "2025-01-01",
                                             "2025-01-31",
                                             backups=backups), 1)
            self.assertFalse(os.path.lexists(str(outside)))
            self.assertTrue(link.is_symlink())          # left alone
            bak = backups[0]
            self.assertNotEqual(bak, link)
            self.assertEqual(bak.parent, source.parent)
            self.assertFalse(bak.is_symlink())
            self.assertEqual(bak.read_text(), _QT_ROWS)
            self.assertEqual(_mode(bak), 0o600)
            self.assertEqual(source.read_text(),
                             "Transaction Date,Symbol,Quantity\n"
                             "2025-02-02,ZZSYN,2\n")

    def test_backup_link_to_an_existing_file_is_never_written(self):
        with tempfile.TemporaryDirectory() as td:
            base, source = self._project(td)
            outside = base / "outside.csv"
            outside.write_text("keep")
            source.with_name(source.name + ".bak").symlink_to(outside)
            backups = []
            C._qt_trim_file(source, "2025-01-01", "2025-01-31",
                            backups=backups)
            self.assertEqual(outside.read_text(), "keep")
            self.assertEqual(backups[0].read_text(), _QT_ROWS)

    def test_flex_backup_skips_a_dangling_link(self):
        old = ("Statement,Header,Field Name,Field Value\n"
               "Statement,Data,BrokerName,Interactive Brokers\n"
               "Account Information,Data,Title,old\n")
        new = old.replace(",old\n", ",new\n")
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            root = base / "project"
            acct = root / "inputs" / "ib"
            acct.mkdir(parents=True)
            out = acct / "ib_flex.csv"
            out.write_text(old)
            outside = base / "outside.csv"
            (acct / "ib_flex.csv.bak").symlink_to(outside)
            cfg = {"settings": {"year": 2025, "country": "canada"},
                   "accounts": {"ib": {"type": "taxable",
                                       "brokerage": "ibkr_flex",
                                       "query_id": "123456"}}}
            req = SimpleNamespace(
                args=SimpleNamespace(days=None, year=None, from_date=None,
                                     flex_token="SYNTHFLEX",
                                     positions=False, dry_run=False,
                                     trim_overlap=False),
                root=root, work=root / "work", config=cfg,
                accounts=["ib"], say=lambda m: None, json=False)
            with mock.patch.object(F, "flex_fetch",
                                   lambda *a, **k: new.encode()), \
                    redirect_stdout(io.StringIO()), \
                    redirect_stderr(io.StringIO()):
                C.run(req)
            self.assertFalse(os.path.lexists(str(outside)))
            self.assertEqual(out.read_text(), new)
            baks = sorted(p for p in acct.iterdir()
                          if p.name.startswith("ib_flex.csv.bak")
                          and not p.is_symlink())
            self.assertEqual([p.read_text() for p in baks], [old])


if __name__ == "__main__":
    unittest.main()
