#!/usr/bin/env bash
# Make a release, in two steps, so GitHub CI has passed on the very commit
# that is tagged (docs/releasing.md, "Cutting a release"):
#
#   scripts/release.sh vX.Y.Z --prepare [--fresh-gate] [--gate-max-age DAYS]
#   scripts/release.sh vX.Y.Z                        # the cut, once CI passed
#   (X.Y.Z also accepted)
#
# main is the development line; a vX.Y.Z tag is a release. Tagging makes
# the release `latest` and nothing more: new installs take `stable`, which
# channels.json on main names and only scripts/promote.sh moves.
#
# --prepare makes the VERSION COMMIT and lands it on main, without a tag:
#   1. refuses on a dirty tree, off main, behind origin/main, when the
#      tag exists, or when an earlier --prepare's version commit, not yet
#      landed, has commits on top of it (it must land last: GitHub CI
#      runs on the head of a push only);
#   2. turns the CHANGELOG's "## Unreleased" into "## vX.Y.Z (date)" (or
#      requires that heading to exist already), each "Fixed in:
#      unreleased" in docs/troubleshooting.md into the tag, and
#      docs/upgrading.md's "### Unreleased" (when there is one) into
#      "### vX.Y.Z";
#   3. bumps pyproject.toml and, in lockstep, the taxjson-fetch plugin's
#      packages/taxjson-fetch/pyproject.toml (its version and its
#      `taxjson>=` floor), reinstalls so `taxjson --version` agrees;
#   4. scans the release notes (the CHANGELOG section: what the public
#      repo's release.yml publishes; scripts/release-notes.sh)
#      with `scripts/check-pii.sh --message` before anything is committed;
#   5. runs the FULL local gate (scripts/ci.sh, fuzzers included) — unless
#      the gate already PASSED on this very tree (scripts/gate-record.sh:
#      same tree hash, a full mode, the same Python, within --gate-max-age
#      days, default 7) and the edits of steps 2-3 are proven to be the
#      only change (release_edits_only); then only the checks that read
#      those lines (scripts/ci.sh --release-edits). --fresh-gate always
#      runs the full gate;
#   6. commits "release vX.Y.Z" and publishes main through publish_main
#      (scripts/land.sh: a pull request, rebase-merged once its checks
#      pass; the pre-push PII gate runs first). GitHub CI then runs on the
#      push of the version commit to main.
#
# The cut (no --prepare) tags that commit and pushes the tag to origin,
# the private dev repo. It refuses unless local main is exactly
# origin/main, the version commit is on main (the commit that made
# pyproject.toml say X.Y.Z, with the CHANGELOG's "## vX.Y.Z (date)"
# heading and the plugin's version), the tag is free here, on origin and
# on the public repo (the remote `public`), and GitHub CI (tests.yml)
# passed on the push of that exact commit to main (scripts/github-ci.sh: a
# run still going is waited for, a failed one is named). It then scans the
# notes, makes the annotated tag on the version commit, runs the pre-push
# gate on it and pushes that one tag by name (never `git push --tags`). A
# refused tag push deletes the local tag again.
#
# The publisher does the rest (docs/releasing.md, "How a release reaches
# the public repo"): it gates the tag, waits, gates again and pushes one
# release commit and the tag to the public repo, whose release.yml then
# creates the GitHub release from the same CHANGELOG section.
#
# It never touches channels.json. Roll a bad release back by promoting the
# previous good one (scripts/promote.sh vX.Y.Z — it asks before moving a
# channel backwards) — never by moving or deleting a published tag.
set -euo pipefail
USAGE="usage: scripts/release.sh vX.Y.Z --prepare [--fresh-gate] [--gate-max-age DAYS]
       scripts/release.sh vX.Y.Z      (the cut, once GitHub CI passed on the version commit)
       (X.Y.Z also accepted)"
