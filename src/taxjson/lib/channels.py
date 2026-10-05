"""Release channels — where `stable`, `beta` and `latest` point, and what
this machine's production copy runs (`taxjson channels`; the release
verbs `promote` and `deploy` find the development checkout here too).

The model (docs/releasing.md):

- a `vX.Y.Z` tag is a release, and tagging (scripts/release.sh) makes it
  `latest` — the newest such tag — and nothing more;
- `stable` and `beta` name a release in `channels.json` on `main`; they
  move only when scripts/promote.sh says so (a one-line commit, never a
  new tag);
- the installer resolves stable (the default) | beta | latest | dev |
  vX.Y.Z, remembers the choice in ~/.config/taxjson/channel and keeps
  the production copy (~/.local/share/taxjson, or TAXJSON_PROD_DIR) on
  it.

Everything here reads a git checkout: the development checkout when
there is one (TAXJSON_DEV_DIR, or the checkout this package is an
editable install of — unless that checkout IS the production copy),
otherwise the production copy's own clone. One `git fetch` of the
checkout's own remote refreshes it; offline (or TAXJSON_OFFLINE=1) it
says so and shows what the clone already knows.
"""

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

CHANNELS_FILE = "channels.json"
NAMED = ("stable", "beta")             # named in channels.json
CHANNELS = NAMED + ("latest",)         # latest = the newest release tag
DEFAULT_LIMIT = 20
# A release is exactly vX.Y.Z (scripts/release.sh makes nothing else, and
# the installer never ships another shape of tag).
# \Z, not $: `$` also matches before a trailing newline, so "v1.2.3\n"
# passed as a release tag (security review I1).
TAG_RE = re.compile(r"v(\d+)\.(\d+)\.(\d+)\Z")


class ChannelsError(ValueError):
    """A channels.json that does not parse, or no checkout to read."""


def parse_channels(text: str, where: str = CHANNELS_FILE) -> Dict[str, str]:
    """{"stable": "vX.Y.Z", "beta": "vX.Y.Z"} from channels.json's text.
    A channel may be absent (the installer then takes the newest
    release); anything else — not JSON, not an object, an unknown
    channel (`latest` included: it is always the newest tag), a value
    that is not a vX.Y.Z release — is refused, naming `where`."""
    try:
        data = json.loads(text)
    except ValueError as e:
        raise ChannelsError(f"{where} is not valid JSON ({e})") from None
    if not isinstance(data, dict):
        raise ChannelsError(
            f'{where} must be a JSON object like {{"stable": "v1.2.3", '
            f'"beta": "v1.2.3"}}')
    unknown = sorted(str(k) for k in data if k not in NAMED)
    if unknown:
        extra = (" (latest is always the newest release tag; it is never "
                 "named)" if "latest" in unknown else "")
        raise ChannelsError(f"{where}: unknown channel "
                            f"{', '.join(map(repr, unknown))} — only "
                            f"stable and beta are named{extra}")
    out: Dict[str, str] = {}
    for ch in NAMED:
        if ch not in data:
            continue
        v = data[ch]
        if not isinstance(v, str) or not TAG_RE.match(v):
            raise ChannelsError(f"{where}: {ch} must name a release like "
                                f"\"v1.2.3\" (got {v!r})")
        out[ch] = v
    return out


def version_key(tag: str) -> Tuple[int, int, int]:
    m = TAG_RE.match(tag)
    if not m:
        raise ValueError(f"not a release tag: {tag!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def release_tags(names) -> List[str]:
    """The release tags among `names`, newest first."""
    return sorted({n for n in names if TAG_RE.match(n)}, key=version_key,
                  reverse=True)


# ------------------------------------------------------------------ git
def _git(repo: Path, *args: str, timeout: float = 60
         ) -> subprocess.CompletedProcess:
    # Hooks and fsmonitor off, no prompts, no optional locks: these are
    # read-only looks (and one fetch) at a checkout.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0",
           "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}
    return subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
         "-C", str(repo), *args],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
        timeout=timeout, env=env)


def _git_out(repo: Path, *args: str) -> Optional[str]:
    try:
        r = _git(repo, *args)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def is_checkout(path: Path) -> bool:
    """A git checkout (a worktree's .git is a file)."""
    return (path / ".git").exists()


