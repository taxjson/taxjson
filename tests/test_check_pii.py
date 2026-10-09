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

    # 2026-10 security review M6: the credential stage is
    # case-insensitive and knows the broker tokens taxjson-fetch takes.
    def test_credential_stage_broker_tokens_any_case(self):
        tok = "aB3dE5" * 3                     # synthetic, 18 characters
        digits = "4071" * 6                    # a flex-token shape
        hits = [
            "TOKEN" + "=" + tok + "x" * 4,
            "Api_Key: " + tok + "x" * 4,
            "taxjson fetch --refresh" + "-token " + tok,
            "taxjson fetch --flex" + "-token=" + digits,
            "export QUESTRADE_REFRESH" + "_TOKEN=" + tok,
            "IBKR_FLEX" + "_TOKEN='" + digits + "'",
            "my_flex" + "_token = " + tok,
        ]
        for text in hits:
            r = self.scan("--text", stdin=text + "\n")
            self.assertEqual(r.returncode, 1, text)
            self.assertIn("credential-looking string", r.stdout)
            self.assertNotIn(tok, r.stdout)
            self.assertNotIn(digits, r.stdout)
        misses = [
            "taxjson fetch --refresh" + "-token YOUR_REFRESH_TOKEN",
            "taxjson fetch --flex" + "-token \"$IBKR_FLEX_TOKEN\"",
            "export QUESTRADE_REFRESH" + "_TOKEN=\"$(pass show qt)\"",
            "IBKR_FLEX" + "_TOKEN=<your token>",
            "--refresh" + "-token short",
        ]
        for text in misses:
            r = self.scan("--text", stdin=text + "\n")
            self.assertEqual(r.returncode, 0, text + r.stdout)

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


