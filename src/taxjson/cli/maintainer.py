"""The Maintainer commands: channels, deploy, promote, parser-check."""
import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

from taxjson.pipeline.messages import _die, _die_input, _PROG
from taxjson.lib import out as _out


def cmd_channels(args: argparse.Namespace) -> None:
    """`tjs channels [all] [--json] [--offline]`: where the release
    channels point (stable / beta in channels.json on main, latest = the
    newest release tag), what this machine's production copy runs, and
    the newest releases (lib/channels). Reads the development checkout
    when there is one, else the production copy's clone; one `git fetch`
    of that clone's own remote, skipped offline."""
    import json
    from taxjson.lib import channels as ch
    try:
        repo, kind = ch.source_checkout()
        st = ch.status(repo, fetch_remote=not args.offline,
                       limit=None if args.scope == "all"
                       else ch.DEFAULT_LIMIT, source_kind=kind)
    except ch.ChannelsError as e:
        _die_input(str(e))
    # A live exact Doc prints the line as the raw print() did.
    d = _out.Doc(exact=True, width_=0, live=True)
    if args.json:
        from taxjson.lib.json_contract import versioned
        d.line(json.dumps(versioned(st, "channels"), indent=2))
    else:
        d.line(ch.render(st, _PROG))


def _dev_checkout(probe: str, absent: str) -> Path:
    """The development checkout (lib/channels.dev_checkout: TAXJSON_DEV_DIR,
    else this package's own editable checkout) holding `probe`; a
    variable pointing elsewhere is refused in its own words, no checkout
    with `absent`."""
    from taxjson.lib import channels as ch
    try:
        dev = ch.dev_checkout()
    except ch.ChannelsError as e:
        _die_input(str(e))
    if dev is None or not (dev / probe).exists():
        _die_input(absent)
    return dev


def _release_dev_checkout(verb: str) -> Path:
    """The development checkout `promote` / `deploy` act from, or a clear
    refusal: they publish or install releases, which is the maintainer's
    machine's job (anywhere else, re-run the installer)."""
    return _dev_checkout(
        "scripts/promote.sh",
        f"`{verb}` is for the machine taxjson is developed on, and no "
        f"development checkout was found (set TAXJSON_DEV_DIR to it, or "
        f"run that checkout's own editable install). To update this "
        f"machine, re-run the installer: bash -c \"$(curl -fsSL "
        f"https://taxjson.com/install.sh)\"; `{_PROG} channels` shows "
        f"where the channels point.")


def _run_script(argv: List[str], env: Optional[Dict[str, str]] = None
                ) -> None:
    """Run a release script on this terminal (promote asks before moving
    a channel backwards) and exit with its status."""
    r = subprocess.run(argv, env=env)
    if r.returncode:
        raise SystemExit(r.returncode)


def cmd_promote(args: argparse.Namespace) -> None:
    """`tjs promote [TAG] [stable|beta]`: point a channel at a
    release — scripts/promote.sh in the development checkout: it commits
    channels.json, lands it on main (scripts/land.sh) and pushes a
    fast-lane tag `channels-<UTC time>` on that commit, which asks the
    publisher to copy channels.json to the public repo. No new release
    tag, no rebuild (backlog TJ-0337). Without TAG: the release this
    machine's production copy runs."""
    from taxjson.lib import channels as ch
    dev = _release_dev_checkout("promote")
    tag, channel = args.tag, args.channel
    if tag in ch.CHANNELS and channel is None:
        tag, channel = None, tag            # `promote beta`
    channel = channel or "stable"
    if not tag:
        tag = ch.this_box().get("release")
        if not tag:
            _die_input(f"this machine's production copy "
                       f"({ch.prod_dir()}) is not on a release — say which: "
                       f"{_PROG} promote vX.Y.Z [stable|beta]")
        _out.put(f"promoting what this machine runs: {tag}")
    _run_script(["bash", str(dev / "scripts" / "promote.sh"), tag, channel])


