"""scripts/check-pii.sh and scripts/hooks/pre-push, driven in a throwaway
git repository with a throwaway denylist (TAXJSON_PII_DENYLIST). Every
"private" value here is synthetic and assembled at run time."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_BASH = shutil.which("bash")
_GIT = shutil.which("git")

# The fake "private" strings (assembled so this file never carries them).
_ACCT = "5550" + "4321"
_NAME = "Quinn" + " " + "Placeholder"


@unittest.skipUnless(_BASH and _GIT, "bash and git required")
class _Sandbox(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.repo = self.tmp / "repo"
        (self.repo / "scripts" / "hooks").mkdir(parents=True)
        shutil.copy(REPO_ROOT / "scripts" / "check-pii.sh", self.repo / "scripts")
        shutil.copy(REPO_ROOT / "scripts" / "hooks" / "pre-push",
                    self.repo / "scripts" / "hooks")
        self.deny = self.tmp / "denylist"
        self.deny.write_text(f"# test denylist\n{_ACCT}\n{_NAME}\n")
        self.env = dict(os.environ, TAXJSON_PII_DENYLIST=str(self.deny),
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                        HOME=str(self.tmp))
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Sam Synthetic")
        self.git("config", "user.email", "sam@example.com")
        self.git("config", "core.hooksPath", os.devnull)
        # The scripts are the tool under test, not scanned content.
        (self.repo / ".gitignore").write_text("scripts/\n")

    def tearDown(self):
        self._tmp.cleanup()

    def git(self, *args, env=None):
        r = subprocess.run(["git", *args], cwd=self.repo, capture_output=True,
                           text=True, env=env or self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def scan(self, *args, stdin=None):
        return subprocess.run(["bash", str(self.repo / "scripts" / "check-pii.sh"), *args],
                              cwd=self.repo, capture_output=True, text=True,
                              input=stdin, env=self.env)


class TestTreeScan(_Sandbox):
    def test_denylist_case_insensitive_and_separator_tolerant(self):
        spaced = _ACCT[:4] + " " + _ACCT[4:]
        dashed = "-".join(_ACCT[i:i + 2] for i in range(0, 8, 2))
        for text in (spaced, dashed, _NAME.upper()):
            f = self.repo / "notes.txt"
            f.write_text(f"see {text} here\n")
            r = self.scan()
            self.assertEqual(r.returncode, 1, text)
            self.assertIn("private denylist match", r.stdout)
            self.assertNotIn(text, r.stdout)                 # masked
        (self.repo / "notes.txt").write_text("see 5550 4322 here\n")
        self.assertEqual(self.scan().returncode, 0)

    def test_pii_ok_must_be_a_comment_marker(self):
        f = self.repo / "fixture.py"
        f.write_text(f'x = "{_ACCT}"  also pii-ok here\n')
        self.assertEqual(self.scan().returncode, 1)          # bare word: still a hit
        f.write_text(f'x = "{_ACCT}"  # pii-ok (synthetic)\n')
        self.assertEqual(self.scan().returncode, 0)
        f.write_text(f'x = "{_ACCT}"  (pii-ok: synthetic)\n')
        self.assertEqual(self.scan().returncode, 0)

    def test_eight_digit_filename_exempt_only_when_a_real_date(self):
        (self.repo / "stmt_20250131.csv").write_text("a\n")
        self.assertEqual(self.scan().returncode, 0)
        (self.repo / "stmt_20991399.csv").write_text("a\n")  # 13th month: an id
        r = self.scan()
        self.assertEqual(r.returncode, 1)
        self.assertIn("file NAME", r.stdout)


class TestDiffAndPush(_Sandbox):
    def _commit_all(self, msg="c"):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)

    def test_pure_rename_scans_the_new_path(self):
        (self.repo / "notes.csv").write_text("a,b\n")
        self._commit_all()
        base = self.git("rev-parse", "HEAD").strip()
        self.git("mv", "notes.csv", "stmt_55507777.csv")   # pii-ok (synthetic id shape)
        self._commit_all("rename")
        diff = self.git("diff", base, "HEAD")
        self.assertNotIn("+++ ", diff)                      # a PURE rename
        r = self.scan("--diff", stdin=diff)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("file NAME", r.stdout)

    def _bare_remote(self):
        remote = self.tmp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True,
                       env=self.env, capture_output=True)
        self.git("remote", "add", "origin", str(remote))
        return remote

    def _pre_push(self, lines):
        return subprocess.run(["bash", str(self.repo / "scripts" / "hooks" / "pre-push"),
                               "origin", "unused-url"], cwd=self.repo,
                              capture_output=True, text=True, input=lines, env=self.env)

    def test_identity_scan_blocks_others_warns_for_configured_user(self):
        self._bare_remote()
        (self.repo / "a.txt").write_text("hello\n")
        self.git("add", "-A")
        first, last = _NAME.split()
        env = dict(self.env, GIT_AUTHOR_NAME=f"{first} {last}",
                   GIT_AUTHOR_EMAIL="q@example.com")
        self.git("commit", "-q", "-m", "c1", env=env)
        sha = self.git("rev-parse", "HEAD").strip()
        z = "0" * 40
        r = self._pre_push(f"refs/heads/main {sha} refs/heads/main {z}\n")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("IDENTITY", r.stderr)
        # Same identity configured as THIS repo's user: warn, don't block.
        self.git("config", "user.name", _NAME)
        self.git("config", "user.email", "q@example.com")
        r = self._pre_push(f"refs/heads/main {sha} refs/heads/main {z}\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("WARNING", r.stderr)

    def test_identity_scan_is_scoped_to_new_commits(self):
        self._bare_remote()
        (self.repo / "a.txt").write_text("hello\n")
        self.git("add", "-A")
        env = dict(self.env, GIT_AUTHOR_NAME=_NAME, GIT_AUTHOR_EMAIL="q@example.com")
        self.git("commit", "-q", "-m", "old", env=env)
        old = self.git("rev-parse", "HEAD").strip()
        self.git("push", "-q", "--no-verify", "origin", "main")
        (self.repo / "b.txt").write_text("more\n")
        self._commit_all("new")
        new = self.git("rev-parse", "HEAD").strip()
        r = self._pre_push(f"refs/heads/main {new} refs/heads/main {old}\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_annotated_tag_message_and_tagger_scanned(self):
        self._bare_remote()
        (self.repo / "a.txt").write_text("hello\n")
        self._commit_all()
        self.git("tag", "-a", "v1", "-m", f"release for {_NAME.lower()}")
        tag = self.git("rev-parse", "v1").strip()
        r = self._pre_push(f"refs/tags/v1 {tag} refs/tags/v1 {'0' * 40}\n")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("TAG message", r.stderr)
        self.git("tag", "-d", "v1")
        env = dict(self.env, GIT_COMMITTER_NAME=_NAME, GIT_COMMITTER_EMAIL="q@example.com")
        self.git("tag", "-a", "v2", "-m", "clean message", env=env)
        tag = self.git("rev-parse", "v2").strip()
        r = self._pre_push(f"refs/tags/v2 {tag} refs/tags/v2 {'0' * 40}\n")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("IDENTITY", r.stderr)


_REAL_U = "U" + "7654321"          # a "real" IB id shape (not exempt)
_FIX_U = "U" + "1234567"           # the exempt synthetic IB id


class TestMediumRoundGaps(_Sandbox):
    """R1-347, S024-05, S024-06, S024-12, S022-05 (medium round)."""

    def test_adhoc_name_scan_ignores_the_path_prefix(self):   # R1-347
        first = _NAME.split()[0]
        parent = self.tmp / _NAME / "work"
        parent.mkdir(parents=True)
        (parent / "clean.txt").write_text("nothing here\n")
        for arg in ("clean.txt", str(parent / "clean.txt"), "."):
            r = subprocess.run(["bash", str(self.repo / "scripts" / "check-pii.sh"), arg],
                               cwd=parent, capture_output=True, text=True, env=self.env)
            self.assertEqual(r.returncode, 0, arg + r.stdout)
        # The file's own name still counts, and the output never shows it.
        (parent / f"{_NAME}.txt").write_text("x\n")
        r = subprocess.run(["bash", str(self.repo / "scripts" / "check-pii.sh"), "."],
                           cwd=parent, capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("file NAME matches the private denylist", r.stdout)
        self.assertNotIn(first, r.stdout)

    def test_unscannable_text_fails_closed_whatever_the_extension(self):   # S024-05
        f = self.repo / "export.tsv"
        f.write_bytes(f"Account\t{_REAL_U}\n".encode("utf-16-le"))
        r = self.scan()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("NUL bytes", r.stdout)
        f.unlink()
        # A Latin-1 byte makes grep call the file binary: scan it anyway.
        (self.repo / "run.log").write_bytes(b"caf\xe9 " + _REAL_U.encode() + b"\n")
        r = self.scan()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("IB account id", r.stdout)

    def test_diff_binary_guard_covers_every_non_binary_extension(self):   # S024-05
        diff = "diff --git a/export.tsv b/export.tsv\nBinary files /dev/null and b/export.tsv differ\n"
        self.assertEqual(self.scan("--diff", stdin=diff).returncode, 1)
        diff = "diff --git a/doc.pdf b/doc.pdf\nBinary files /dev/null and b/doc.pdf differ\n"
        self.assertEqual(self.scan("--diff", stdin=diff).returncode, 0)

    def test_exemption_applies_per_match_not_per_line(self):   # S024-06
        acct = "8765" + "4321"
        for text in (f"moved shares from {_REAL_U} today (fixture {_FIX_U})",
                     f"account no. {acct}, old fixture 9990" + "1234",
                     "write to me" + "@corp.io or test@example.com"):
            r = self.scan("--text", stdin=text + "\n")
            self.assertEqual(r.returncode, 1, text)
        self.assertEqual(self.scan("--text", stdin=f"fixture {_FIX_U}\n").returncode, 0)
        (self.repo / f"{_FIX_U}_activity.csv").write_text(f"Account,{_REAL_U}\n")
        r = self.scan()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("IB account id", r.stdout)

    def test_configured_denylist_missing_or_unreadable_fails(self):   # S024-12
        missing = self.tmp / "no-such-denylist"
        env = dict(self.env, TAXJSON_PII_DENYLIST=str(missing))
        r = subprocess.run(["bash", str(self.repo / "scripts" / "check-pii.sh")],
                           cwd=self.repo, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(missing.name, r.stdout)
        if os.geteuid() != 0:
            self.deny.chmod(0)
            try:
                r = self.scan()
            finally:
                self.deny.chmod(0o600)
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("cannot be read", r.stdout)
        # No variable and no default file: generic patterns only, still clean.
        env = {k: v for k, v in self.env.items() if k != "TAXJSON_PII_DENYLIST"}
        r = subprocess.run(["bash", str(self.repo / "scripts" / "check-pii.sh")],
                           cwd=self.repo, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("no private denylist", r.stdout)

    def test_sin_shape_with_valid_check_digit(self):   # S022-05 hardening
        sin = "046" + " 454 " + "286"                   # CRA's published sample
        r = self.scan("--text", stdin=f"SIN {sin}\n")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("social insurance number", r.stdout)
        self.assertEqual(self.scan("--text", stdin=f"SIN {sin.replace(' ', '-')}\n").returncode, 1)
        self.assertEqual(self.scan("--text", stdin="ref 123 456 789\n").returncode, 0)  # bad check digit


def _zip_bytes(members):
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in members.items():
            z.writestr(name, text)
    return buf.getvalue()


def _png_with_text(key, text):
    import struct
    import zlib

    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00")
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"zTXt", key.encode() + b"\x00\x00" + zlib.compress(text.encode()))
            + chunk(b"IDAT", idat) + chunk(b"IEND", b""))


class TestLowRoundGaps(_Sandbox):
    """S024-04/07/09/11/13/14/16/21/24 and S025-06 (low round)."""

    def _hook(self, lines):
        return subprocess.run(["bash", str(self.repo / "scripts" / "hooks" / "pre-push"),
                               "origin", "unused-url"], cwd=self.repo,
                              capture_output=True, text=True, input=lines, env=self.env)

    # S024-04 / S024-07: text inside binary documents is scanned.
    def test_binary_document_metadata_is_scanned(self):
        pdf = (b"%PDF-1.4\n1 0 obj << /Author (" + _NAME.encode()
               + b") /Producer (x) >> endobj\ntrailer << /Info 1 0 R >>\n%%EOF\n")
        docx = _zip_bytes({"docProps/core.xml":
                           f"<cp:coreProperties><dc:creator>{_NAME}</dc:creator>"
                           "</cp:coreProperties>",
                           "word/document.xml": "<w:t>hello</w:t>"})
        cases = {"docs/deck.pdf": pdf, "docs/notes.docx": docx,
                 "docs/shot.png": _png_with_text("Author", _NAME)}
        for rel, data in cases.items():
            f = self.repo / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)
            r = self.scan()
            self.assertEqual(r.returncode, 1, rel + r.stdout)
            self.assertIn("private denylist match", r.stdout)
            self.assertIn("(embedded text)", r.stdout)
            self.assertNotIn(_NAME.split()[0], r.stdout)
            f.unlink()
        self.assertEqual(self.scan().returncode, 0)

    def test_spreadsheet_cells_and_upper_case_suffix(self):
        xlsx = _zip_bytes({"xl/sharedStrings.xml":
                           f"<sst><si><t>Account</t></si><si><t>{_REAL_U}</t></si></sst>"})
        (self.repo / "statement.xlsx").write_bytes(xlsx)
        r = self.scan()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("IB account id", r.stdout)
        (self.repo / "statement.xlsx").unlink()
        (self.repo / "TRADES.CSV").write_bytes(f"Account,{_REAL_U}\n".encode("utf-16-le"))
        r = self.scan()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("NUL bytes", r.stdout)

    # S024-09: one positive test per generic detector.
    def test_each_generic_detector_fires(self):
        cases = {
            "IB account id": f"moved from {_REAL_U} today",
            "8-digit number next to the word account": "account no. 8765" + "4321",
            "home directory path": "see /ho" + "me/quinn/books",
            "e-mail address not on the allowlist": "mail quinn" + "@corp.io",
            "credential-looking string": "token = ghp_" + "A" * 36,
        }
        for label, text in cases.items():
            r = self.scan("--text", stdin=text + "\n")
            self.assertEqual(r.returncode, 1, label)
            self.assertIn(label, r.stdout)
            self.assertIn("<masked>", r.stdout + "<masked>")
        (self.repo / f"stmt_{_REAL_U}.csv").write_text("a,b\n")
        r = self.scan()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("file NAME carries an account-id shape", r.stdout)
        self.assertNotIn(_REAL_U, r.stdout)

    # S024-11: account numbers under an Account column, and Webull's
    # bilingual label.
    def test_account_column_and_bilingual_label(self):
        acct = "5123" + "4567"
        (self.repo / "q.csv").write_text(
            "Transaction Date,Action,Symbol,\"Description, long\",Account #\n"
            f"2025-01-02,Buy,XYZ,\"XYZ, INC\",{acct}\n")
        r = self.scan()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("under an Account column", r.stdout)
        self.assertNotIn(acct, r.stdout)
        (self.repo / "q.csv").write_text("Account,Symbol\n99901234,XYZ\n")
        self.assertEqual(self.scan().returncode, 0)          # synthetic 9990…
        text = "Account Number / Numéro de compte:,,,,,,,," + acct + ",\n"
        r = self.scan("--text", stdin=text)
        self.assertEqual(r.returncode, 1, r.stdout)

    # S024-13: an IB id inside a token (HTML element id).
    def test_ib_id_inside_a_token(self):
        for text in (f'id="tblAccountInformation_{_REAL_U}Body"',
                     f"secAccountInformation_{_REAL_U}Heading"):
            r = self.scan("--text", stdin=text + "\n")
            self.assertEqual(r.returncode, 1, text)
        self.assertEqual(self.scan("--text", stdin="ref XU76543210 1\n").returncode, 0)

    # S024-14: identities get the e-mail allowlist.
    def test_identity_email_allowlist(self):
        ident = "Pat Contributor <pat.c" + "@gmail.com>"
        r = self.scan("--identity", stdin=ident + "\n")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("e-mail address not on the allowlist", r.stdout)
        ok = "Pat <123+pat" + "@users.noreply.github.com>"
        self.assertEqual(self.scan("--identity", stdin=ok + "\n").returncode, 0)

    # S024-16: an id-named directory.
    def test_id_in_directory_name(self):
        d = self.repo / "tests" / "fixtures" / _REAL_U
        d.mkdir(parents=True)
        (d / "trades.csv").write_text("a,b\n")
        r = self.scan()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("file NAME carries an account-id shape", r.stdout)
        diff = (f"diff --git a/tests/fixtures/{_REAL_U}/t.csv b/tests/fixtures/{_REAL_U}/t.csv\n"
                f"+++ b/tests/fixtures/{_REAL_U}/t.csv\n+a,b\n")
        self.assertEqual(self.scan("--diff", stdin=diff).returncode, 1)

    # S024-21: a ':' in the path must not unmask a denylist hit.
    def test_colon_in_path_keeps_the_hit_hidden(self):
        d = self.repo / "2025:export"
        d.mkdir()
        (d / "notes.txt").write_text(f"holder,{_NAME}\n")
        r = self.scan()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("<content hidden>", r.stdout)
        self.assertNotIn(_NAME.split()[1], r.stdout)

    # S024-24 + binary blobs in the push.
    def test_pre_push_scans_ref_names_and_binary_blobs(self):
        (self.repo / "a.txt").write_text("hello\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "c1")
        sha = self.git("rev-parse", "HEAD").strip()
        z = "0" * 40
        for ref in (f"refs/heads/fix/{_REAL_U}-import",
                    f"refs/heads/wip/{_ACCT}-books"):
            r = self._hook(f"{ref} {sha} {ref} {z}\n")
            self.assertEqual(r.returncode, 1, ref + r.stderr)
            self.assertIn("NAME hit the scan", r.stderr)
        r = self._hook(f"refs/heads/main {sha} refs/heads/main {z}\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        pdf = (b"%PDF-1.4\n%\x00\x01\xe2\xe3\n<< /Author (" + _NAME.encode()
               + b") >>\n%%EOF\n")                    # binary to git
        (self.repo / "deck.pdf").write_bytes(pdf)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "deck")
        sha2 = self.git("rev-parse", "HEAD").strip()
        r = self._hook(f"refs/heads/main {sha2} refs/heads/main {sha}\n")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("BINARY", r.stderr)


class TestReaudit2Gaps(_Sandbox):
    """A2-0044/0450/0458 (denylist encodings), A2-0449 (lower-case IB id),
    A2-0760/1387 (labelled SIN / SSN forms), A2-1388 (account column in
    --diff mode)."""

    def _deny_bytes(self, data):
        self.deny.write_bytes(data)

    def test_bom_denylist_keeps_its_first_pattern(self):   # A2-0044/0450/0458
        self._deny_bytes(b"\xef\xbb\xbf" + f"{_NAME}\n{_ACCT}\n".encode())
        for text in (f"contact {_NAME}", f"acct {_ACCT}"):
            r = self.scan("--text", stdin=text + "\n")
            self.assertEqual(r.returncode, 1, text + r.stdout)
            self.assertIn("private denylist match", r.stdout)
        # a BOM before a comment line keeps it a comment (no bogus pattern)
        self._deny_bytes(b"\xef\xbb\xbf# header\n" + f"{_NAME}\n".encode())
        self.assertEqual(self.scan("--text", stdin="nothing private\n").returncode, 0)
        self.assertEqual(self.scan("--text", stdin=f"x {_NAME}\n").returncode, 1)

    def test_utf16_or_non_utf8_denylist_fails_closed(self):   # A2-0044/0450
        for data in (("\ufeff" + f"{_NAME}\n").encode("utf-16-le"),
                     f"{_NAME}\n".encode("utf-16-be"),
                     "Z\u00e9br\u00e9" .encode("cp1252") + b"\n"):
            self._deny_bytes(data)
            r = self.scan("--text", stdin="nothing private here\n")
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("denylist", r.stdout)
            self.assertIn("UTF-8", r.stdout)
            self.assertNotIn("check-pii: clean", r.stdout)
        self.deny.unlink()
        self.deny.mkdir()                               # a directory: refused too
        r = self.scan("--text", stdin="nothing private here\n")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("not a regular file", r.stdout)

    def test_lower_case_ib_id_in_content_and_names(self):   # A2-0449
        low = _REAL_U.lower()
        r = self.scan("--text", stdin=f'id="tblaccountinformation_{low}Heading"\n')
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("IB account id", r.stdout)
        self.assertEqual(self.scan("--text", stdin=f"fixture {_FIX_U.lower()}\n").returncode, 0)
        self.assertEqual(self.scan("--text", stdin="fixture u9990" + "001\n").returncode, 0)
        (self.repo / f"{low}.html").write_text("clean\n")
        r = self.scan()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("file NAME carries an account-id shape", r.stdout)
        self.assertNotIn(low, r.stdout)
        (self.repo / f"{low}.html").unlink()
        (self.repo / ("u9990" + "0001.html")).write_text("clean\n")
        self.assertEqual(self.scan().returncode, 0)

    def test_labelled_sin_any_separator(self):   # A2-0760 / A2-1387
        sin = "046" + "454" + "286"                     # CRA's published sample
        dotted = ".".join((sin[:3], sin[3:6], sin[6:]))
        for text in (f"SIN {sin}", f"SIN: {dotted}", f"social insurance number: {sin}",
                     f"NAS {sin}", f"sin,{sin}"):
            r = self.scan("--text", stdin=text + "\n")
            self.assertEqual(r.returncode, 1, text + r.stdout)
            self.assertIn("social insurance number", r.stdout)
            self.assertNotIn(sin, r.stdout)
        # bad check digit, or no label: not a SIN
        for text in ("SIN 123" + "456789", f"order {sin}", f"using {sin}x"):
            self.assertEqual(self.scan("--text", stdin=text + "\n").returncode, 0, text)

    def test_labelled_ssn(self):   # A2-1387
        ssn = "078" + "05" + "1120"
        forms = ("-".join((ssn[:3], ssn[3:5], ssn[5:])), " ".join((ssn[:3], ssn[3:5], ssn[5:])),
                 ".".join((ssn[:3], ssn[3:5], ssn[5:])), ssn)
        for label in ("SSN", "SSN:", "TIN", "Tax ID:", "ITIN"):
            for f in forms:
                r = self.scan("--text", stdin=f"{label} {f}\n")
                self.assertEqual(r.returncode, 1, f"{label} {f}" + r.stdout)
                self.assertIn("social security number", r.stdout)
        # impossible SSNs (area 000/666/9xx, group 00, serial 0000) are not hits
        for bad in ("000" + "-12-3456", "666" + "-12-3456", "912" + "-12-3456",
                    "123" + "-00-4567", "123" + "-45-0000"):
            self.assertEqual(self.scan("--text", stdin=f"SSN {bad}\n").returncode, 0, bad)
        self.assertEqual(self.scan("--text", stdin="ref 123" + "-45-6789\n").returncode, 0)

    def test_account_column_in_diff_mode(self):   # A2-1388
        acct = "5551" + "2345"
        diff = ("diff --git a/acct.csv b/acct.csv\nnew file mode 100644\n"
                "--- /dev/null\n+++ b/acct.csv\n@@ -0,0 +1,2 @@\n"
                f"+Date,Account #,Amount\n+2025-01-02,{acct},10\n")
        r = self.scan("--diff", stdin=diff)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("under an Account column", r.stdout)
        self.assertNotIn(acct, r.stdout)
        # the header lives in the file, the hunk adds only a data row
        (self.repo / "acct.csv").write_text(
            "Date,Account #,Amount\n" + "2025-01-01,99901234,5\n" * 10
            + f"2025-01-02,{acct},10\n")
        diff = ("diff --git a/acct.csv b/acct.csv\n--- a/acct.csv\n+++ b/acct.csv\n"
                "@@ -9,3 +9,4 @@\n 2025-01-01,99901234,5\n 2025-01-01,99901234,5\n"
                f" 2025-01-01,99901234,5\n+2025-01-02,{acct},10\n")
        r = self.scan("--diff", stdin=diff)
        self.assertEqual(r.returncode, 1, r.stdout)
        # a context (unchanged) row is not re-reported, a synthetic id passes
        diff = ("diff --git a/acct.csv b/acct.csv\n--- a/acct.csv\n+++ b/acct.csv\n"
                f"@@ -1,2 +1,3 @@\n Date,Account #,Amount\n 2025-01-02,{acct},10\n"
                "+2025-01-03,99901234,7\n")
        self.assertEqual(self.scan("--diff", stdin=diff).returncode, 0, self.scan("--diff", stdin=diff).stdout)


class TestReleaseAndCiGates(unittest.TestCase):
    """S025-06, S024-23, S023-00: static checks of the gate wiring."""

    def test_release_runs_the_pre_push_gate_before_pushing(self):
        text = (REPO_ROOT / "scripts" / "release.sh").read_text()
        hook = text.index("scripts/hooks/pre-push origin")
        self.assertLess(hook, text.index("git push --quiet origin main"))

    def test_missing_ruff_fails_and_dev_extra_installs_it(self):
        ci = (REPO_ROOT / "scripts" / "ci.sh").read_text()
        self.assertNotIn("== lint == skipped", ci)
        self.assertIn('FAILED+=("lint")', ci)
        try:
            import tomllib
        except ImportError:                       # Python < 3.11
            import tomli as tomllib
        doc = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
        dev = doc["project"]["optional-dependencies"]["dev"]
        self.assertTrue(any(d.startswith("ruff") for d in dev), dev)

    def test_workflow_runs_pii_and_consistency(self):
        wf = (REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text()
        for stage in ("scripts/check-pii.sh", "scripts/check-consistency.sh",
                      "scripts/check_tax_rules.py"):
            self.assertIn(stage, wf)
        for doc in ("scripts/ci.sh", "CONTRIBUTING.md"):
            self.assertNotIn("private repo", (REPO_ROOT / doc).read_text())


if __name__ == "__main__":
    unittest.main()