class TestCommitMessageAmounts(_Sandbox):
    """A2-1384: a pushed commit or tag MESSAGE may not quote a money-like
    amount (thousands separators and cents) — owner-book totals once
    reached the public history that way. `pii-ok` on the line lets a
    synthetic number through. Every amount here is synthetic."""

    _AMT = "1" + ",234,567" + ".89"

    def test_message_mode_refuses_amounts(self):
        for text in (f"owner 2025 total {self._AMT}",
                     "gain moved 12" + ",345.67 -> 12" + ",300.00",
                     "(-71" + ",734.84 on a book)",
                     "max cost $1" + ",139,811.07"):
            r = self.scan("--message", stdin=text + "\n")
            self.assertEqual(r.returncode, 1, text + r.stdout)
            self.assertIn("money amount", r.stdout)
            self.assertNotIn(self._AMT, r.stdout)

    def test_message_mode_lets_other_numbers_through(self):
        for text in ("fix 1234.56 rounding", "list 1,2,3.45", "id 1,000",
                     "v1.2.3, 4,500 rows", "ratio 0.25 and 12.50",
                     "a 1" + ",234.5 one-decimal value"):
            r = self.scan("--message", stdin=text + "\n")
            self.assertEqual(r.returncode, 0, text + r.stdout)

    def test_pii_ok_marks_a_synthetic_amount(self):
        for mark in ("pii-ok", "(pii-ok: synthetic)", "# pii-ok"):
            text = f"test books sum to {self._AMT} {mark}"
            r = self.scan("--message", stdin=text + "\n")
            self.assertEqual(r.returncode, 0, text + r.stdout)
        # the marker covers only its own line
        r = self.scan("--message",
                      stdin="synthetic 1" + f",000.00 pii-ok\nreal {self._AMT}\n")
        self.assertEqual(r.returncode, 1, r.stdout)
        # a word merely containing it does not count
        r = self.scan("--message", stdin=f"{self._AMT} not-pii-okay\n")
        self.assertEqual(r.returncode, 1, r.stdout)

    def test_plain_text_and_tree_modes_unchanged(self):
        self.assertEqual(self.scan("--text", stdin=f"x {self._AMT}\n").returncode, 0)
        (self.repo / "README.md").write_text(f"example total {self._AMT}\n")
        self.assertEqual(self.scan().returncode, 0)

    def _pre_push(self, lines):
        return subprocess.run(["bash", str(self.repo / "scripts" / "hooks" / "pre-push"),
                               "origin", "unused-url"], cwd=self.repo,
                              capture_output=True, text=True, input=lines, env=self.env)

    def test_pre_push_refuses_an_amount_in_a_commit_message(self):
        remote = self.tmp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True,
                       env=self.env, capture_output=True)
        self.git("remote", "add", "origin", str(remote))
        (self.repo / "a.txt").write_text("hello\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", f"engine fix\n\nOwner books: {self._AMT}")
        sha = self.git("rev-parse", "HEAD").strip()
        z = "0" * 40
        r = self._pre_push(f"refs/heads/main {sha} refs/heads/main {z}\n")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("commit MESSAGE", r.stderr)
        self.git("commit", "-q", "--amend", "-m",
                 f"engine fix\n\nSynthetic test total {self._AMT} (pii-ok)")
        sha = self.git("rev-parse", "HEAD").strip()
        r = self._pre_push(f"refs/heads/main {sha} refs/heads/main {z}\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # an annotated tag message is a message too
        self.git("tag", "-a", "v1", "-m", f"release; total {self._AMT}")
        tag = self.git("rev-parse", "v1").strip()
        r = self._pre_push(f"refs/tags/v1 {tag} refs/tags/v1 {z}\n")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("TAG message", r.stderr)


class TestPushEveryCommitAndDiffAmounts(_Sandbox):
    """Pre-release security review M2: the pre-push hook scans the added
    lines of EVERY pushed commit (a value added in one commit and removed
    in the next is still published in history), and --diff refuses a
    money amount added to a CHANGELOG / markdown doc or a code comment
    (pii-ok on the line lets a synthetic one through). Synthetic values."""

    _AMT = "1" + ",234,567" + ".89"

    def _commit(self, path, text, msg="c"):
        (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / path).write_text(text)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", msg)
        return self.git("rev-parse", "HEAD").strip()

    def _remote_with_base(self):
        remote = self.tmp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)],
                       check=True, env=self.env, capture_output=True)
        self.git("remote", "add", "origin", str(remote))
        base = self._commit("a.txt", "hello\n", "base")
        self.git("push", "-q", "--no-verify", "origin", "main")
        return base

    def _pre_push(self, new, old):
        return subprocess.run(
            ["bash", str(self.repo / "scripts" / "hooks" / "pre-push"),
             "origin", "unused-url"], cwd=self.repo, capture_output=True,
            text=True, env=self.env,
            input=f"refs/heads/main {new} refs/heads/main {old}\n")

    def test_value_added_then_removed_is_still_refused(self):
        base = self._remote_with_base()
        self._commit("notes.txt", f"acct {_ACCT}\n", "add")
        tip = self._commit("notes.txt", "acct (removed)\n", "remove")
        self.assertNotIn(_ACCT, self.git("diff", base, tip))   # net: clean
        r = self._pre_push(tip, base)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotIn(_ACCT, r.stdout + r.stderr)

    def test_clean_range_passes(self):
        base = self._remote_with_base()
        self._commit("notes.txt", "one\n")
        tip = self._commit("notes.txt", "two\n")
        r = self._pre_push(tip, base)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_amount_in_changelog_added_then_removed_is_refused(self):
        base = self._remote_with_base()
        self._commit("CHANGELOG.md", f"- the 2025 total is {self._AMT}\n")
        tip = self._commit("CHANGELOG.md", "- the 2025 total is unchanged\n")
        r = self._pre_push(tip, base)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("money amount", r.stdout + r.stderr)
        self.assertNotIn(self._AMT, r.stdout + r.stderr)

    # Issue #7: the binary scan read only the TIP's blob of each binary
    # path, so an earlier revision of a PDF (a compressed content stream
    # holding a denylist value) went out in history unscanned.
    @staticmethod
    def _pdf(text):
        import zlib
        body = zlib.compress(b"BT (" + text.encode() + b") Tj ET")
        return (b"%PDF-1.4\n1 0 obj << /Length " + str(len(body)).encode()
                + b" /Filter /FlateDecode >>\nstream\n" + body
                + b"\nendstream endobj\ntrailer << >>\n%%EOF\n")

    def _commit_bytes(self, path, data, msg="c"):
        (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / path).write_bytes(data)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD").strip()

    def test_earlier_binary_revision_in_the_range_is_refused(self):
        base = self._remote_with_base()
        bad = self._pdf(f"Account {_ACCT}")
        self.assertNotIn(_ACCT.encode(), bad)        # only once inflated
        self._commit_bytes("docs/slip.pdf", bad, "add slip")
        self.assertEqual(self.scan().returncode, 1)  # the tree scan sees it
        tip = self._commit_bytes("docs/slip.pdf", self._pdf("Account (cut)"),
                                 "clean slip")
        self.assertEqual(self.scan().returncode, 0)  # the tip is clean
        r = self._pre_push(tip, base)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("BINARY", r.stderr)
        self.assertIn("private denylist match", r.stdout + r.stderr)
        self.assertNotIn(_ACCT, r.stdout + r.stderr)

    def test_earlier_binary_revision_on_a_new_branch_is_refused(self):
        self._remote_with_base()
        self.git("checkout", "-q", "-b", "topic")
        self._commit_bytes("slip.pdf", self._pdf(f"Account {_ACCT}"))
        tip = self._commit_bytes("slip.pdf", self._pdf("Account (cut)"))
        r = subprocess.run(
            ["bash", str(self.repo / "scripts" / "hooks" / "pre-push"),
             "origin", "unused-url"], cwd=self.repo, capture_output=True,
            text=True, env=self.env,
            input=f"refs/heads/topic {tip} refs/heads/topic {'0' * 40}\n")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("BINARY", r.stderr)
        self.assertNotIn(_ACCT, r.stdout + r.stderr)

    def test_binary_blob_already_on_the_remote_is_not_rescanned(self):
        # A blob the remote already holds is published already: a push
        # that brings it back (a revert) adds nothing new to scan.
        self._remote_with_base()
        self._commit_bytes("slip.pdf", self._pdf(f"Account {_ACCT}"))
        self.git("push", "-q", "--no-verify", "origin", "main")
        base = self.git("rev-parse", "HEAD").strip()
        self._commit_bytes("slip.pdf", self._pdf("Account (cut)"))
        self.git("revert", "--no-edit", "HEAD")
        tip = self.git("rev-parse", "HEAD").strip()
        r = self._pre_push(tip, base)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_clean_binary_revisions_pass(self):
        base = self._remote_with_base()
        self._commit_bytes("slip.pdf", self._pdf("Account one"))
        self._commit_bytes("slip.pdf", self._pdf("Account two"))
        self._commit_bytes("other.pdf", self._pdf("Account three"))
        tip = self._commit_bytes("other.pdf", self._pdf("Account four"))
        r = self._pre_push(tip, base)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def _diff_of(self, path, text):
        base = self._commit("a.txt", "x\n", "base")
        self._commit(path, text)
        return self.git("diff", base, "HEAD")

    def test_diff_mode_amount_in_docs_and_comments(self):
        for path, text in (
                ("CHANGELOG.md", f"- owner books moved to {self._AMT}\n"),
                ("KNOWN_ISSUES.md", f"Total {self._AMT} differs\n"),
                ("docs/guide.md", f"e.g. ${self._AMT}\n"),
                ("src/x.py", f"x = 1  # was {self._AMT} on the books\n"),
                ("src/y.py", f"    # total {self._AMT}\n"),
                ("tools/z.sh", f"# {self._AMT}\n"),
                ("src/w.js", f"// {self._AMT}\n")):
            with self.subTest(path=path):
                r = self.scan("--diff", stdin=self._diff_of(path, text))
                self.assertEqual(r.returncode, 1, path + r.stdout)
                self.assertIn("money amount", r.stdout)
                self.assertNotIn(self._AMT, r.stdout)

    def test_diff_mode_amount_elsewhere_or_marked_passes(self):
        for path, text in (
                # data and code (fixtures carry synthetic amounts)
                ("tests/fixtures/s.csv", f'Total,"{self._AMT}"\n'),
                ("src/x.py", f'AMT = "{self._AMT}"\n'),
                # the escape, as a comment marker or the bare word
                ("CHANGELOG.md", f"- synthetic {self._AMT} <!-- pii-ok -->\n"),
                ("CHANGELOG.md", f"- synthetic test total {self._AMT} (pii-ok)\n"),
                ("src/y.py", f"# synthetic {self._AMT} pii-ok\n"),
                # not an amount
                ("CHANGELOG.md", "- v1.2.3 handles 1,000 rows and 12.50 fees\n")):
            with self.subTest(path=path, text=text):
                r = self.scan("--diff", stdin=self._diff_of(path, text))
                self.assertEqual(r.returncode, 0, path + r.stdout)