def cmd_deploy(args: argparse.Namespace) -> None:
    """`tjs deploy [TAG]`: put a release on this machine's production
    copy now — the newest tag, or TAG — through the installer's upgrade
    path (the development checkout's install.sh run against the
    production copy), then checks that the copy is at that tag and its
    `tjs --version` agrees (exit 1 when not). The remembered channel is left as it is; the
    installer never moves a channel backwards, so the copy stays on TAG
    until its channel passes it. No fetch flag is passed: the installer
    installs taxjson-fetch unless this machine opted out (remembered in
    ~/.config/taxjson/fetch) or TAXJSON_WITH_FETCH says otherwise."""
    import os
    from taxjson.lib import channels as ch
    dev = _release_dev_checkout("deploy")
    prod = ch.prod_dir()
    if not ch.is_checkout(prod):
        _die_input(f"no production copy at {prod} — install it first with "
                   f"the installer (bash -c \"$(curl -fsSL "
                   f"https://taxjson.com/install.sh)\"), or set "
                   f"TAXJSON_PROD_DIR")
    target = "latest"
    if args.tag:
        target = args.tag if args.tag.startswith("v") else f"v{args.tag}"
        if not ch.TAG_RE.match(target):
            _die_input(f"{args.tag!r} is not a release — give vX.Y.Z "
                       f"(or nothing, for the newest)")
    env = dict(os.environ, TAXJSON_DIR=str(prod),
               TAXJSON_REMEMBER_CHANNEL="0")
    _run_script(["bash", str(dev / "install.sh"), "--channel", target], env)
    # The installer said it worked; check what now runs is the release
    # (a pip install that silently kept an older build, a copy left on
    # another commit) — loudly, exit 1, never a quiet mismatch.
    want, got, problems = ch.check_deployed(prod, target)
    if problems:
        _die(f"deploy: the production copy does not run "
             f"{want or 'a release'}", *problems)
    _out.put(f"deployed {want}: {prod} runs {got}")


def _register_channels(sub: argparse._SubParsersAction) -> None:
    # Release verbs (docs/releasing.md). `channels` works anywhere a
    # taxjson checkout is (the development one, else the production
    # copy); `promote` and `deploy` only where the development checkout
    # is — they refuse anywhere else.
    p_chan = sub.add_parser(
        "channels",
        help="Where stable, beta and latest point; releases",
        description="Where each release channel points — stable and beta "
             "as channels.json on main names them, latest the newest "
             "release tag — what this machine's production copy runs "
             "(~/.local/share/taxjson, or TAXJSON_PROD_DIR) and on which "
             "channel, and the newest 20 releases with their date and "
             "first CHANGELOG entry. Reads the development checkout "
             "(TAXJSON_DEV_DIR, or this package's own git checkout), "
             "else the production copy's clone, after one `git fetch` of "
             "its remote; offline (or TAXJSON_OFFLINE=1) it says so and "
             "shows what the clone knows.")
    p_chan.add_argument("scope", nargs="?", choices=["all"],
                        help="List every release, not just the newest 20")
    p_chan.add_argument("--offline", action="store_true",
                        help="Do not fetch; show what the clone knows")
    p_chan.add_argument("--json", action="store_true",
                        help="Emit JSON instead of text")
    p_chan.set_defaults(func=cmd_channels)


def _register_promote(sub: argparse._SubParsersAction) -> None:
    p_prom = sub.add_parser(
        "promote",
        help="Point stable or beta at a release (dev machine)",
        description="Point a release channel at a release: what new "
             "installs on that channel (and re-runs of the installer) "
             "get from now on. Runs scripts/promote.sh in the development "
             "checkout: the tag must exist, the checkout must be on main "
             "with a clean channels.json, and moving a channel BACKWARDS "
             "asks first; it commits channels.json (\"Promote vX.Y.Z to "
             "stable\"), lands it on main and pushes a channels-<UTC "
             "time> tag on that commit, which asks the publisher to copy "
             "channels.json to the public repo — no new release tag, no "
             "rebuild. Without TAG: the release this machine's production "
             "copy runs. Refused where no development checkout is found.")
    p_prom.add_argument("tag", nargs="?", metavar="TAG",
                        help="The release, vX.Y.Z (default: what this "
                             "machine runs)")
    p_prom.add_argument("channel", nargs="?", metavar="CHANNEL",
                        help="stable (default) or beta")
    p_prom.set_defaults(func=cmd_promote)


