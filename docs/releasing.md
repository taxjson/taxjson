# Releasing

taxjson has one development line, a sequence of release tags, and three
channels that name a release. Development happens in a private dev repo;
the public repo (`taxjson/taxjson`, what installers read) changes only
through the publisher, which publishes each release tag after a gate and a
wait ("How a release reaches the public repo", below).

| Line | What it is | Who runs it |
| --- | --- | --- |
| `main` | Development, on the dev repo. Changes land by pull request once CI passes (`scripts/land.sh`); every commit passes `scripts/ci.sh --quick`, the full gate before a release. The public repo's `main` holds one commit per published release, plus channel updates. | The maintainer; installs on the `dev` channel run the public `main`. |
| `vX.Y.Z` tags | Releases. Never moved or deleted; each one passed the full local gate (fuzzers included) and CI before it was tagged, and the publisher's gate twice before it was published. | Installs on a channel that names it, or pinned to it. |

| Channel | Points at | Moves when |
| --- | --- | --- |
| `latest` | the newest published `vX.Y.Z` tag | the publisher publishes a release you tagged (`scripts/release.sh`) |
| `beta` | the release `channels.json` on the public `main` names | you promote (`scripts/promote.sh vX.Y.Z beta`); the publisher copies it on its next run |
| `stable` | the release `channels.json` on the public `main` names — **what new installs get** | you promote (`scripts/promote.sh vX.Y.Z`); the publisher copies it on its next run |

`dev` (the `main` branch) and a pinned `vX.Y.Z` are installer channels too.

## How a release reaches the public repo

The maintainer's clone has two remotes: `origin`, the private dev repo
(branches, pull requests, CI, release tags), and `public`, the public
repo, read only:

```bash
git remote add --no-tags public https://github.com/taxjson/taxjson.git
git remote set-url --push public DISABLED
```

