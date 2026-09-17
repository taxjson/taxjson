# Releasing

taxjson has one development line and a sequence of production tags.

| Line | What it is | Who runs it |
| --- | --- | --- |
| `main` | Development. Every commit passes `scripts/ci.sh --quick`; the full gate before a release. | Contributors, and the `TAXJSON_CHANNEL=dev` installer channel. |
| `vX.Y.Z` tags | Production. Immutable; each one passed the full gate (fuzzers included) at cut time. | Everyone who used the one-line installer, which checks out the newest tag. |

## Cutting a release

```bash
scripts/release.sh 0.15.0
```

The script refuses on a dirty tree or off `main`, promotes the CHANGELOG's `## Unreleased` section to `## v0.15.0 (date)`, bumps `pyproject.toml`, runs the **full** local gate, then commits, tags, and pushes `main` and the tag. The gate runs the test suite only: a handful of tests drive the real pipeline, but on synthetic two-trade projects written into temporary directories and deleted afterwards. Nothing in the suite or the release script reads a real tax project. The gate also runs `scripts/check-pii.sh` (personal data and secrets, plus the maintainer's private denylist), the same scan the `pre-push` hook applies to every push. GitHub Actions is not part of the gate — the local run is the source of truth (see CONTRIBUTING.md).

Semantic versioning, applied to tax output: a change that alters any filed number for an already-supported input is at least a minor bump and gets a CHANGELOG entry that names the rule and the direction of the change. Parser additions, new commands, and report-format changes are minor; documentation and internal refactors are patch.

## Rolling back

Never move or delete a published tag — installers may already have it. Tag the last good commit as the next patch version instead:

```bash
git checkout v0.15.0
git checkout -b hotfix && git cherry-pick <fix> && …
scripts/release.sh 0.15.1
```

## Your own production install

Develop in the clone (`scripts/dev-setup.sh`, then `source setup.sh`), but run real books on a release: the installer's checkout under `~/.local/share/taxjson` is the production copy, and `taxjson` on your PATH points at it. Re-running the installer upgrades it to the newest tag; `TAXJSON_CHANNEL=dev` switches that copy to `main` when you want to test a fix on real data before tagging.