V=""; FRESH_GATE=""; GATE_MAX_AGE=""; PREPARE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --prepare) PREPARE=1; shift ;;
    --notes|--notes=*) echo "--notes is gone: the release notes are the CHANGELOG's '## vX.Y.Z' section, which the public repo's release.yml publishes — edit that"; exit 1 ;;
    --fresh-gate) FRESH_GATE=1; shift ;;
    --gate-max-age) [ $# -ge 2 ] || { echo "--gate-max-age needs a number of days"; exit 1; }; GATE_MAX_AGE="$2"; shift 2 ;;
    --gate-max-age=*) GATE_MAX_AGE="${1#--gate-max-age=}"; shift ;;
    -h|--help) echo "$USAGE"; exit 0 ;;
    -*) echo "unknown option '$1' — $USAGE"; exit 1 ;;
    *) [ -z "$V" ] || { echo "$USAGE"; exit 1; }; V="$1"; shift ;;
  esac
done
[ -n "$V" ] || { echo "$USAGE"; exit 1; }
if [ -z "$PREPARE" ] && { [ -n "$FRESH_GATE" ] || [ -n "$GATE_MAX_AGE" ]; }; then
  echo "--fresh-gate and --gate-max-age belong to --prepare (the cut runs no local gate: GitHub CI on the version commit is its check)"; exit 1