(`--no-tags`: the public repo's tags are the publisher's own objects,
which would clash with the dev repo's.) Nobody pushes to the public repo:
its rulesets let only the publisher's deploy key update `main` and create
`v*` tags.

1. `scripts/release.sh` pushes an annotated `vX.Y.Z` tag to the dev repo.
2. The publisher (a private repository of the maintainer's that runs the
   publish kit on a schedule) picks the tag up and runs its gate on the
   tagged tree and its CHANGELOG section: the maintainer's private values
   (as keyed fingerprints), the generic personal-data patterns and
   gitleaks. A hit stops the release and opens a private issue naming the
   rule, path and line (never the value); the fix is a new patch version.
3. It posts a private digest of the public-facing text that changes, then
   waits an hour (`wait_hours` in `publish.toml`). Meanwhile the maintainer can hold all publishing or
   reject this release; a moved or deleted dev tag cancels it.
4. It runs the gate again (private values added during the wait count
   too), then pushes one commit onto the public `main` (the tagged tree
   minus the files that never leave the dev repo, as `Release vX.Y.Z`) and
   an annotated `vX.Y.Z` tag on it.
5. The public repo's `.github/workflows/release.yml` creates the GitHub
   release from that tag's CHANGELOG section (`scripts/release-notes.sh`),
   and `tests.yml` runs on the release commit.

A channel move takes the fast lane: `scripts/promote.sh` lands the
`channels.json` change on the dev `main` and pushes a
`channels-YYYYMMDDTHHMMSSZ` tag (UTC) on that commit. On its next run the
publisher copies that one file onto the public `main` (`Update channels`),
after its gate (every release the file names must be published), with no
wait and no digest. Nothing else on the dev `main` rides along.

So the public history never carries dev commit messages, branches or pull
requests, and a release is public about a day after it is tagged. The
publisher runs on a schedule that GitHub may start hours late; the
maintainer can start a run by hand. Never published: `publish.toml`
(where and how the publisher publishes; its own copy must match),
`.github/dependabot.yml` (dependency updates run in the dev repo),
`backlog.md` (the list of known bugs and tasks, open ones included) and the
private-values provider `scripts/private-values`, which reads the
maintainer's private lists for the publisher's fingerprints. It prints
values: never run it where its output would be kept.

A pull request on the public repo cannot be merged there. The maintainer
brings it into the dev repo (the contributor stays the commit's author),
lands it, and closes the public pull request, which ships with the next
release.

## Channels — releasing often without moving new users

A tag is a release, and **tagging makes a release `latest` — nothing
more.** New installs take **`stable`**, which names a release in
`channels.json` on `main` and moves only when you say so:

```bash
scripts/release.sh v0.17.0 --prepare  # the version commit, landed on main (no tag yet)
scripts/release.sh v0.17.0            # once GitHub CI passed on it: tag, release — v0.17.0 is latest
scripts/promote.sh v0.17.0 beta       # beta   → v0.17.0
scripts/promote.sh v0.17.0            # stable → v0.17.0
scripts/channels.sh                   # where every channel points, and the releases
```

On the development machine the CLI has the same verbs. They find the
development checkout through `TAXJSON_DEV_DIR`, or the git checkout the
running `taxjson` is an editable install of (as long as that is not the
production copy):

```bash
tjs deploy               # this machine's production copy → the newest published tag   (tjs deploy v0.17.0 for one)
tjs promote v0.17.0      # stable → v0.17.0   (tjs promote v0.17.0 beta; no version = what this machine runs)
tjs channels             # where everything points (works on any install; --json, all)
```

`deploy` then checks that the production copy is checked out at the tag and that its `taxjson --version` reports that release, and fails loudly (exit 1) when either disagrees. `promote` and `deploy` refuse anywhere no development checkout is found;
everyone else upgrades by re-running the installer.

So the rhythm is: tag as often as you like, and `tjs deploy` each tag to
your own production copy once it is published (the production copy is a
clone of the public repo; it runs your real books on the release before
anyone else moves to it); promote to `beta` when a release is worth testers'
time; promote to `stable` once it has run for a while without surprises
— real books run on it, `check-filed` clean. Rolling `stable` back is
the same command with an older tag (it asks first).
`scripts/check-consistency.sh` (every gate run) checks that
`channels.json` parses and that every release it names is a tag.

### What promote does, and refuses

`scripts/promote.sh <tag> [stable|beta]` edits `channels.json`, commits
only that file as `Promote vX.Y.Z to stable` and lands it on `main`
through `scripts/land.sh` (the pre-push PII gate, then a pull request
merged once its checks pass; see "main takes pull requests only"). Then
it asks the publisher for it with a fast-lane tag (above). It never makes
a release tag and never rebuilds. Run again when the channel already
names the release, it makes the fast-lane tag only if the public repo
does not say so yet and none is waiting (after a pull request that merged
once promote had stopped waiting, for instance). It promotes only what
GitHub holds, so it fetches first and refuses:

- off `main`, with uncommitted changes to `channels.json`, for `latest`
  (always the newest tag), or for a tag that is not a `vX.Y.Z` release;
- unless the local `main` is exactly `origin/main` (nothing unpushed
  rides along with the promote commit, and nothing newer is missed);
- unless the tag is an annotated tag that origin has (the same object as
  this clone's) and its commit is on `origin/main`;
- unless the release is published: the public repo has the tag (the
  remote `public`; without that remote it refuses too).

The channel's current release is read from `origin/main:channels.json`
(the public `main` gets that file through the fast lane). Moving a channel **forward** also needs CI: the
GitHub Actions `tests.yml` run for the push of the tag's commit to
`main` must have passed (`scripts/github-ci.sh`, the same check the
release cut makes; a run still going is waited for, up to
`TAXJSON_CI_WAIT_MINS` (60); no run, a failed run, one still going at
that limit, or no `gh` refuses).
`TAXJSON_PROMOTE_IGNORE_CI=1` promotes without that check and prints a
warning. A forward move then runs `scripts/check-public.sh`: it reads
what the public repo serves beside the code — every release's notes,
every issue and pull request, their comments and each pull request's
reviews (`gh api`, read-only) —
and scans it
with `scripts/check-pii.sh` (release notes as `--message`), naming the
item of each hit, masked; a hit stops the promote. Without a working
`gh` that scan is skipped with a warning. Run it any time:
`scripts/check-public.sh` (exit 1 a hit, 2 cannot check). Moving a channel BACKWARDS (a rollback) is not held to CI,
since an older tag may predate a CI fix, and asks `[y/N]` first;
anything but `y` changes nothing. When the promote cannot land (main
moved on meanwhile, the PII gate refuses, a check fails), the promote
commit is taken back off the local `main`: only that commit (the script checks HEAD is it,
its parent the `main` it started from, its only file `channels.json`),
and other staged or uncommitted work is left alone. When `land.sh` is
still waiting at its timeout (exit 3), the pull request stays open; run
`scripts/promote.sh` again once it merges. Trailers
go on the commit only when you pass them:
`TAXJSON_PROMOTE_TRAILERS='Co-Authored-By: …' scripts/promote.sh v0.17.0`.
`TAXJSON_SLUG` names the dev repository when `origin` is not a
github.com URL (`release.sh` and `land.sh` read it too);
`TAXJSON_PUBLIC_SLUG` the public one (default: the remote `public`'s
address, else `taxjson/taxjson`), which `check-public.sh` scans.

### What the installer does with a channel

`install.sh` resolves `stable` (the default), `beta`, `latest`, `dev` or
`vX.Y.Z` (`--channel NAME`, or `TAXJSON_CHANNEL=NAME`; `release`, the old
name, means `latest`), prints `channel stable → release v0.17.0`, and
remembers the channel in `~/.config/taxjson/channel` (outside the clone),
so re-running the installer upgrades along the same channel. A pinned
version is remembered too: re-running stays there until another channel
is named. A channel never moves an install backwards — an install
running ahead of its channel (after `tjs deploy`, or a switch from
`latest` to `stable`) stays put until the channel passes it; naming a
version is how to go back. It installs only an annotated tag on
`main`'s history (tags are not signed; this is not a signature check).
`TAXJSON_DRY_RUN=1` prints what it would pick and stops: an existing
install only has its tags fetched; a missing one is not created (the
channel is resolved in a temporary clone that is removed).

The installer script is on the `stable` schedule too: taxjson.com's
`install.sh` shim fetches it from the release `stable` names, not from
`main` (only the `dev` channel runs `main`'s). A change to `install.sh`
reaches new users when you promote the release that carries it. The
first release that carries channels needs promoting to `stable` before
`--channel` works through the shim: until then the shim runs the older
installer, which installs the newest release and knows no `--channel`.

## Dependency locks

The installer installs dependencies only from `requirements/*.txt`
(hash-pinned; SECURITY.md, "Release integrity"). `scripts/lock_deps.py`
writes them with uv (`pipx install uv`): one resolution of every lock,
universal over platforms and Python 3.10+, nothing published in the last
7 days (`--cooldown-days`), then a wheel-coverage check for Linux
x86_64 / aarch64 and macOS arm64 / x86_64 on Python 3.10–3.13 (it fails
when `build`, `core` or `fx` lack one; other extras are reported, and
install.sh installs the core package where an extra has no wheel). A
known gap in an optional lock is listed as accepted instead
(`ACCEPTED_GAPS` in the script, with the reason): today Intel macOS has
no cryptography 50 wheel, which `all` and `generate-parser` pin for
google-auth. `--coverage` says when an accepted gap is no longer seen;
remove it then.
Regenerate and commit them:

- whenever `pyproject.toml`'s dependencies or extras (or taxjson-fetch's
  dependencies) change: `tests/test_dependency_locks.py` fails until
  you do;
- before each release, so new installs get current fixes;
- on a Dependabot security alert for a pinned package (the alert names
  the file in `requirements/`). When the fixed version is newer than the
  cooldown, let that one package past it:
  `scripts/lock_deps.py --security-update PKG` (repeatable; the locks'
  header names it under `security-updates`); every other package keeps
  the cooldown. `pip-audit --disable-pip --no-deps -r requirements/<lock>.txt`
  shows what is still flagged. Dependabot's pip updates skip
  `requirements/` (`exclude-paths` in `.github/dependabot.yml`); a pull
  request that edits a lock anyway is closed and replaced by a
  regeneration — the test refuses a hand-edited lock (`body-sha256`).

Review the diff (new packages especially) before committing.

The dev lock (`requirements/dev.txt`) also pins the linter: `tests.yml`'s
lint job and `scripts/dev-setup.sh` both install ruff from it, so the local
gate lints as CI does, and a regenerated lock moves both (re-run
`scripts/dev-setup.sh`; `scripts/ci.sh` notes a venv whose ruff differs).

## Dependency and code checks (the publish kit's)

Every pull request of the dev repo also runs the publish kit's checks
(`kit-checks` job, `.github/workflows/kit-checks.yml`; publish-kit SPEC.md
section 14), and `kit preflight` runs them again on what a tag carries:

- **known-vulnerable pins**: `osv-scanner` asks OSV.dev about every pinned
  version in the lock files `kit-checks.toml` lists (every
  `requirements/*.txt`; a new lock goes into that list too);
- **unsafe code patterns**: `opengrep` with the kit's selection of the
  opengrep-rules security rules over the tree.

Every hit blocks, whatever its severity, unless `kit-checks.toml` excepts
it: one advisory id in one lock file, or one rule id in one path, each with
a reason and an expiry date (an expired exception fails like a hit; `kit
doctor` lists those expiring within 14 days). The file is published, so
the reason holds no private values. Today's exceptions are two SHA-1
fingerprints that are stored and compared, not security uses: the row stems
that number parsed rows and the hash a ticker.map marker carries of its
line (`tests/test_kit_checks.py` keeps them the only SHA-1 uses in `src/`).
Inline `nosemgrep` comments and `.semgrepignore` files are not honoured.

`scripts/kit_checks.py` is an unchanged copy of the kit's
`checks/kit_checks.py` (`kit doctor` compares the two; a kit release with a
new tool version or rules commit means copying it again). Run it locally
on a clean checkout, not the working tree: the tools scan every file
there, `venv/` and stray worktrees included:

```bash
python3 -I scripts/kit_checks.py              # 3.11+; fetches the pinned tools into ~/.cache/publish-kit/checks once
python3 -I scripts/kit_checks.py --only deps  # or --only code
```

A dependency hit is fixed by regenerating the locks with the fixed version
let past the cooldown (`scripts/lock_deps.py --security-update PKG`,
above). `scripts/land.sh` waits for the job like every other pull-request
check, and counts a workflow every pull request triggers (no `paths`
filter) whose run GitHub has not created yet as pending.

`scripts/release.sh --prepare` does not run `kit_checks.py` itself, on
purpose: it lands the version commit through `land.sh`, so the
`kit-checks` job runs on that commit's pull request, on the very tree the
cut tags (`land.sh` merges only onto the base the checks ran on), and a
hit closes the pull request before anything is tagged. A local run would
repeat the same script on the same tree, and would need an export of it
(the tools scan every file of a working tree), Python 3.11+ and the
network. What the job cannot see is an advisory published after it ran;
`kit preflight taxjson` runs the publisher's gate and the kit checks on
the version commit again before you cut, if you want that answer first
(the publish kit's command: yours to run, since it runs the private-values
provider); Dependabot alerts cover the time after.

## Cutting a release

Two steps, so the commit that is tagged is one GitHub CI has passed:

```bash
scripts/release.sh v0.17.0 --prepare   # or 0.17.0 — both forms are accepted
# … GitHub CI runs on the version commit's push to main …
scripts/release.sh v0.17.0             # the cut
```

**`--prepare` makes the version commit and lands it, without a tag.** It
refuses on a dirty tree, off `main`, behind `origin/main`, or when the tag
exists (here or on origin). It promotes the CHANGELOG's `## Unreleased`
section to `## v0.17.0 (date)`, each `Fixed in: unreleased` in
`docs/troubleshooting.md` to the tag and `docs/upgrading.md`'s
`### Unreleased` (when a change asked something of users) to
`### v0.17.0`, bumps `pyproject.toml` — and, in
lockstep, `packages/taxjson-fetch/pyproject.toml` (the broker-fetch
plugin's version and its `taxjson>=` floor) — scans the release notes,
runs the **full** local gate (or reuses a recorded PASS of the same tree:
"Reusing a PASS" below), commits `release v0.17.0` and publishes `main`
through `scripts/land.sh` (the pre-push PII gate, then a pull request
rebase-merged once its checks pass). That push is the one in
`publish_main`, the only place `release.sh` publishes `main`. GitHub CI
then runs `tests.yml` on the version commit's push to `main`. That run
exists only when the version commit is the head of its push, so it must
land last: should a check fail on its pull request, a flaky one is landed
again as it is (`scripts/land.sh`), but for one that needs a fix the
version commit is taken back first (`git reset --keep HEAD~1`), the fix
landed, and `--prepare` run again. `--prepare` refuses an earlier version
commit, not yet landed, that has commits on top of it.

**The cut tags that commit once CI has passed on it.** It refuses unless
the local `main` is exactly `origin/main`; the version commit is on `main`
(the newest commit whose change made `pyproject.toml` say the version,
found by content — a rebase merge gives the landed commit a new id — with
the CHANGELOG's `## v0.17.0 (date)` heading and the plugin's version); the
tag is free here, on origin and on the public repo (a tag name the public
repo has is used for good); and the GitHub Actions `tests.yml` run for
the push of **that exact commit** to `main` succeeded
(`scripts/github-ci.sh`: it waits up to three minutes for the run to be
listed, waits for a run still going — up to an hour,
`TAXJSON_CI_WAIT_MINS`, then stops with the run's address: a run queued
while the self-hosted runner is offline would wait as long as it is
off — and names a failed one; there is no override). A pull request's run does not count: it tested a merge onto
whatever its base was then. Then it makes the annotated tag on the version
commit (not on later commits, which it names), runs the pre-push gate on
it, and pushes that one tag by name to the dev repo — never
`git push --tags` (a checkout can hold tags that must not be public; the
pre-push hook refuses any other tag). A refused tag push deletes the local
tag again. The cut needs `gh`, logged in: without it nothing is tagged.
The publisher does the rest ("How a release reaches the public repo"),
and the public repo's `release.yml` creates the GitHub release
(`gh release create vX.Y.Z --verify-tag -t "taxjson vX.Y.Z"`) from the
CHANGELOG's `## vX.Y.Z` section at the tag.

The notes are published apart from the code (as the GitHub release), so
they are scanned with `scripts/check-pii.sh --message` (the
commit-message scan: denylist, figure list, generic patterns, money
amounts) by `--prepare` before anything is committed and by the cut
before it tags; the publisher's gate scans them twice more and
`release.yml` once more (generic patterns) before the release exists. A
hit stops the release. (A published release's notes can still be edited,
even on an immutable release — see "Something private leaked into a
release" — but a scan before they are published is the point.) They are
always the CHANGELOG section: the old `--notes FILE` is refused, since the
public release could not carry it.

Neither step touches `channels.json`: once published the new release is
`latest`, and `stable` / `beta` stay where they were until you promote.
The gate runs
the test suite only: a handful of tests drive the real pipeline, but on
synthetic two-trade projects written into temporary directories and
deleted afterwards. Nothing in the suite or the release script reads a
real tax project. The gate also runs `scripts/check-pii.sh` (personal data
and secrets, plus the maintainer's private denylist and the maintainer's
private figure list), the same scan the `pre-push` hook applies to every
push. Refresh the figure list after each year's run, before releasing —
`scripts/check-pii.sh --collect-amounts <project dir>...` (salted hashes
of the figures in each project's `reports/`, `work/*.sum`, `*.toml` and
`*.tt`, and of the distinctive amounts, prices, quantities, reference
codes and clock times in its raw `inputs/` exports, merged into
`~/.config/taxjson/pii-amounts`; see CONTRIBUTING.md) — so no figure from
your own books can reach the tree, a pushed commit or a commit or tag
message.

**The gate is both:** the full local `scripts/ci.sh` (fuzzers, the private
lists) before the version commit is made, and GitHub CI (the Python
3.10–3.13 matrix, the extras, gitleaks, the per-commit PII scan) on the
version commit before it is tagged, then the publisher's gate twice before
it is published. A CI failure on the version commit means nothing is
tagged: re-run a flaky run on GitHub and cut again, or
fix `main` and prepare the next patch version.

### Reusing a PASS

The full gate is the slow part of a release, and it is often run on the
very commit being released just before (in a fresh clone, as AGENTS.md
asks). `scripts/ci.sh` records every full PASS (default, `--nightly` or
`--mutation` mode; never `--quick`) by the exact tree it tested:
`~/.cache/taxjson-gate/<tree>.<mode>.py<version>.pass` (owner-only;
`TAXJSON_GATE_CACHE` names another folder; `scripts/gate-record.sh`
writes and finds them). A record is written only when the working tree
was clean — nothing modified, nothing untracked — when the gate started
and still is, at the same tree, when it ends, and only when the gate
imported this tree's own `src/taxjson` (not one that `PYTHONPATH` or
another install put first). The tree hash, not the commit, is the key:
a fresh clone of the same commit, or a rebased commit with the same
content, finds it.

`scripts/release.sh --prepare` looks for a record of the tree it is about to
release (`git rev-parse HEAD^{tree}`, before its own edits), made on
the same Python version (`$PYTHON`, else `venv/bin/python3`), no older
than `--gate-max-age DAYS` (default 7). The tree it tags is not that
tree: it has the release's own edits (the CHANGELOG heading, the
playbook's `Fixed in` tags, the upgrade notes' heading, both
`pyproject.toml` versions and the plugin's `taxjson>=` floor). So before
reusing the record it proves those are the only differences
(`release_edits_only`): no file but those five is changed or untracked,
and each of the five equals the gated tree's copy once exactly those
lines are masked. Then, instead of
the full gate, it runs `scripts/ci.sh --release-edits` — the
consistency check and every test module that reads one of those lines
(a module that names `CHANGELOG`, `troubleshooting.md`, `pyproject`,
the installed version...; seconds) — and prints the record it reused:

```
== gate: reusing the PASS of tree 1a2b3c4d5e6f — ~/.cache/taxjson-gate/... (default gate, Python 3.12.3, passed 2026-10-09T12:00:00Z); --fresh-gate runs it again ==
```

With no record, an older one, another Python, or any other change in
the tree, it runs the full gate as before. `--fresh-gate` always runs
it. The cut runs no local gate. A typical release is then: run the gate
in a fresh clone of `main` with that clone's own code first on the path
— `PYTHONPATH=$PWD/src PYTHON=<the dev venv's python> scripts/ci.sh`
(a few minutes; without `PYTHONPATH` the dev venv's editable install
would test the dev clone's code, and no record is written) — then
`scripts/release.sh vX.Y.Z --prepare` in the dev clone, which reuses that
PASS, and `scripts/release.sh vX.Y.Z` once GitHub CI is green.

One tag covers both distributions: the core (`taxjson`) and the broker-fetch plugin (`taxjson-fetch`, `packages/taxjson-fetch`), which the installer installs by default from the same checkout (`--without-fetch` leaves it out). To publish wheels as well, build each from the tagged tree — `python -m build` and `python -m build packages/taxjson-fetch` — and upload both.

Semantic versioning, applied to tax output: a change that alters any filed number for an already-supported input is at least a minor bump and gets a CHANGELOG entry that names the rule and the direction of the change. Parser additions, new commands, and report-format changes are minor; documentation and internal refactors are patch.

## The tag guard

Never `git push --tags`: `scripts/release.sh` pushes the one tag, by name, and `scripts/promote.sh` its fast-lane tag. The `pre-push` hook (`scripts/hooks/pre-push`, installed by `scripts/dev-setup.sh`) refuses a push that carries any tag that is not named `vX.Y.Z` or `channels-YYYYMMDDTHHMMSSZ`, is a lightweight tag, or points at a commit that is not on the remote's `main` (or on the `main` the same push sends), and every push that deletes or moves a tag. On the public repo, rulesets let only the publisher's deploy key update `main` or create a `v*` tag, refuse moving or deleting a `v*` tag, and refuse creating the old pre-release tag ranges; and releases are immutable (below). The dev repo is private and has no rulesets (rulesets on private repositories need a paid plan): the hook and the publisher, which ignores any tag that is not a newer release or a fast-lane tag, are its checks.

`scripts/dev-setup.sh` (or `scripts/dev-setup.sh --hook-only`) installs the hook into the clone's own hooks folder (`git rev-parse --git-common-dir`/hooks, shared by every worktree; the installed file runs the pushing worktree's own `scripts/hooks/pre-push`). It does so even when a global `core.hooksPath` is set — git then runs that folder's `pre-push` instead — and says whether that global hook chains to the clone's own (one that runs `"$(git rev-parse --git-common-dir)/hooks/pre-push"` does); when it does not, it warns that the guard will not run.

## Immutable releases

Immutable releases are on for the repository. GitHub's own documentation
(<https://docs.github.com/en/code-security/supply-chain-security/understanding-your-software-supply-chain/immutable-releases>)
says what that locks: "Only the assets and tag are locked." A published
release's **assets** cannot be modified or deleted, and its **tag** is
locked to its commit and cannot be changed or deleted while the release
exists. Its **title and release notes stay editable** ("You can still
edit the title and release notes of a published immutable release"), as
does whether it is marked pre-release or latest. The release itself can
be deleted; then the tag can be deleted too, but "you cannot reuse the
same tag name" — that version number is gone for good. (Earlier versions
of this page, the scripts and SECURITY.md said the notes could never be
edited: that was wrong.)

## main takes pull requests only

Every change reaches the dev repo's `main` through a pull request whose
checks passed, whoever or whatever made it. `scripts/land.sh` is how the
maintainer's own commits land (and how `scripts/release.sh --prepare` and
`scripts/promote.sh` publish `main`):

```bash
git commit -m "…"           # on main, as before
scripts/land.sh             # a pull request; rebase-merged once its checks pass
```

It refuses (exit 2) off `main`, behind `origin/main` (pull --rebase
first) or when the pre-push PII gate refuses — which it runs itself,
through `bash`, on exactly what it publishes. Then it pushes the new
commits as a branch `land/<time>-<sha>`, opens the pull request (title:
the commit subject; body: the subjects), waits for every check and
rebase-merges it once all have passed — if `origin`'s `main` is still the
commit it pushed on. (Were `main` given a ruleset that
requires checks and a branch up to date with `main` ("require branches to
be up to date before merging"), and auto-merge allowed, it would hand the
merge to GitHub's auto-merge instead once every check has passed, which
then refuses to merge onto a `main` that moved. Never earlier: GitHub's
auto-merge waits only for the checks the ruleset requires, so a failed
check it does not name would merge. A ruleset that requires checks but not an up-to-date
branch is not enough: auto-merge would replay the commits onto whatever
`main` is when the checks pass, so `land.sh` merges itself then. The
private dev repo, on GitHub's free plan, has no ruleset. Should `main`
move in the moment between its last look and its own merge, it warns
that the landed commits do not sit on the gated base: run the gate on
`main`.) It waits (`--timeout`, 60 minutes; `--no-wait` returns once
the pull request is open, and nothing merges it by itself: merge it once
its checks pass, as the message says) and brings local `main` to what landed
(`git pull --rebase --autostash`: the rebase merge gave the commits new
ids, and the local copies drop out). A failed check (exit 1) closes the
pull request and deletes its branch, so nothing merges later behind your
back; the commits stay on local `main` to fix and land again. When GitHub
refuses that (an outage), the message says the pull request is still open
(with auto-merge still on, it may merge by itself) and how to close it. So does a
`main` that moved on while the checks ran (another pull request merged, a
promote from another clone, a merge on the web; exit 1): `git pull
--rebase`, run the gate again, and land again. Exit 3:
still waiting at the timeout; the pull request stays open. Never
`git push --no-verify`. `TAXJSON_LAND_DIRECT=1` pushes straight to `main`
after the same gate — only for the script tests' throwaway remotes and
the emergency below.

**Why a rebase merge**, not a merge commit or a squash:

- **What lands is what was gated.** `land.sh` pushes only when `main` has
  not moved on and merges only while it still has not, so the rebase
  replays the commits unchanged onto the base they were gated on: after a
  landing, `main`'s tip is the very tree the local gate and the pull
  request's CI passed, each landed commit keeps the tree it had locally,
  and a gate record (keyed by tree, "Reusing a PASS") still applies to it.
- **A linear `main`**, with no merge commit whose resolution could add
  content neither parent had (the merge case the PII gate had to learn).
- **CI on the version commit itself.** GitHub runs `tests.yml` on the head
  of each push to `main`, and the release cut tags only a version commit
  whose own push passed (`scripts/github-ci.sh`). A rebase merge of
  `release.sh --prepare`'s one-commit pull request makes the version
  commit that head; a merge commit would be the head instead.
- **Each commit keeps its own message**, unlike a squash, and `git bisect`
  stays precise. Squashing is the public side's job: the publisher
  (`mode = "squash"` in `publish.toml`) publishes each release as one
  commit of the tagged tree, so the dev history's commits, messages and
  branches never reach the public repo either way, and keeping every
  commit here costs nothing public.

The cost: the landed commits get new ids, hence the `git pull --rebase`
and `release.sh` finding the version commit by content.

Nothing enforces this on the dev repo but the tooling: land through
`scripts/land.sh`. Every `tests.yml` job that runs on a pull request runs
on every one (no path filter, no `if:` that skips one), so a check that
should have run is never simply missing from the wait
(`tests/test_fix_release_hardening.py`). The nightly run (the whole
matrix plus the deep fuzz) and the privacy watch (daily) are not part of
it, and both run on the public repo only. The dev repo is private, and
GitHub's runners for private repos are billed by the minute: a nightly
run there would use more than a month's included minutes, and each
landing runs the matrix twice. So the dev repo's jobs run on its own
self-hosted runner (label `taxjson-dev`: the maintainer's machine, as a
user of its own, set up with the publish kit's `runner/setup.sh`), and
the public repo's on GitHub's. Each job installs into a fresh virtualenv,
since a self-hosted runner keeps its Python between jobs.

### Emergencies

- **A broken `main` that blocks every pull request** (or GitHub Actions
  down while a fix must ship): push the fix with
  `TAXJSON_LAND_DIRECT=1 scripts/land.sh` (the PII gate still runs) and
  **say so in the commit message** ("pushed straight to main: <why>"). A
  release still needs green CI on its version commit; there is no way
  around that but waiting.
- **A bad release:** release a new patch version, and roll a channel back
  with `scripts/promote.sh` and an older tag (Rolling back, below). A bad
  release is not edited.
- **A release that must not publish:** during its wait, add the label
  `reject` to its digest issue in the publisher, or set the publisher's
  hold; or move or delete its dev tag (`git push origin :refs/tags/vX.Y.Z`
  from your own terminal; the pre-push hook refuses tag deletes by design).
- **Something private leaked into a release** — see the next section.

### Something private leaked into a release

First, if it is a credential: **revoke and rotate it now.** Removing it
from GitHub does not un-publish it (clones, forks, caches); rotation does.

- **In the release notes (or the title):** edit them —
  `gh release edit vX.Y.Z --notes-file fixed.md` — an immutable release's
  title and notes stay editable; only its assets and tag are locked. Then
  `scripts/check-public.sh` must come back clean.
- **In an issue, pull request or comment:** edit or delete it on GitHub,
  and delete the old revision from its edit history (the "edited"
  menu → the revision → "Delete revision from history"); a pull request's
  title and body too.
- **In a file of a published release:** the public history must be
  rewritten, and every release whose tree holds the value is lost (dev
  commit messages never reach the public repo: each release is one commit
  with a fixed message). First set the publisher's hold, so nothing
  publishes meanwhile, and remove the value on the dev `main`. Then:
  - **When the public repo has no forks**, recreate it rather than rewrite
    it (no wait on GitHub Support): make it private and rename it (it
    stays, frozen, never public again), create a new public
    `taxjson/taxjson`, and put it on the publisher again (`kit adopt`:
    deploy key, rulesets, immutable releases). Release the next patch
    version from the clean dev `main`, with `channels.json` naming only
    releases the new repository will have. The cost: GitHub does not let
    an immutable release's tag name be created again, even in the new
    repository, so the current release is re-cut under the next patch
    number (v0.30.0 became v0.30.1 this way on 2026-10-12), and installs
    pinned to an older release must move (docs/troubleshooting.md,
    "channels.json names … but … has no such tag").
  - **When it has forks:**
    1. Move any channel (`stable`, `beta`) that names an affected release
       back to a release before the value (`scripts/promote.sh`), so no
       install is sent to a tag about to disappear.
    2. Delete each affected release (`gh release delete vX.Y.Z --yes`: an
       immutable release can be deleted).
    3. Turn the public repo's tag rulesets off ("release tags: never
       moved or deleted", the publisher's `kit-tags`), delete the tags
       (`gh api -X DELETE repos/taxjson/taxjson/git/refs/tags/vX.Y.Z`),
       and turn them back on.
    4. Turn "protect main" and the publisher's `kit-main` off, rewrite
       the history (`git filter-repo` on a fresh clone; check it with
       `git log --all -S '<value>'`), force-push `main`, and turn both back
       on. The publisher then stops ("the public repo changed outside the
       kit") until `kit accept-public` records the new `main`.
    5. Release the next patch version. The deleted version numbers are
       gone for good.
    6. Ask GitHub Support to remove cached views of the old commits;
       forks and clones keep their copies, which is why step 0 is
       rotation.

## Pinned actions and runners

Every `uses:` in `.github/workflows/` names a full commit SHA with its
release beside it (`actions/checkout@<sha>  # v7.0.1`), and every job runs
on an explicit runner image (`runs-on: ubuntu-24.04`, never
`ubuntu-latest`) or, for a job of the dev repo only, its self-hosted
runner's label (`taxjson-dev`): a moved tag or label cannot change what runs, and the
runner image changes only when we choose (GitHub moves `ubuntu-latest` to
Ubuntu 26 from 2026-10-19). The jobs have read-only tokens, a timeout, and
checkouts that keep no credentials. `tests/test_fix_release_hardening.py`
fails CI when a `uses:` is not a SHA with its version, a runner label is a
`*-latest` one, or a workflow asks for write access.

To move an action, check the upstream release (its notes, and that the
tag is the project's own), then resolve it yourself:

```bash
gh api repos/<owner>/<action>/git/ref/tags/<tag>    # type "tag"? then:
gh api repos/<owner>/<action>/git/tags/<sha>        # .object.sha is the commit
gh api "repos/<owner>/<action>/contents/action.yml?ref=<commit>" --jq .content | base64 -d | grep using:   # node24
```

To move the runner image, change every `runs-on:` and the `os:` matrix,
which renames the matrix checks: update the ruleset's required checks
(above) in the same change.

## Checks on GitHub besides the suite

- **gitleaks** (`secrets (gitleaks)`): the commits a push or pull request
  adds, with the default rules and `.gitleaks.toml`'s exceptions — none
  today; an exception is an exact value for one rule in one file, never
  a word list (one that matched "fake", "example", "synthetic"… hid real
  secrets whose setting name held the word). `scripts/gitleaks-regression.sh`
  proves ordinary secrets are reported whatever they are called. On a
  pull request both the config used to scan and the regression script are
  the **base** commit's copies (the regression runs against the config
  the pull request proposes), so a pull request can neither widen the
  allowlist it is judged by nor weaken the test its new config must pass;
  a new exception lands on `main` before the fixture that needs it.
- **Kit checks** (`kit-checks`, the dev repo only): known-vulnerable pins
  and unsafe code patterns, above.
- **Per-commit PII scan** (`pii / new commits`): every commit a pull
  request adds — and, on a push to `main`, every commit from `before` to
  the new head — its patch, message and identities, with the base's
  scanner and the generic patterns.
- **Privacy watch** (`.github/workflows/privacy-watch.yml`, daily on the
  public repo, and by hand): `scripts/check-public.sh --days 2` on
  `taxjson/taxjson` with a read-only token and no
  secrets — the last two days of issues, pull request titles and bodies,
  comments, the reviews of the pull requests updated, and the messages of
  `main`'s commits, and every release note,
  against the generic patterns. A hit fails the run and GitHub e-mails the
  owner; the log shows a masked hint and where. The full scan (private
  lists included) runs on the maintainer's machine before every forward
  promote.

## Rolling back

Never move or delete a published tag — installers may already have it. Roll the channel back instead: `scripts/promote.sh v0.16.0` (it asks before moving `stable` backwards; the fast lane has no wait), and new installs on `stable` get the good release again once the publisher has run (start a run by hand to hurry it). Installs already on the bad release stay there — a channel never moves an install backwards — until the fix ships: fix it on `main` (`scripts/land.sh`), release the next patch (`scripts/release.sh v0.17.1 --prepare`, then `scripts/release.sh v0.17.1`; it publishes after its wait), and promote that. A user who cannot wait pins the good release: `--channel v0.16.0`.

## Your own production install

Develop in the clone (`scripts/dev-setup.sh`, then `source setup.sh`), but run real books on a release: the installer's checkout under `~/.local/share/taxjson` (`TAXJSON_PROD_DIR` tells the release verbs when it is elsewhere) is the production copy, and `taxjson` on your PATH points at it. `tjs deploy` puts each new tag there once it is published. `--channel dev` follows the public `main`, which changes only with releases; to try an unreleased fix on real data, run a build of the dev checkout (a `git archive` export, not a clone) from the tax folder. There is no follow-a-channel timer (Hatchabot's `follow-channel.sh`): a tax tool upgrades when you re-run the installer, not behind your back.