def _register_deploy(sub: argparse._SubParsersAction) -> None:
    p_dep = sub.add_parser(
        "deploy",
        help="Update this machine to a release (dev machine)",
        description="Update this machine's production copy "
             "(~/.local/share/taxjson, or TAXJSON_PROD_DIR) to the newest "
             "release tag, or to TAG, through the installer's upgrade "
             "path (the development checkout's install.sh). The channel "
             "the installer remembered is kept; a channel never moves the "
             "copy backwards, so it stays on TAG until its channel passes "
             "it. Refused where no development checkout is found.")
    p_dep.add_argument("tag", nargs="?", metavar="TAG",
                       help="The release, vX.Y.Z (default: the newest)")
    p_dep.set_defaults(func=cmd_deploy)


def cmd_parser_check(args: argparse.Namespace) -> None:
    """`tjs parser-check <id>|all [--fixtures DIR]`: the conformance
    checks (taxjson.parsers.conformance) on a registered parser's
    fixture folders — the development checkout's tests/fixtures/<id>
    and every folder there whose fixture.toml names the parser, or the
    folder given (a shipped parser on a folder of your own). Each
    finding an act-on line with its detail under it; a folder that claims
    no kinds (fixture.toml `kinds`, `cash_kinds`) an `Info:` line; a kind
    the parser declares (`builds`) that none of its checkout folders claims
    a finding of the parser's. Exit 1 on a finding, 2 when the parser or a
    folder is not found."""
    from taxjson.lib.brokerages.detect import detect
    from taxjson.lib.brokerages.generic import unverified_mapping
    from taxjson.lib.out import act, show
    from taxjson.parsers import registry
    from taxjson.parsers.conformance import (check, declared_findings, fixture_dirs,
                                             unclaimed_note)
    # The shipped crypto fixtures' goldens were made in this zone (the
    # suite sets it too); a fixture of your own: set TAXJSON_LOCAL_TZ.
    os.environ.setdefault("TAXJSON_LOCAL_TZ", "America/Toronto")
    # The detection check judges the parser, as the suite does: the box's
    # own layouts route no fixture unless TAXJSON_LAYOUTS names them.
    os.environ.setdefault("TAXJSON_LAYOUTS", "")
    if args.parser == "all":
        specs = list(registry.SPECS)
        if args.fixtures:
            _die_input("--fixtures names one parser's folder; name the parser.")
    else:
        try:
            specs = [registry.spec(args.parser)]
        except ValueError:
            _die_input(f"unknown parser {args.parser!r} (known: "
                       f"{', '.join(s.id for s in registry.SPECS)}).")
    if args.fixtures:
        runs = [(s, Path(args.fixtures)) for s in specs]
    else:
        fixtures = _dev_checkout(
            "tests/fixtures", "no development checkout with tests/fixtures/ (set "
            "TAXJSON_DEV_DIR to it) — give the fixture folder with --fixtures DIR."
        ) / "tests" / "fixtures"
        runs = [(s, d) for s in specs for d in fixture_dirs(fixtures, s.id)]
    missing = [f"{s.id}: {d}" for s, d in runs if not (d / "sample.csv").is_file()]
    if missing:
        _die_input(f"no fixture folder (sample.csv beside expected.json): "
                   f"{'; '.join(missing)}.")
    bad, lines, phased = 0, [], set()
    classes: Dict[str, type] = {}
    for s, folder in runs:
        try:
            cls = classes[s.id] = registry.parser_class(s)
        except ValueError as e:
            lines.append(str(e))            # names the parser itself
            bad += 1
            continue
        # The account phase is the class's: checked with its first folder.
        found, rows, skipped = check(cls, folder, update_golden=args.update_golden,
                                     hint="--update-golden", route=detect, parser_id=s.id,
                                     account_phase=s.id not in phased)
        phased.add(s.id)
        if found:
            bad += 1
            lines.append(f"{s.id}: {len(found)} finding(s) on {folder}")
            for f in found:
                head, _, detail = f.partition("\n")
                lines.append(act(head))
                lines += [f"  {d}" for d in detail.split("\n") if d]
        else:
            lines.append(f"{s.id}: ok — {rows} row(s), {skipped} counted as "
                         f"skipped ({folder})")
        note = unclaimed_note(folder)
        if note:
            lines.append(f"Info: {s.id}: {note}")
        # A layout never run on a real export (`[broker] verified = false`,
        # TJ-2573): its folder's pass proves no real file.
        lay = unverified_mapping(folder / "sample.csv")
        if lay:
            lines.append(f"Info: {s.id}: {folder} is read with the {lay}, "
                         f"unverified against a real export: a pass checks the "
                         f"layout against its own made-up sample, not a real file")
    # Each kind a parser declares has a row in one of its checkout folders
    # (a folder of one's own, --fixtures, need not hold them all).
    for s in ([] if args.fixtures else specs):
        if s.id not in classes:
            continue                        # the parser itself, named above
        found = declared_findings(classes[s.id], fixture_dirs(fixtures, s.id))
        if found:
            bad += 1
            lines.append(f"{s.id}: {len(found)} finding(s) on its fixture folders")
            for f in found:
                head, _, detail = f.partition("\n")
                lines.append(act(head))
                lines += [f"  {d}" for d in detail.split("\n") if d]
    show(lines, sys.stdout)
    if bad:
        sys.exit(1)


