# Releasing

taxjson has one development line, a sequence of release tags, and three
channels that name a release.

| Line | What it is | Who runs it |
| --- | --- | --- |
| `main` | Development. Every commit passes `scripts/ci.sh --quick`; the full gate before a release. | Contributors, and installs on the `dev` channel. |
| `vX.Y.Z` tags | Releases. Immutable; each one passed the full gate (fuzzers included) at cut time. | Installs on a channel that names it, or pinned to it. |

| Channel | Points at | Moves when |
| --- | --- | --- |
| `latest` | the newest `vX.Y.Z` tag | you tag (`scripts/release.sh`) |
| `beta` | the release `channels.json` on `main` names | you promote (`scripts/promote.sh vX.Y.Z beta`) |
| `stable` | the release `channels.json` on `main` names — **what new installs get** | you promote (`scripts/promote.sh vX.Y.Z`) |

`dev` (the `main` branch) and a pinned `vX.Y.Z` are installer channels too.

## Channels — releasing often without moving new users

A tag is a release, and **tagging makes a release `latest` — nothing
more.** New installs take **`stable`**, which names a release in
`channels.json` on `main` and moves only when you say so:

```bash
scripts/release.sh v0.17.0            # cut and tag: v0.17.0 is latest
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
only that file as `Promote vX.Y.Z to stable` and pushes `main` (after the
pre-push PII gate, which the script runs itself, as `release.sh` does).
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
`main` must have passed (`gh api`; a run still going is waited for; no
run, a failed run or no `gh` refuses).
`TAXJSON_PROMOTE_IGNORE_CI=1` promotes without that check and prints a
warning. Moving a channel BACKWARDS (a rollback) is not held to CI,
since an older tag may predate a CI fix, and asks `[y/N]` first;
anything but `y` changes nothing. When the push is refused (main moved
on meanwhile) or the PII gate refuses, the promote commit is taken back
off the local `main`: only that commit (the script checks HEAD is it,
its parent the `main` it started from, its only file `channels.json`),
and other staged or uncommitted work is left alone. Trailers
go on the commit only when you pass them:
`TAXJSON_PROMOTE_TRAILERS='Co-Authored-By: …' scripts/promote.sh v0.17.0`.
`TAXJSON_SLUG` names the GitHub repository when `origin` is not a
github.com URL (default `taxjson/taxjson`).

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

## Cutting a release

```bash
scripts/release.sh v0.17.0      # or 0.17.0 — both forms are accepted
```

The script refuses on a dirty tree or off `main`, promotes the CHANGELOG's `## Unreleased` section to `## v0.17.0 (date)` and each `Fixed in: unreleased` in `docs/troubleshooting.md` to the tag, bumps `pyproject.toml` — and, in lockstep, `packages/taxjson-fetch/pyproject.toml` (the broker-fetch plugin's version and its `taxjson>=` floor) — runs the **full** local gate, then commits, tags, and pushes `main` and the one tag, by name — never `git push --tags` (a checkout can hold tags that must not be public; the pre-push hook refuses any other tag). It then creates the GitHub release (`gh release create vX.Y.Z --verify-tag -t "taxjson vX.Y.Z"`) with the CHANGELOG's `## vX.Y.Z` section as its notes, or with `--notes FILE` (`scripts/release.sh v0.17.0 --notes notes.md`). The notes are published apart from the code, so no push hook sees them, and GitHub releases are immutable, so they are scanned with `scripts/check-pii.sh --message` (the commit-message scan: denylist, figure list, generic patterns, money amounts) before anything is pushed and again right before the release is created; a hit stops the release. Without `gh`, or with `gh` not logged in, the script stops after the push and prints the exact command to finish with. It does not touch `channels.json`: the new release is `latest`, and `stable` / `beta` stay where they were until you promote. The gate runs the test suite only: a handful of tests drive the real pipeline, but on synthetic two-trade projects written into temporary directories and deleted afterwards. Nothing in the suite or the release script reads a real tax project. The gate also runs `scripts/check-pii.sh` (personal data and secrets, plus the maintainer's private denylist and the maintainer's private figure list), the same scan the `pre-push` hook applies to every push. Refresh the figure list after each year's run, before releasing — `scripts/check-pii.sh --collect-amounts <project dir>...` (salted hashes of the figures in each project's `reports/`, `work/*.sum`, `*.toml` and `*.tt`, and of the distinctive amounts, prices, quantities, reference codes and clock times in its raw `inputs/` exports, merged into `~/.config/taxjson/pii-amounts`; see CONTRIBUTING.md) — so no figure from your own books can reach the tree, a pushed commit or a commit or tag message. GitHub Actions is not part of the gate — the local run is the source of truth (see CONTRIBUTING.md).

One tag covers both distributions: the core (`taxjson`) and the broker-fetch plugin (`taxjson-fetch`, `packages/taxjson-fetch`), which the installer installs by default from the same checkout (`--without-fetch` leaves it out). To publish wheels as well, build each from the tagged tree — `python -m build` and `python -m build packages/taxjson-fetch` — and upload both.

Semantic versioning, applied to tax output: a change that alters any filed number for an already-supported input is at least a minor bump and gets a CHANGELOG entry that names the rule and the direction of the change. Parser additions, new commands, and report-format changes are minor; documentation and internal refactors are patch.

## The tag guard

Never `git push --tags`: `scripts/release.sh` pushes the one tag, by name. The `pre-push` hook (`scripts/hooks/pre-push`, installed by `scripts/dev-setup.sh`) refuses a push that carries any tag that is not named `vX.Y.Z`, is a lightweight tag, or points at a commit that is not on the remote's `main` (or on the `main` the same push sends), and every push that deletes or moves a tag. On GitHub, a ruleset refuses creating, moving or deleting the old pre-release tag ranges, and releases are immutable.

`scripts/dev-setup.sh` (or `scripts/dev-setup.sh --hook-only`) installs the hook into the clone's own hooks folder (`git rev-parse --git-common-dir`/hooks, shared by every worktree; the installed file runs the pushing worktree's own `scripts/hooks/pre-push`). It does so even when a global `core.hooksPath` is set — git then runs that folder's `pre-push` instead — and says whether that global hook chains to the clone's own (one that runs `"$(git rev-parse --git-common-dir)/hooks/pre-push"` does); when it does not, it warns that the guard will not run.

## Rolling back

Never move or delete a published tag — installers may already have it. Roll the channel back instead: `scripts/promote.sh v0.16.0` (it asks before moving `stable` backwards), and new installs on `stable` get the good release again. Installs already on the bad release stay there — a channel never moves an install backwards — until the fix ships: fix it on `main`, cut the next patch (`scripts/release.sh v0.17.1`), and promote that. A user who cannot wait pins the good release: `--channel v0.16.0`.

## Your own production install

Develop in the clone (`scripts/dev-setup.sh`, then `source setup.sh`), but run real books on a release: the installer's checkout under `~/.local/share/taxjson` (`TAXJSON_PROD_DIR` tells the release verbs when it is elsewhere) is the production copy, and `taxjson` on your PATH points at it. `tjs deploy` puts each new tag there; `--channel dev` switches that copy to `main` when you want to test a fix on real data before tagging. There is no follow-a-channel timer (Hatchabot's `follow-channel.sh`): a tax tool upgrades when you re-run the installer, not behind your back.
