# Releasing

taxjson has one development line, a sequence of release tags, and three
channels that name a release.

| Line | What it is | Who runs it |
| --- | --- | --- |
| `main` | Development. Changes land by pull request once GitHub CI passes (`scripts/land.sh`); every commit passes `scripts/ci.sh --quick`, the full gate before a release. | Contributors, and installs on the `dev` channel. |
| `vX.Y.Z` tags | Releases. Never moved or deleted; each one passed the full local gate (fuzzers included) and GitHub CI before it was tagged. | Installs on a channel that names it, or pinned to it. |

| Channel | Points at | Moves when |
| --- | --- | --- |
| `latest` | the newest `vX.Y.Z` tag | you cut a release (`scripts/release.sh`) |
| `beta` | the release `channels.json` on `main` names | you promote (`scripts/promote.sh vX.Y.Z beta`) |
| `stable` | the release `channels.json` on `main` names — **what new installs get** | you promote (`scripts/promote.sh vX.Y.Z`) |

`dev` (the `main` branch) and a pinned `vX.Y.Z` are installer channels too.

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
tjs deploy               # this machine's production copy → the newest tag   (tjs deploy v0.17.0 for one)
tjs promote v0.17.0      # stable → v0.17.0   (tjs promote v0.17.0 beta; no version = what this machine runs)
tjs channels             # where everything points (works on any install; --json, all)
```

`deploy` then checks that the production copy is checked out at the tag and that its `taxjson --version` reports that release, and fails loudly (exit 1) when either disagrees. `promote` and `deploy` refuse anywhere no development checkout is found;
everyone else upgrades by re-running the installer.

So the rhythm is: tag as often as you like, and `tjs deploy` each tag to
your own production copy (it runs your real books on the release before
anyone else does); promote to `beta` when a release is worth testers'
time; promote to `stable` once it has run for a while without surprises
— real books run on it, `check-filed` clean. Rolling `stable` back is
the same command with an older tag (it asks first).
`scripts/check-consistency.sh` (every gate run) checks that
`channels.json` parses and that every release it names is a tag.

### What promote does, and refuses

`scripts/promote.sh <tag> [stable|beta]` edits `channels.json`, commits
only that file as `Promote vX.Y.Z to stable` and lands it on `main`
through `scripts/land.sh` (the pre-push PII gate, then a pull request
merged once its checks pass; see "main takes pull requests only").
It never tags and never rebuilds. It promotes only what GitHub holds, so
it fetches first and refuses:

- off `main`, with uncommitted changes to `channels.json`, for `latest`
  (always the newest tag), or for a tag that is not a `vX.Y.Z` release;
- unless the local `main` is exactly `origin/main` (nothing unpushed
  rides along with the promote commit, and nothing newer is missed);
- unless the tag is an annotated tag that origin has (the same object as
  this clone's) and its commit is on `origin/main`.

The channel's current release is read from `origin/main:channels.json`,
what installers read. Moving a channel **forward** also needs CI: the
GitHub Actions `tests.yml` run for the push of the tag's commit to
`main` must have passed (`scripts/github-ci.sh`, the same check the
release cut makes; a run still going is waited for; no run, a failed run
or no `gh` refuses).
`TAXJSON_PROMOTE_IGNORE_CI=1` promotes without that check and prints a
warning. A forward move then runs `scripts/check-public.sh`: it reads
what GitHub serves beside the code — every release's notes, every issue
and pull request, and their comments (`gh api`, read-only) — and scans it
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
still waiting at its timeout (exit 3), the pull request stays open and
the promote happens when it merges. Trailers
go on the commit only when you pass them:
`TAXJSON_PROMOTE_TRAILERS='Co-Authored-By: …' scripts/promote.sh v0.17.0`.
`TAXJSON_SLUG` names the GitHub repository when `origin` is not a
github.com URL (default `taxjson/taxjson`); `release.sh` and
`check-public.sh` read it too.

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
`TAXJSON_DRY_RUN=1` prints what it would pick and changes nothing.

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
universal over platforms and Python 3.9+, nothing published in the last
7 days (`--cooldown-days`), then a wheel-coverage check for Linux
x86_64 / aarch64 and macOS arm64 / x86_64 on Python 3.9–3.13 (it fails
when `build`, `core` or `fx` lack one; other extras are reported).
Regenerate and commit them:

- whenever `pyproject.toml`'s dependencies or extras (or taxjson-fetch's
  dependencies) change: `tests/test_dependency_locks.py` fails until
  you do;
- before each release, so new installs get current fixes;
- on a Dependabot security alert for a pinned package (the alert names
  the file in `requirements/`). Dependabot's pip updates skip
  `requirements/` (`exclude-paths` in `.github/dependabot.yml`); a pull
  request that edits a lock anyway is closed and replaced by a
  regeneration — the test refuses a hand-edited lock (`body-sha256`).

Review the diff (new packages especially) before committing.

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
section to `## v0.17.0 (date)` and each `Fixed in: unreleased` in
`docs/troubleshooting.md` to the tag, bumps `pyproject.toml` — and, in
lockstep, `packages/taxjson-fetch/pyproject.toml` (the broker-fetch
plugin's version and its `taxjson>=` floor) — scans the release notes,
runs the **full** local gate (or reuses a recorded PASS of the same tree:
"Reusing a PASS" below), commits `release v0.17.0` and publishes `main`
through `scripts/land.sh` (the pre-push PII gate, then a pull request
rebase-merged once its checks pass). That push is the one in
`publish_main`, the only place `release.sh` publishes `main`. GitHub CI
then runs `tests.yml` on the version commit's push to `main`.

**The cut tags that commit once CI has passed on it.** It refuses unless
the local `main` is exactly `origin/main`; the version commit is on `main`
(the newest commit whose change made `pyproject.toml` say the version,
found by content — a rebase merge gives the landed commit a new id — with
the CHANGELOG's `## v0.17.0 (date)` heading and the plugin's version); the
tag is free here and on origin; and the GitHub Actions `tests.yml` run for
the push of **that exact commit** to `main` succeeded
(`scripts/github-ci.sh`: it waits up to three minutes for the run to be
listed, waits for a run still going, and names a failed one; there is no
override). A pull request's run does not count: it tested a merge onto
whatever its base was then. Then it makes the annotated tag on the version
commit (not on later commits, which it names), runs the pre-push gate on
it, pushes that one tag by name — never `git push --tags` (a checkout can
hold tags that must not be public; the pre-push hook refuses any other
tag) — and creates the GitHub release
(`gh release create vX.Y.Z --verify-tag -t "taxjson vX.Y.Z"`) with the
CHANGELOG's `## vX.Y.Z` section as its notes, or `--notes FILE` (give the
same file to both steps). A refused tag push deletes the local tag again.
The cut needs `gh`, logged in: without it nothing is tagged.