class TestPrivateFigureList(_Sandbox):
    """The maintainer's private figure list: `--collect-amounts` hashes the
    distinctive figures of a project's outputs into a salted SHA-256 list
    (mode 0600, no plain figures), and every scan mode refuses a line that
    holds one of them — tree, --diff (the pre-push per-commit scan),
    --message and --text — naming file:line, never the figure, with no
    pii-ok escape. A missing list at the default path is skipped. Every
    figure here is synthetic and assembled at run time."""

    _BOOK = "48" + ",213.97"          # a figure "from the books"
    _BARE = "48213" + ".97"
    _ONE = "73016" + ".4"             # one decimal: 73016.40 in the books
    _NEW = "91" + ",357.03"           # added by a second collection

    def setUp(self):
        super().setUp()
        self.amounts = self.tmp / "figs" / "pii-amounts"
        self.env["TAXJSON_PII_AMOUNTS"] = str(self.amounts)
        self.proj = self.tmp / "books" / "2025"
        (self.proj / "reports").mkdir(parents=True)
        (self.proj / "work").mkdir()
        (self.proj / "inputs").mkdir()
        (self.proj / "reports" / "margin.sum").write_text(
            f"GRAND TOTAL {self._BOOK} CAD\n"
            # round or short figures are not distinctive: never listed
            "fees 1,500.00  div 250.25  small 99.87  cost 1234.50\n")
        (self.proj / "work" / "x.sum").write_text(f"other {self._ONE}0\n")
        # an export's amounts are listed from 6 digits up only
        (self.proj / "inputs" / "b.csv").write_text("Net,624.18\n")

    def collect(self, *dirs):
        return subprocess.run(
            ["bash", str(self.repo / "scripts" / "check-pii.sh"),
             "--collect-amounts", *map(str, dirs)], cwd=self.tmp,
            capture_output=True, text=True, env=self.env)

    def test_collect_writes_a_private_hashed_list_and_merges(self):
        r = self.collect(self.proj)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("2 figure(s), 2 new", r.stdout)
        self.assertEqual(self.amounts.stat().st_mode & 0o777, 0o600)
        body = self.amounts.read_text()
        for plain in (self._BOOK, self._BARE, self._BARE.replace(".", ""),
                      self._ONE, "73016"):
            self.assertNotIn(plain, body)
        lines = [ln for ln in body.splitlines() if not ln.startswith("#")]
        self.assertRegex(lines[0], r"^salt [0-9a-f]{32}$")
        self.assertTrue(all(len(h) == 64 for h in lines[1:]), lines)
        self.assertEqual(len(lines) - 1, 2)
        salt = lines[0]
        # a second project merges into the same list, same salt
        other = self.tmp / "books" / "2026"
        (other / "reports").mkdir(parents=True)
        (other / "reports" / "y.txt").write_text(
            f"{self._NEW}\n{self._BOOK}\n")
        r = self.collect(self.proj, other)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("3 figure(s), 1 new", r.stdout)
        self.assertIn(salt, self.amounts.read_text())
        self.assertEqual(self.amounts.stat().st_mode & 0o777, 0o600)
        # a relative project path resolves against the caller's folder
        self.assertEqual(self.collect("books/2025").returncode, 0)
        self.assertNotEqual(self.collect(self.tmp / "nope").returncode, 0)

    def test_tree_scan_refuses_a_figure_without_printing_it(self):
        self.collect(self.proj)
        (self.repo / "t.py").write_text(
            "a = 1\n"
            f"b = {self._BARE}  # pii-ok\n"     # no escape for these
            f'c = "x {self._ONE}"\n'           # 73016.4 == 73016.40
            "d = 1500.00, 624.18, 99.87\n")    # not listed
        r = self.scan()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("matches a figure from your own books", r.stdout)
        self.assertIn("t.py:2", r.stdout)
        self.assertIn("t.py:3", r.stdout)
        self.assertNotIn("t.py:4", r.stdout)
        for plain in (self._BOOK, self._BARE, self._ONE):
            self.assertNotIn(plain, r.stdout)
        (self.repo / "t.py").write_text(f"b = {self._BARE[:-1]}8\n")
        r = self.scan()
        self.assertEqual(r.returncode, 0, r.stdout)
        # a CSV row whose cells sit side by side: 1,48213.97 is two cells
        (self.repo / "t.csv").write_text(f"Qty,Net\n1,{self._BARE}\n")
        r = self.scan()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("t.csv:2", r.stdout)
        (self.repo / "t.csv").unlink()
        # text inside a binary: drawing coordinates (73016.4) are not
        # amounts there, an exact two-decimal one still is
        pdf = self.repo / "d.pdf"
        pdf.write_bytes(b"%PDF-1.4\n0 0 m " + self._ONE.encode()
                        + b" 10 l S\n")
        r = self.scan()
        self.assertEqual(r.returncode, 0, r.stdout)
        pdf.write_bytes(b"%PDF-1.4\nBT (" + self._BARE.encode() + b") Tj ET\n")
        r = self.scan()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("d.pdf (embedded text):2", r.stdout)

    def test_message_and_text_modes_refuse(self):
        self.collect(self.proj)
        for mode in ("--message", "--text"):
            for text in (f"the total moved to {self._BOOK}",
                         f"synthetic {self._BOOK} pii-ok"):
                r = self.scan(mode, stdin="subject\n\n" + text + "\n")
                self.assertEqual(r.returncode, 1, mode + r.stdout)
                self.assertIn("line 3", r.stdout)
                self.assertNotIn(self._BOOK, r.stdout)
        r = self.scan("--message", stdin="the total is unchanged\n")
        self.assertEqual(r.returncode, 0, r.stdout)
        # identities are names, not figures
        r = self.scan("--identity", stdin="Sam <sam@example.com>\n")
        self.assertEqual(r.returncode, 0, r.stdout)

    def _commit(self, path, text, msg="c"):
        (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / path).write_text(text)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", msg)
        return self.git("rev-parse", "HEAD").strip()

    def test_diff_names_the_file_and_new_line(self):
        self.collect(self.proj)
        base = self._commit("src/a.py", "one\ntwo\nthree\nfour\n", "base")
        self._commit("src/a.py",
                     f"one\ntwo\nthree\nX = {self._BARE}\nfour\n")
        r = self.scan("--diff", stdin=self.git("diff", base, "HEAD"))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("src/a.py:4", r.stdout)
        self.assertNotIn(self._BARE, r.stdout)

    def test_pre_push_refuses_a_figure_added_then_removed(self):
        remote = self.tmp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)],
                       check=True, env=self.env, capture_output=True)
        self.git("remote", "add", "origin", str(remote))
        base = self._commit("a.txt", "hello\n", "base")
        self.git("push", "-q", "--no-verify", "origin", "main")
        self.collect(self.proj)
        self._commit("tests/t.py", f"X = {self._BARE}\n", "add")
        tip = self._commit("tests/t.py", "X = 1\n", "remove")

        def push(new):
            return subprocess.run(
                ["bash", str(self.repo / "scripts" / "hooks" / "pre-push"),
                 "origin", "unused-url"], cwd=self.repo, capture_output=True,
                text=True, env=self.env,
                input=f"refs/heads/main {new} refs/heads/main {base}\n")
        r = push(tip)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("matches a figure from your own books", r.stdout)
        self.assertNotIn(self._BARE, r.stdout + r.stderr)
        # the same figure in a commit message
        self.git("reset", "-q", "--hard", base)
        tip = self._commit("a.txt", "bye\n", f"fix\n\nTotal {self._BOOK}")
        r = push(tip)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("commit MESSAGE", r.stderr)

    def test_missing_or_broken_list(self):
        (self.repo / "t.py").write_text(f"b = {self._BARE}\n")
        # contributors have no list: the default path is skipped silently
        del self.env["TAXJSON_PII_AMOUNTS"]
        r = self.scan()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("figure", r.stdout)
        # a list named explicitly must exist
        self.env["TAXJSON_PII_AMOUNTS"] = str(self.amounts)
        r = self.scan()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("does not exist", r.stdout)
        # a list that cannot be parsed fails closed
        self.amounts.parent.mkdir(parents=True)
        self.amounts.write_text(f"{self._BARE}\n")
        r = self.scan()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("could not be checked", r.stdout)
        self.assertNotIn(self._BARE, r.stdout)
        # the default path is used when the variable is unset
        del self.env["TAXJSON_PII_AMOUNTS"]
        self.amounts.unlink()
        self.assertEqual(self.collect(self.proj).returncode, 0)
        default = self.tmp / ".config" / "taxjson" / "pii-amounts"
        self.assertTrue(default.is_file())
        self.assertEqual(self.scan().returncode, 1)