def _register_parser_check(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "parser-check",
        help="Run the conformance checks on a registered parser",
        description="The conformance checks every parser passes on its "
             "synthetic fixture folder (sample.csv beside expected.json): "
             "the schema, the golden (and expected_cash.json, the cash events "
             "the broker's parse builds), determinism, row accounting, the account "
             "phase (prepare_files, coverage_messages and reconcile_files take "
             "what the parse passes them), detection "
             "(tjs run routes the sample to the parser) and the countries "
             "(under each country's settings the schema and the accounting "
             "hold), and the kinds (each row and cash event of a kind, the "
             "row's one its parser declares; one of each kind the folder's "
             "fixture.toml claims: kinds = \"...\", cash_kinds = \"...\"; each "
             "declared kind claimed by a folder) (taxjson.parsers.conformance). "
             "The folders are the "
             "development checkout's tests/fixtures/<id> and every folder "
             "there whose fixture.toml names the parser (parser = \"<id>\"), "
             "or --fixtures DIR (a shipped "
             "parser on a folder of your own). A crypto fixture's UTC stamps are dated in "
             "TAXJSON_LOCAL_TZ, America/Toronto unless set (the shipped "
             "goldens' zone). Your own layouts are not read unless "
             "TAXJSON_LAYOUTS names them. Exit 1 on a finding, 2 when the parser or the "
             "folder is not found.")
    p.add_argument("parser", help="a parser id or spelling, or all")
    p.add_argument("--fixtures", metavar="DIR",
                   help="the fixture folder (default: the checkout's "
                        "tests/fixtures/<id>)")
    p.add_argument("--update-golden", action="store_true",
                   help="write expected.json from the parser's output and "
                        "expected_cash.json from the parse's cash events first "
                        "(a reviewed change)")
    p.set_defaults(func=cmd_parser_check)


def register(sub: argparse._SubParsersAction) -> None:
    """This group's commands, for the `tjs` parser."""
    _register_channels(sub)
    _register_promote(sub)
    _register_deploy(sub)
    _register_parser_check(sub)