The notes are published apart from the code, so no push hook sees them:
they are scanned with `scripts/check-pii.sh --message` (the
commit-message scan: denylist, figure list, generic patterns, money
amounts) by `--prepare` before anything is committed, by the cut before
it tags, and again right before the release is created; a hit stops the
release. (A published release's notes can still be edited, even on an
immutable release — see "Something private leaked into a release" — but
a scan before they are published is the point.)

Neither step touches `channels.json`: the new release is `latest`, and
`stable` / `beta` stay where they were until you promote. The gate runs
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
3.9–3.13 matrix, the extras, gitleaks, the per-commit PII scan) on the
version commit before it is tagged. A CI failure on the version commit
means nothing is tagged: re-run a flaky run on GitHub and cut again, or
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
playbook's `Fixed in` tags, both `pyproject.toml` versions and the
plugin's `taxjson>=` floor). So before reusing the record it proves
those are the only differences (`release_edits_only`): no file but
those four is changed or untracked, and each of the four equals the
gated tree's copy once exactly those lines are masked. Then, instead of
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

Never `git push --tags`: `scripts/release.sh` pushes the one tag, by name. The `pre-push` hook (`scripts/hooks/pre-push`, installed by `scripts/dev-setup.sh`) refuses a push that carries any tag that is not named `vX.Y.Z`, is a lightweight tag, or points at a commit that is not on the remote's `main` (or on the `main` the same push sends), and every push that deletes or moves a tag. On GitHub, rulesets refuse moving or deleting a `v*` tag, creating one except by an admin, and creating the old pre-release tag ranges; and releases are immutable (below).

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