class TestPrivateFigureListInputs(_Sandbox):
    """The figure list also covers the raw exports under a project's
    inputs/: amounts with cents from 6 digits up, numbers with 3+
    decimals and 6+ significant digits (prices, rates, quantities),
    broker reference codes and clock times next to their date. Only
    distinctive values are listed, so short or round synthetic values
    never collide. Every value here is synthetic and assembled at run
    time, so this file never holds a listed value itself."""

    _PRICE = "37.4" + "18291"            # a 6-decimal price
    _QTY = "0.000" + "731942"            # a coin quantity
    _BIG = "6" + "1,742.39"              # a 6-digit amount with cents
    _CODE = "K" + "730419"               # a broker's internal code
    _OPT = "8" + "QWZKP3"                # an RBC-style option code
    _REF = "73" + "1904428"              # a 9-digit order reference
    _DATE = "2025-" + "04-17"
    _TIME = "14:" + "37:52"
    _ROOT = "QZJ" + "T"
    _SERIES = "2506" + "20C00041500"     # an option series (no root)

    def setUp(self):
        super().setUp()
        self.amounts = self.tmp / "figs" / "pii-amounts"
        self.env["TAXJSON_PII_AMOUNTS"] = str(self.amounts)
        self.proj = self.tmp / "books" / "2025"
        (self.proj / "inputs" / "margin").mkdir(parents=True)
        (self.proj / "inputs" / "margin" / "export.csv").write_text(
            "Date,Symbol,Quantity,Price,Net,Ref\n"
            f'"{self._DATE}, {self._TIME}",{self._CODE},{self._QTY},'
            f'{self._PRICE},"{self._BIG}",{self._REF}\n'
            f"{self._DATE},{self._OPT},1,0,0,x\n"
            # an IB instrument line: root and series padded apart
            f"Options,{self._ROOT}   {self._SERIES},{self._ROOT} 20JUN25 41.5 C\n"
            # none of these is distinctive: never listed
            "2025-04-17 10:00:00,THRU02,1.4138,0.555,99.87,624.18,"
            "US0378331005,250620C00041500,20250417,1000000\n")
        (self.proj / "inputs" / "kraken.json").write_text(
            '{"time": "' + self._DATE + "T" + self._TIME + 'Z"}\n')

    def collect(self):
        return subprocess.run(
            ["bash", str(self.repo / "scripts" / "check-pii.sh"),
             "--collect-amounts", str(self.proj)], cwd=self.tmp,
            capture_output=True, text=True, env=self.env)

    def test_collects_the_distinctive_values_of_raw_exports(self):
        r = self.collect()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # price, quantity, amount, code, option code, reference, the
        # clock time (both stamps are the same instant) and the IB
        # option's root+series: 8 values
        self.assertIn("8 figure(s), 8 new", r.stdout)
        body = self.amounts.read_text()
        for plain in (self._PRICE, self._QTY, self._BIG, self._CODE,
                      self._OPT, self._REF, self._TIME, self._SERIES):
            self.assertNotIn(plain, body)
        self.assertEqual(self.amounts.stat().st_mode & 0o777, 0o600)

    def test_scans_refuse_each_kind_without_printing_it(self):
        self.collect()
        lines = [
            f"price = {self._PRICE}0\n",                     # trailing zero
            f"qty = {self._QTY}\n",
            f"net = {self._BIG.replace(',', '')}\n",
            f"sym = '{self._CODE.lower()}'\n",                # any case
            f"# RBC code {self._OPT}\n",
            f"ref = {self._REF}  # pii-ok\n",                 # no escape
            f"when = '{self._DATE} {self._TIME}'\n",
            f"opt = '{self._ROOT}{self._SERIES}'\n",          # OCC form
            "ok = ['2025-04-17 10:00:00', 'THRU02', 1.4138, 0.555,\n",
            "      'US0378331005', '250620C00041500', 1000000]\n",
        ]
        (self.repo / "t.py").write_text("".join(lines))
        r = self.scan()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("matches a figure from your own books", r.stdout)
        for n in range(1, 9):
            self.assertIn(f"t.py:{n}\n", r.stdout + "\n")
        self.assertNotIn("t.py:9", r.stdout)
        self.assertNotIn("t.py:10", r.stdout)
        for plain in (self._PRICE, self._QTY, self._CODE, self._OPT,
                      self._REF, self._TIME):
            self.assertNotIn(plain, r.stdout)
        # the same values in a commit message
        for text in (f"fix the {self._CODE} row", f"at {self._DATE} {self._TIME}",
                     f"price {self._PRICE}"):
            r = self.scan("--message", stdin="subject\n\n" + text + "\n")
            self.assertEqual(r.returncode, 1, text + r.stdout)
            self.assertIn("line 3", r.stdout)
        r = self.scan("--message", stdin="subject\n\nprice 37.4183\n")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_binary_inputs_are_named_not_read(self):
        (self.proj / "inputs" / "slip.pdf").write_bytes(b"%PDF-1.4\n")
        r = self.collect()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 binary file(s)", r.stderr)


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

    def test_workflow_scans_every_pull_request_commit(self):
        # Security review M7: patch, message and identities of each
        # commit, with the base branch's scanner; read-only token.
        wf = (REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text()
        self.assertIn("\npermissions:\n  contents: read\n", wf)
        job = wf[wf.index("\n  pr-commits:\n"):]
        job = job[:job.index("\n  test:\n")]
        self.assertIn("if: github.event_name == 'pull_request'", job)
        self.assertIn("fetch-depth: 0", job)
        run = job[job.index("run: |"):]
        for part in ('git show "$BASE_SHA:scripts/check-pii.sh"',
                     "git log -p --no-merges", "--diff", "--message",
                     "--identity", 'range="$BASE_SHA..$HEAD_SHA"'):
            self.assertIn(part, run)
        self.assertNotIn("${{", run)          # event data only via env

    def test_workflow_actions_pinned_by_sha(self):
        # Security review LOW (j): a moved tag cannot change what runs.
        import re
        wf = (REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text()
        uses = re.findall(r"uses:\s*(\S+)(.*)", wf)
        self.assertTrue(uses)
        for ref, rest in uses:
            self.assertRegex(ref, r"@[0-9a-f]{40}$", ref)
            self.assertRegex(rest, r"#\s*v\d+\.\d+\.\d+", ref)
        self.assertRegex(wf, r"pip install ruff==\d+\.\d+\.\d+")

if __name__ == "__main__":
    unittest.main()