fi
GATE_MAX_AGE="${GATE_MAX_AGE:-7}"
[[ "$GATE_MAX_AGE" =~ ^[0-9]+$ ]] || { echo "--gate-max-age must be a whole number of days (got '$GATE_MAX_AGE')"; exit 1; }
cd "$(dirname "$0")/.."
V="${V#v}"                                   # vX.Y.Z and X.Y.Z both accepted
[[ "$V" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "version must be vX.Y.Z or X.Y.Z (got 'v$V')"; exit 1; }
TAG="v$V"
VRE="${V//./\\.}"
PY="${PYTHON:-$PWD/venv/bin/python3}"
FETCH_TOML=packages/taxjson-fetch/pyproject.toml
Z=0000000000000000000000000000000000000000
# The dev repository, whose CI is checked: TAXJSON_SLUG, else origin's
# github.com address.
SLUG="${TAXJSON_SLUG:-$(git remote get-url origin | sed -nE 's#^.*github\.com[:/]+([^/]+/[^/]+)$#\1#p' | sed 's/\.git$//')}"
# The public repo, read only (its push URL is DISABLED): a tag name it has
# is used for good (immutable releases).
PUBLIC_REMOTE="${TAXJSON_PUBLIC_REMOTE:-public}"

[ "$(git rev-parse --abbrev-ref HEAD)" = main ] || { echo "release from main only"; exit 1; }
# The pre-push PII gate must run, and main is published only through
# land.sh: a checkout without them (or without the scanner the gate
# calls) is refused before anything is changed.
for f in scripts/hooks/pre-push scripts/check-pii.sh scripts/land.sh scripts/github-ci.sh scripts/release-notes.sh; do
  [ -f "$f" ] || { echo "$f is missing from this checkout — the pre-push PII gate cannot run, so nothing is released"; exit 1; }
done
git remote get-url "$PUBLIC_REMOTE" >/dev/null 2>&1 || {
  echo "no remote '$PUBLIC_REMOTE' for the public repo — add it, read only: git remote add --no-tags public https://github.com/taxjson/taxjson.git && git remote set-url --push public DISABLED"; exit 1; }
if [ -n "$(git status --porcelain)" ]; then
  echo "working tree is not clean — commit or stash first:"; git status --porcelain | sed 's/^/    /'; exit 1
fi
git fetch --tags --quiet origin
tag_free() {
  if [ -n "$(git ls-remote --tags origin "refs/tags/$TAG")" ]; then
    echo "$TAG already exists on origin — a version is released once; make the next patch version"; exit 1
  fi
  if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
    echo "$TAG already exists in this clone (not on origin) — delete it (git tag -d $TAG) and run again"; exit 1
  fi
  local pub
  pub="$(git ls-remote --tags "$PUBLIC_REMOTE" "refs/tags/$TAG")" \
    || { echo "could not read the public repo's tags ($PUBLIC_REMOTE) — nothing done"; exit 1; }
  if [ -n "$pub" ]; then
    echo "$TAG already exists on the public repo — a version is released once; make the next patch version"; exit 1
  fi
}
tag_free

# The release notes: the `## vX.Y.Z` section of the given CHANGELOG, in a
# file of their own while they are scanned.
NOTES=""
drop_notes() { [ -z "$NOTES" ] || rm -f "$NOTES"; }
make_notes() {   # make_notes CHANGELOG-FILE
  NOTES="$(mktemp "${TMPDIR:-/tmp}/taxjson-notes-$TAG.XXXXXX")"
  bash scripts/release-notes.sh "$TAG" "$1" > "$NOTES"
  [ -s "$NOTES" ] || { rm -f "$NOTES"; echo "release notes are empty (the CHANGELOG's '## $TAG' section) — write them first"; exit 1; }
}
# Release notes are published apart from the code (the public repo's
# release.yml makes them the GitHub release). Fail closed: any hit or
# scanner error stops the release.
scan_notes() {
  if ! scripts/check-pii.sh --message < "$NOTES"; then
    echo "release notes REFUSED by scripts/check-pii.sh --message ($1). Fix the notes (the CHANGELOG's '## $TAG' section) and run again."
    return 1
  fi
}

# ---- publishing main: the ONE place ----------------------------------------
# main takes pull requests only: scripts/land.sh pushes the new commits as
# a branch, opens the pull request, has it rebase-merged once its checks
# pass and brings local main to what landed (it runs the pre-push PII gate
# first). Every push of main this script makes goes through here. Those
# checks include the publish kit's (kit-checks.yml, on the very tree the
# cut tags), so --prepare runs no scripts/kit_checks.py of its own
# (docs/releasing.md, "Dependency and code checks").
publish_main() {
  bash scripts/land.sh
}

if [ -n "$PREPARE" ]; then
  # ==== --prepare: the version commit, landed on main, not tagged ==========
  git merge-base --is-ancestor origin/main HEAD || { echo "local main is behind origin/main — pull first"; exit 1; }
  if grep -q "^## $TAG " CHANGELOG.md && grep -qx "version = \"$V\"" pyproject.toml \
     && [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ]; then
    echo "$TAG is already prepared: its version commit is on main. Once GitHub CI has passed on it: scripts/release.sh $TAG"
    drop_notes; exit 0
  fi
  # The version commit lands LAST: GitHub runs tests.yml on the head of a
  # push only, and the cut tags only a version commit whose own push to
  # main passed. One an earlier --prepare made but could not land, with a
  # commit on top of it now (a fix for the check that failed), would land
  # in the middle of its push and never get a run of its own.
  VC=""
  for c in $(git log --format=%H -G"^version = \"$VRE\"\$" origin/main..HEAD -- pyproject.toml); do
    if git show "$c:pyproject.toml" | grep -qx "version = \"$V\""; then VC="$c"; break; fi
  done
  if [ -n "$VC" ] && [ "$VC" != "$(git rev-parse HEAD)" ]; then
    echo "the version commit of $TAG (${VC:0:9}) is not the last commit on local main: $(git rev-list --count "$VC..HEAD") commit(s) follow it, so it would land in the middle of a push, and GitHub CI runs on the head of a push only (the cut would find no run on it). Take it out and prepare again, which makes it anew on top: git rebase --onto ${VC:0:9}~1 ${VC:0:9}, then scripts/release.sh $TAG --prepare"
    exit 1
  fi
  # A PASS of the full gate on this exact tree (the clean tree being
  # released, before the release's own edits), recorded by scripts/ci.sh.
  PRE_TREE="$(git rev-parse 'HEAD^{tree}')"
  REUSE=""
  if [ -z "$FRESH_GATE" ]; then
    PYVER="$("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || true)"
    REUSE="$(bash scripts/gate-record.sh find "$PRE_TREE" "$PYVER" "$GATE_MAX_AGE" 2>/dev/null || true)"
  fi
  # The release's own edits (below) and nothing else: every file but the
  # five it edits as HEAD has it, nothing untracked, and each of the five
  # equal to HEAD's once the lines it rewrites are masked — the version
  # lines, the plugin's `taxjson>=` floor, the CHANGELOG heading (and the
  # fresh Unreleased heading above it), the playbook's Fixed-in tag, the
  # upgrade notes' heading.
  release_edits_only() {
    local line f st tagre
    tagre="v$VRE"
    while IFS= read -r line; do
      st="${line:0:2}"; f="${line:3}"
      case "$f" in CHANGELOG.md|docs/troubleshooting.md|docs/upgrading.md|pyproject.toml|"$FETCH_TOML") ;;
        *) echo "   $f is changed besides the release edits"; return 1 ;; esac
      [ "$st" = " M" ] || { echo "   $f: status '$st'"; return 1; }
    done < <(git status --porcelain --untracked-files=all)
    same() {   # same FILE PRE-SED POST-SED [POST-AWK]
      cmp -s <(git show "HEAD:$1" | sed -E "$2") \
             <(if [ -n "${4:-}" ]; then awk "$4" "$1"; else cat "$1"; fi | sed -E "$3") \
        || { echo "   $1 differs from HEAD beyond the release edits"; return 1; }
    }
    same pyproject.toml 's/^version = "[^"]*"/version = @V@/' "s/^version = \"$VRE\"/version = @V@/" || return 1
    same "$FETCH_TOML" 's/^version = "[^"]*"/version = @V@/; s/"taxjson>=[^"]*"/"taxjson>=@V@"/' \
         "s/^version = \"$VRE\"/version = @V@/; s/\"taxjson>=$VRE\"/\"taxjson>=@V@\"/" || return 1
    same docs/troubleshooting.md 's/^- \*\*Fixed in:\*\* unreleased$/@FIXED@/' \
         "s/^- \\*\\*Fixed in:\\*\\* \`$tagre\`\$/@FIXED@/" || return 1
    [ ! -f docs/upgrading.md ] \
      || same docs/upgrading.md 's/^### Unreleased$/@HEAD@/' "s/^### $tagre\$/@HEAD@/" || return 1
    # The new file is line 1, a blank, "## Unreleased", a blank, then the
    # old file from its line 2 with "## Unreleased…" renamed to the tag.
    same CHANGELOG.md 's/^## Unreleased.*/@HEAD@/' \
         "s/^## $tagre \([0-9]{4}-[0-9]{2}-[0-9]{2}\)\$/@HEAD@/" \
         'NR == 1 { print; next }
          NR <= 4 { if ((NR == 3) != ($0 == "## Unreleased") || (NR != 3 && $0 != "")) print "@NOT-THE-RELEASE-LAYOUT@"; next }
          { print }' || return 1
  }

  # CHANGELOG: promote Unreleased, or accept an existing heading.
  if grep -q "^## $TAG " CHANGELOG.md; then
    :
  elif grep -q "^## Unreleased" CHANGELOG.md; then
    sed -i "s/^## Unreleased.*/## $TAG ($(date +%F))/" CHANGELOG.md
    { echo "# Changelog"; echo; echo "## Unreleased"; echo; tail -n +2 CHANGELOG.md; } > CHANGELOG.md.new
    mv CHANGELOG.md.new CHANGELOG.md
  else
    echo "CHANGELOG.md has neither '## Unreleased' nor '## $TAG'"; exit 1
  fi
  # The notes, scanned now — before the gate, the commit or the push — so
  # a hit costs nothing. The cut scans them again.
  make_notes CHANGELOG.md
  scan_notes "nothing committed or pushed; CHANGELOG/pyproject edits left for you to inspect" || { drop_notes; exit 1; }
  rm -f "$NOTES"
  # The playbook's entries fixed since the last release name this one.
  sed -i "s/^- \*\*Fixed in:\*\* unreleased\$/- **Fixed in:** \`$TAG\`/" docs/troubleshooting.md
  # The upgrade notes written since the last release are this one's. No
  # fresh Unreleased heading: a release that asks nothing is not listed.
  [ ! -f docs/upgrading.md ] || sed -i "s/^### Unreleased\$/### $TAG/" docs/upgrading.md
  sed -i "s/^version = \"[^\"]*\"/version = \"$V\"/" pyproject.toml
  grep -q "^version = \"$V\"" pyproject.toml || { echo "pyproject version bump failed"; exit 1; }
  # The broker-fetch plugin ships from the same tag, version for version,
  # and needs at least this core (the `taxjson fetch` plugin interface).
  sed -i -e "s/^version = \"[^\"]*\"/version = \"$V\"/" \
         -e "s/\"taxjson>=[^\"]*\"/\"taxjson>=$V\"/" "$FETCH_TOML"
  grep -q "^version = \"$V\"" "$FETCH_TOML" && grep -q "\"taxjson>=$V\"" "$FETCH_TOML" \
    || { echo "taxjson-fetch version bump failed"; exit 1; }
  "$PY" -m pip install -e . --quiet
  # The plugin's version moved too: an editable install's metadata keeps
  # the version it was installed at, so a venv that has the plugin (the
  # dev venv) reinstalls it as well; the egg-info a build leaves beside
  # the sources goes (scripts/editable_check.py, backlog TJ-0636).
  if "$PY" -m pip show -q taxjson-fetch >/dev/null 2>&1; then
    "$PY" -m pip install --no-deps -e packages/taxjson-fetch --quiet
  fi
  rm -rf src/*.egg-info packages/*/src/*.egg-info
  [ "$("$PY" -m taxjson.bin.taxjson_run --version)" = "taxjson $V" ] || { echo "taxjson --version disagrees with $V"; exit 1; }

  if [ -n "$REUSE" ] && ! release_edits_only; then
    echo "gate: not reusing $REUSE — the working tree differs from the gated tree by more than the release edits"
    REUSE=""
  fi
  if [ -n "$REUSE" ]; then
    echo "== gate: reusing the PASS of tree ${PRE_TREE:0:12} — $REUSE ($(sed -n 's/^mode=//p' "$REUSE") gate, Python $(sed -n 's/^python=//p' "$REUSE"), passed $(sed -n 's/^passed_utc=//p' "$REUSE")); --fresh-gate runs it again =="
    echo "== release-edit checks (the version, tag and date lines just written) =="
    scripts/ci.sh --release-edits || { echo "release-edit checks FAILED — release aborted (CHANGELOG/pyproject edits left for you to inspect; --fresh-gate runs the full gate)"; exit 1; }
  else
    echo "== full gate =="
    scripts/ci.sh || { echo "gate FAILED — release aborted (CHANGELOG/pyproject edits left for you to inspect)"; exit 1; }
  fi

  git add CHANGELOG.md pyproject.toml "$FETCH_TOML" docs/troubleshooting.md
  [ ! -f docs/upgrading.md ] || git add docs/upgrading.md
  # An earlier run may already have committed the bump: publish it then.
  git diff --cached --quiet || git commit -q -m "release $TAG"
  if ! publish_main; then
    echo "the version commit of $TAG is made here but NOT on main (above). A flaky check: land it as it is (scripts/land.sh), then, once GitHub CI has passed on it: scripts/release.sh $TAG. A check that needs a fix: take the version commit back first (git reset --keep HEAD~1), commit the fix and land it, then scripts/release.sh $TAG --prepare again — the version commit must be the last commit of its push, the one GitHub CI runs on."
    exit 1
  fi
  echo "prepared $TAG: the version commit $(git rev-parse --short HEAD) is on main; GitHub CI runs on it now (https://github.com/${SLUG:-taxjson/taxjson}/actions/workflows/tests.yml)."
  echo "Once it has passed, cut the release: scripts/release.sh $TAG"
  exit 0
fi

# ==== the cut: tag the version commit once GitHub CI passed on it ==========
# gh answers the CI question: without it nothing is tagged.
if ! command -v gh >/dev/null 2>&1 || ! gh auth status >/dev/null 2>&1; then
  echo "gh is $(command -v gh >/dev/null 2>&1 && echo 'not logged in (gh auth login)' || echo 'not installed') — the cut needs it to check GitHub CI; nothing tagged."
  drop_notes; exit 1
fi
AHEAD="$(git rev-list --count origin/main..HEAD)"; BEHIND="$(git rev-list --count HEAD..origin/main)"
if [ "$AHEAD" != 0 ] || [ "$BEHIND" != 0 ]; then
  echo "local main is not origin/main ($AHEAD commit(s) ahead, $BEHIND behind) — the release is cut from what GitHub's main holds: land or pull first (the version commit is made by scripts/release.sh $TAG --prepare)"
  drop_notes; exit 1
fi
# The version commit: the newest commit on main's first-parent line whose
# change made pyproject.toml say this version (found by content: a rebase
# merge gives the landed commit a new id).
C=""
for c in $(git log --first-parent --diff-merges=first-parent --format=%H -G"^version = \"$VRE\"\$" origin/main -- pyproject.toml); do
  if git show "$c:pyproject.toml" | grep -qx "version = \"$V\""; then C="$c"; break; fi
done
if [ -z "$C" ]; then
  echo "no version commit for $TAG on main (pyproject.toml never says $V there) — run scripts/release.sh $TAG --prepare first"
  drop_notes; exit 1
fi
AT="$(mktemp -d "${TMPDIR:-/tmp}/taxjson-release-$TAG.XXXXXX")"
git show "$C:CHANGELOG.md" > "$AT/CHANGELOG.md"
git show "$C:$FETCH_TOML" > "$AT/fetch.toml" 2>/dev/null || : > "$AT/fetch.toml"
problem=""
grep -Eq "^## $TAG \([0-9]{4}-[0-9]{2}-[0-9]{2}\)\$" "$AT/CHANGELOG.md" || problem="its CHANGELOG.md has no '## $TAG (date)' heading"
{ grep -qx "version = \"$V\"" "$AT/fetch.toml" && grep -q "\"taxjson>=$V\"" "$AT/fetch.toml"; } \
  || problem="${problem:+$problem; }its $FETCH_TOML is not version $V with taxjson>=$V"
if [ -n "$problem" ]; then
  echo "the version commit ${C:0:9} of $TAG is not a release commit: $problem — prepare it with scripts/release.sh $TAG --prepare"
  rm -rf "$AT"; exit 1
fi
LATER="$(git rev-list --count "$C..origin/main")"
echo "== $TAG: the version commit is ${C:0:9} \"$(git log -1 --format=%s "$C")\"$([ "$LATER" = 0 ] || echo " — main has $LATER later commit(s); they are not in $TAG") =="
make_notes "$AT/CHANGELOG.md"
rm -rf "$AT"
scan_notes "nothing tagged or pushed" || { drop_notes; exit 1; }
# GitHub CI on the push of exactly this commit to main (waits for a run
# still going; names a failed one).
if ! bash scripts/github-ci.sh "$C" "the version commit of $TAG" --slug "${SLUG:-taxjson/taxjson}" \
       --appear-wait "${TAXJSON_CI_APPEAR_SECS:-180}"; then
  echo "GitHub CI has not passed on the version commit ${C:0:9} (above) — nothing tagged. Re-run a flaky run on GitHub and cut again, or fix main and prepare the next version."
  drop_notes; exit 1
fi
tag_free
git tag -a "$TAG" "$C" -m "taxjson $TAG"
# The pre-push PII gate (the tag message and tagger, the tag guard) runs
# here whether or not this clone has the hook installed (through bash: a
# lost executable bit does not skip it).
if ! printf 'refs/tags/%s %s refs/tags/%s %s\n' "$TAG" "$(git rev-parse "$TAG")" "$TAG" "$Z" \
     | bash scripts/hooks/pre-push origin "$(git remote get-url origin)"; then
  git tag -d "$TAG" >/dev/null
  echo "pre-push PII gate refused the tag — nothing pushed (the local tag $TAG is deleted again)"
  drop_notes; exit 1
fi
# One tag, by name — never `git push --tags` (a checkout can hold tags
# that must not be public; the pre-push hook refuses any other tag).
if ! git push --quiet origin "refs/tags/$TAG"; then
  git tag -d "$TAG" >/dev/null
  echo "the push of $TAG was refused — nothing published (the local tag is deleted again)"
  drop_notes; exit 1
fi
rm -f "$NOTES"
STABLE="$(git show origin/main:channels.json 2>/dev/null | sed -nE 's/.*"stable"[[:space:]]*:[[:space:]]*"(v[^"]+)".*/\1/p' | sed -n 1p || true)"
echo "tagged $TAG on the dev repo. The publisher gates it, waits, gates it again and publishes it to the public repo, where release.yml creates the GitHub release; then it is 'latest'."
echo "stable is still ${STABLE:-unset}; once $TAG is published and has held up: scripts/promote.sh $TAG beta / scripts/promote.sh $TAG"
# The tag covers both distributions (the installer installs the plugin
# from the same checkout by default). To publish wheels as well:
#   python -m build && python -m build packages/taxjson-fetch
#   twine upload dist/taxjson-$V* packages/taxjson-fetch/dist/taxjson_fetch-$V*