Every change reaches `main` through a pull request whose required checks
passed, whoever or whatever made it. `scripts/land.sh` is how the
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
the commit subject; body: the subjects), and has it rebase-merged once its
checks pass — by GitHub's auto-merge (`gh pr merge --auto --rebase`) when
`main`'s rules require checks and the repository allows auto-merge,
otherwise by waiting for every check itself and then merging. It waits
(`--timeout`, 60 minutes; `--no-wait` returns once the pull request is
open) and brings local `main` to what landed (`git pull --rebase
--autostash`: the rebase merge gave the commits new ids, and the local
copies drop out). A failed check (exit 1) turns auto-merge off, closes the
pull request and deletes its branch, so nothing merges later behind your
back; the commits stay on local `main` to fix and land again. Exit 3: still
waiting at the timeout; the pull request stays open. Never
`git push --no-verify`. `TAXJSON_LAND_DIRECT=1` pushes straight to `main`
after the same gate — only for a remote with no pull-request rule (the
script tests' throwaway remotes) and the emergency below.

### The ruleset

Applied by the owner (`gh api repos/taxjson/taxjson/rulesets -X POST
--input -` with the JSON below), next to the existing "protect main"
ruleset. GitHub's "Allow auto-merge" setting
(`gh api -X PATCH repos/taxjson/taxjson -F allow_auto_merge=true`) lets
`land.sh` hand the merge to GitHub; without it `land.sh` waits and merges
itself. The required checks are the check-run names of every `tests.yml`
job that runs on **every** pull request — none is path-filtered or
skipped by an `if:` on a pull request, so no required check can wait
forever. The nightly fuzz (schedule only) and the privacy watch (daily)
are not required. Renaming a job, or changing the runner image or the
Python matrix, renames its check: change this list in the same pull
request, and update the ruleset (`tests/test_fix_release_hardening.py`
fails when the two disagree).

<!-- ruleset: main -->
```json
{
  "name": "main: pull requests with passing checks",
  "target": "branch",
  "enforcement": "active",
  "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
  "bypass_actors": [],
  "rules": [
    {"type": "deletion"},
    {"type": "non_fast_forward"},
    {"type": "required_linear_history"},
    {"type": "pull_request", "parameters": {
      "required_approving_review_count": 0,
      "dismiss_stale_reviews_on_push": false,
      "require_code_owner_review": false,
      "require_last_push_approval": false,
      "required_review_thread_resolution": false,
      "allowed_merge_methods": ["rebase"]}},
    {"type": "required_status_checks", "parameters": {
      "strict_required_status_checks_policy": false,
      "do_not_enforce_on_create": false,
      "required_status_checks": [
        {"context": "ruff (critical tier)", "integration_id": 15368},
        {"context": "consistency / tax-rules / pii", "integration_id": 15368},
        {"context": "secrets (gitleaks)", "integration_id": 15368},
        {"context": "pii / new commits", "integration_id": 15368},
        {"context": "ubuntu-24.04 / Python 3.9", "integration_id": 15368},
        {"context": "ubuntu-24.04 / Python 3.10", "integration_id": 15368},
        {"context": "ubuntu-24.04 / Python 3.11", "integration_id": 15368},
        {"context": "ubuntu-24.04 / Python 3.12", "integration_id": 15368},
        {"context": "ubuntu-24.04 / Python 3.13", "integration_id": 15368},
        {"context": "ubuntu-24.04 / Python 3.12 / all extras", "integration_id": 15368}
      ]}}
  ]
}
```

(`integration_id` 15368 is GitHub Actions: only its runs satisfy a
check.) No bypass: in an emergency the owner turns the rule off (below).

### Emergencies

- **A broken `main` that blocks every pull request** (or GitHub Actions
  down while a fix must ship): turn the rule off —
  `gh api -X PUT repos/taxjson/taxjson/rulesets/<id> -f enforcement=disabled`
  (the id from `gh api repos/taxjson/taxjson/rulesets`) — push the fix
  with `TAXJSON_LAND_DIRECT=1 scripts/land.sh` (the PII gate still runs),
  turn it back on (`-f enforcement=active`), and **say so in the commit
  message** ("pushed with the main rule off: <why>"). A release still
  needs green CI on its version commit; there is no way around that but
  waiting.
- **A bad release:** release a new patch version, and roll a channel back
  with `scripts/promote.sh` and an older tag (Rolling back, below). A bad
  release is not edited.
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
- **In code or a commit message that shipped:** the history must be
  rewritten, and every release whose commit contains the bad commit is
  lost. In order:
  1. Move any channel (`stable`, `beta`) that names an affected release
     back to a release before the bad commit (`scripts/promote.sh`), so
     no install is sent to a tag about to disappear.
  2. Delete each affected release (`gh release delete vX.Y.Z --yes`: an
     immutable release can be deleted).
  3. Turn the "release tags: never moved or deleted" ruleset off, delete
     the tags on GitHub (`gh api -X DELETE
     repos/taxjson/taxjson/git/refs/tags/vX.Y.Z`; the pre-push hook
     refuses tag deletes by design), and turn it back on.
  4. Turn "protect main" and the pull-request rule off, rewrite the
     history (`git filter-repo` on a fresh clone; check it with
     `git log --all -S '<value>'`), force-push `main`, and turn both rules
     back on.
  5. Release the next patch version from the clean history. The deleted
     version numbers are gone for good: GitHub never lets an immutable
     release's tag name be used again.
  6. Ask GitHub Support to remove cached views of the old commits and
     the pull-request refs that still hold them; forks and clones keep
     their copies, which is why step 0 is rotation.

## Pinned actions and runners

Every `uses:` in `.github/workflows/` names a full commit SHA with its
release beside it (`actions/checkout@<sha>  # v7.0.1`), and every job runs
on an explicit runner image (`runs-on: ubuntu-24.04`, never
`ubuntu-latest`): a moved tag or label cannot change what runs, and the
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
- **Per-commit PII scan** (`pii / new commits`): every commit a pull
  request adds — and, on a push to `main`, every commit from `before` to
  the new head — its patch, message and identities, with the base's
  scanner and the generic patterns.
- **Privacy watch** (`.github/workflows/privacy-watch.yml`, daily and by
  hand): `scripts/check-public.sh --days 2` with a read-only token and no
  secrets — the last two days of issues, pull request titles and bodies,
  comments and the messages of `main`'s commits, and every release note,
  against the generic patterns. A hit fails the run and GitHub e-mails the
  owner; the log shows a masked hint and where. The full scan (private
  lists included) runs on the maintainer's machine before every forward
  promote.

## Rolling back

Never move or delete a published tag — installers may already have it. Roll the channel back instead: `scripts/promote.sh v0.16.0` (it asks before moving `stable` backwards), and new installs on `stable` get the good release again. Installs already on the bad release stay there — a channel never moves an install backwards — until the fix ships: fix it on `main` (`scripts/land.sh`), release the next patch (`scripts/release.sh v0.17.1 --prepare`, then `scripts/release.sh v0.17.1`), and promote that. A user who cannot wait pins the good release: `--channel v0.16.0`.

## Your own production install

Develop in the clone (`scripts/dev-setup.sh`, then `source setup.sh`), but run real books on a release: the installer's checkout under `~/.local/share/taxjson` (`TAXJSON_PROD_DIR` tells the release verbs when it is elsewhere) is the production copy, and `taxjson` on your PATH points at it. `tjs deploy` puts each new tag there; `--channel dev` switches that copy to `main` when you want to test a fix on real data before tagging. There is no follow-a-channel timer (Hatchabot's `follow-channel.sh`): a tax tool upgrades when you re-run the installer, not behind your back.