def fetch(repo: Path, env: Optional[Mapping[str, str]] = None,
          timeout: float = 30) -> Tuple[bool, str]:
    """`git fetch --tags origin main` in `repo`: (True, "") when it
    worked, else (False, why). TAXJSON_OFFLINE=1 skips it."""
    from taxjson.lib.offline import offline_enabled
    if offline_enabled(env):
        return False, "TAXJSON_OFFLINE is set"
    try:
        r = _git(repo, "fetch", "--quiet", "--tags", "--force", "origin",
                 "main", timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"git fetch timed out after {timeout:.0f}s"
    except OSError as e:
        return False, f"git could not run ({e})"
    if r.returncode != 0:
        lines = [x for x in (r.stderr or "").splitlines() if x.strip()]
        return False, (lines[-1].strip() if lines else
                       f"git fetch exited {r.returncode}")
    return True, ""


def read_channels(repo: Path) -> Tuple[Dict[str, str], Optional[str]]:
    """channels.json as main has it: origin/main's (what installers
    read), else the local main's, else the working tree's. Returns
    (channels, where) — ({}, None) when no version of the file exists."""
    for ref in ("origin/main", "main"):
        text = _git_out(repo, "show", f"{ref}:{CHANNELS_FILE}")
        if text is not None:
            where = f"{CHANNELS_FILE} on {ref}"
            return parse_channels(text, where), where
    path = repo / CHANNELS_FILE
    if path.is_file():
        return parse_channels(path.read_text(encoding="utf-8"),
                              str(path)), str(path)
    return {}, None


# ------------------------------------------------------- where things are
def prod_dir(env: Optional[Mapping[str, str]] = None) -> Path:
    """This machine's production copy: the installer's checkout."""
    env = os.environ if env is None else env
    d = env.get("TAXJSON_PROD_DIR")
    return (Path(d).expanduser() if d
            else Path.home() / ".local" / "share" / "taxjson")


def channel_file() -> Path:
    """Where the installer remembers this machine's channel."""
    return Path.home() / ".config" / "taxjson" / "channel"


def remembered_channel() -> Optional[str]:
    try:
        v = channel_file().read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return v or None


def _package_checkout() -> Optional[Path]:
    """The git checkout this package runs from (an editable install of a
    clone), or None (an installed wheel)."""
    root = Path(__file__).resolve().parents[3]
    if (is_checkout(root) and (root / "pyproject.toml").is_file()
            and (root / "src" / "taxjson").is_dir()):
        return root
    return None


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def dev_checkout(env: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    """The development checkout — where releases are cut and promoted:
    TAXJSON_DEV_DIR, else the checkout this package is an editable
    install of, unless that is the production copy (the installer's
    clone is an editable install too). None when there is none."""
    env = os.environ if env is None else env
    d = env.get("TAXJSON_DEV_DIR")
    if d:
        p = Path(d).expanduser()
        if not is_checkout(p):
            raise ChannelsError(f"TAXJSON_DEV_DIR={d} is not a git checkout "
                                f"of taxjson")
        return p.resolve()
    root = _package_checkout()
    if root is None or _same(root, prod_dir(env)):
        return None
    return root


def source_checkout(env: Optional[Mapping[str, str]] = None
                    ) -> Tuple[Path, str]:
    """The checkout `channels` reads: (path, "development checkout" |
    "production copy")."""
    dev = dev_checkout(env)
    if dev is not None:
        return dev, "development checkout"
    prod = prod_dir(env)
    if is_checkout(prod):
        return prod.resolve(), "production copy"
    raise ChannelsError(
        f"no taxjson git checkout to read: no production copy at {prod} "
        f"(install with: bash -c \"$(curl -fsSL "
        f"https://taxjson.com/install.sh)\", or set TAXJSON_PROD_DIR) and no "
        f"development checkout (set TAXJSON_DEV_DIR)")


def this_box(env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """What the production copy runs: the release tag it is checked out
    at (None when it is on a branch — the dev channel — or absent), its
    commit, and the channel the installer remembered."""
    prod = prod_dir(env)
    out: Dict[str, Any] = {"dir": str(prod), "installed": is_checkout(prod),
                           "release": None, "commit": None, "branch": None,
                           "channel": remembered_channel()}
    if not out["installed"]:
        return out
    tag = (_git_out(prod, "describe", "--tags", "--exact-match") or "").strip()
    out["release"] = tag or None
    out["commit"] = (_git_out(prod, "rev-parse", "--short", "HEAD")
                     or "").strip() or None
    br = (_git_out(prod, "symbolic-ref", "--quiet", "--short", "HEAD")
          or "").strip()
    out["branch"] = br or None
    return out


# ------------------------------------------------------------- releases
def _tag_rows(repo: Path) -> List[Tuple[str, str, str]]:
    out = _git_out(repo, "for-each-ref", "--sort=-creatordate",
                   "--format=%(refname:short)|%(creatordate:short)|"
                   "%(subject)", "refs/tags/v[0-9]*") or ""
    rows = []
    for line in out.splitlines():
        tag, _, rest = line.partition("|")
        date, _, subject = rest.partition("|")
        if TAG_RE.match(tag):
            rows.append((tag, date, subject))
    rows.sort(key=lambda r: version_key(r[0]), reverse=True)
    return rows


_BOLD = re.compile(r"\*\*(.+?)\*\*")


def headline(repo: Path, tag: str, tag_subject: str = "") -> str:
    """One line saying what a release is: the first entry of its
    CHANGELOG section (its bold title when it has one) and how many more
    there are; else the tag's own message when it says more than
    "taxjson vX.Y.Z"."""
    text = _git_out(repo, "show", f"{tag}:CHANGELOG.md") or ""
    lines = text.splitlines()
    start = next((i for i, x in enumerate(lines)
                  if x.startswith(f"## {tag} ") or x.strip() == f"## {tag}"),
                 None)
    if start is not None:
        entries: List[List[str]] = []
        for x in lines[start + 1:]:
            if x.startswith("## "):
                break
            if x.startswith("- "):
                entries.append([x[2:].strip()])
            elif entries and x.startswith("  ") and x.strip():
                entries[-1].append(x.strip())
        if entries:
            first = " ".join(entries[0])
            m = _BOLD.search(first)
            head = (m.group(1) if m else first).replace("`", "").strip()
            head = head.rstrip(" —-:")
            more = len(entries) - 1
            return head + (f" (+{more} more)" if more else "")
    subj = tag_subject.strip()
    return "" if subj in ("", f"taxjson {tag}", tag) else subj


def status(repo: Path, *, fetch_remote: bool = True,
           limit: Optional[int] = DEFAULT_LIMIT,
           env: Optional[Mapping[str, str]] = None,
           source_kind: str = "") -> Dict[str, Any]:
    """Everything `taxjson channels` shows, as data."""
    if fetch_remote:
        online, why = fetch(repo, env)
    else:
        online, why = False, "--offline"
    chans, where = read_channels(repo)
    rows = _tag_rows(repo)
    known = {r[0] for r in rows}
    latest = rows[0][0] if rows else None
    box = this_box(env)
    pointers = {"stable": chans.get("stable"), "beta": chans.get("beta"),
                "latest": latest}
    problems = []
    for ch in NAMED:
        v = chans.get(ch)
        if v and v not in known:
            problems.append(f"{ch} names {v}, which this clone has no tag "
                            f"for")
        if not v:
            problems.append(f"{ch} is not named in channels.json — the "
                            f"installer takes the newest release for it")
    shown = rows if limit is None else rows[:limit]
    releases = []
    for tag, date, subject in shown:
        marks = [ch for ch in CHANNELS if pointers.get(ch) == tag]
        if box.get("release") == tag:
            marks.append("this-box")
        releases.append({"tag": tag, "date": date,
                         "subject": headline(repo, tag, subject),
                         "marks": marks})
    return {"source": str(repo), "source_kind": source_kind,
            "online": online, "offline_reason": None if online else why,
            "channels_file": where, "channels": pointers,
            "this_box": box, "problems": problems,
            "releases": releases, "total": len(rows)}


def render(st: Dict[str, Any], prog: str = "taxjson") -> str:
    """The text page for `taxjson channels`."""
    out = []
    if not st["online"]:
        out.append(f"(offline — showing what this clone already knows: "
                   f"{st['offline_reason']})")
    kind = f"{st['source_kind']} " if st.get("source_kind") else ""
    out.append(f"Release channels — from the {kind}{st['source']}"
               + (f" ({st['channels_file']})" if st["channels_file"] else
                  " (no channels.json yet)"))
    ch = st["channels"]
    for name in CHANNELS:
        out.append(f"  {name:<9}{ch.get(name) or '(not set)'}")
    box = st["this_box"]
    if box["installed"]:
        on = (box["release"] or
              (f"{box['branch']} @ {box['commit']}" if box["branch"]
               else f"{box['commit']} (not a release)"))
        extra = f", channel {box['channel']}" if box["channel"] else ""
        out.append(f"  {'this box':<9}{on}  ({box['dir']}{extra})")
    else:
        out.append(f"  {'this box':<9}no production copy at {box['dir']}")
    for p in st["problems"]:
        out.append(f"  ! {p}")
    out.append("")
    for r in st["releases"]:
        marks = ("←" + " ".join(r["marks"])) if r["marks"] else ""
        out.append(f"  {r['tag']:<9} {r['date']}  {marks:<26} "
                   f"{r['subject'][:70]}".rstrip())
    if not st["releases"]:
        out.append("  (no releases yet)")
    hidden = st["total"] - len(st["releases"])
    if hidden > 0:
        out.append(f"  … {hidden} older — {prog} channels all")
    return "\n".join(out)
