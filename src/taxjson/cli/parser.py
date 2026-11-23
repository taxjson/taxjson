"""The argument parser and the grouped help page."""
import argparse
from typing import Any, Optional, Tuple

from taxjson.cli import (
    books, elect, estimate, explain, file, listings,
    maintainer, maps, migrate, plan, positions, setup,
    slips, summaries, technicals, tips, tools, totals, unclose,
)


class _CappedHelpFormatter(argparse.HelpFormatter):
    """Help wrapped at the house width (lib/out.width, docs/output-
    style.md): argparse wraps to the FULL terminal width, so on a wide
    monitor the option descriptions sprawl into hard-to-scan lines; the
    house width caps it at 160 columns on a terminal (120 piped) and
    still shrinks with a narrow terminal (TAXJSON_WIDTH=N sets it; 0
    keeps the piped house width)."""

    def __init__(self, prog, **kw):
        from taxjson.lib.out import WIDTH, width
        w = width()
        # A help page is always for a person: unwrapped (width 0) it
        # would be one line per option, so it keeps the house width.
        kw.setdefault("width", w if w > 0 else WIDTH)
        super().__init__(prog, **kw)


# The help page lists the subcommands by what they are for, not as one
# flat list of 55+ names (owner request). Every registered subcommand
# sits in exactly one group (tests/test_cli_polish.py); the README's
# command table uses the same groups in the same order.
_COMMAND_GROUPS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("Set up", ("checklist", "init", "years", "new-year", "align",
                "format", "format-map", "migrate", "fetch", "elect",
                "ticker-map", "update-tobase-map")),
    ("Build the books", ("run", "compare", "snapshot", "clean",
                         "crypto-sends", "find-missing-history",
                         "opening")),
    ("Summaries", ("amt", "div-estimate", "estimate", "fx-cash",
                   "instalments", "option-yield", "plot-tax", "stats",
                   "sum", "win-rates")),
    ("Positions", ("list", "shares", "technicals")),
    ("Row listings", ("cash", "dil", "divs", "events", "fees", "gains",
                      "leaps", "roc", "staking", "trades", "transfers")),
    ("Totals by type", ("ccd-sum", "dil-sum", "divs-sum", "fees-sum",
                        "leaps-sum", "losers", "roc-sum", "staking-sum", "trades-sum",
                        "winners")),
    ("Before you trade", ("wash-radar", "buy-check", "sell-check",
                          "rebuy", "harvest", "tips", "watch")),
    ("Before you file", ("form-export", "t1135",
                         "reconcile-slips", "slip-audit", "carryover",
                         "option-boundary",
                         "close-year", "unclose-year", "check-filed",
                         "handoff")),
    ("Explain and check", ("audit", "wash-sales", "tax-logic", "edge-cases",
                           "check-dates", "check-tt", "sanity", "journals",
                           "renames", "spinoffs", "splits")),
    ("Maintainer", ("channels", "deploy", "parser-check", "promote")),
    ("Tools", ("redact", "help")),
)
# The group of the maintainer's release commands: listed by `help --all`
# and on a development checkout only (they stay runnable everywhere).
_MAINTAINER_GROUP = "Maintainer"


def _dev_checkout_present() -> bool:
    """A development checkout taxjson is released from (lib/channels
    .dev_checkout with scripts/promote.sh): the help page lists the
    maintainer's commands there."""
    from taxjson.lib import channels as ch
    try:
        dev = ch.dev_checkout()
    except ch.ChannelsError:
        return False
    return dev is not None and (dev / "scripts" / "promote.sh").is_file()

_TOP_DESCRIPTION = (
    "taxjson — capital gains, income and the superficial-loss / "
    "wash-sale rules from your broker exports, computed on your "
    "machine. `tjs` is the same program under a shorter name. Run with "
    "no command for this page; `COMMAND -h` explains one command. New "
    "here? docs/getting-started.md walks a first project through, "
    "missing purchase history included "
    "(github.com/taxjson/taxjson/blob/main/docs/getting-started.md).")


class _GroupedHelpParser(argparse.ArgumentParser):
    """The top-level parser: its help page lists the subcommands under
    _COMMAND_GROUPS' headings (argparse can only print one flat
    COMMAND list). Usage, description, options and epilog are argparse's
    own. A subcommand missing from the groups is still listed, under
    "Other commands", never hidden.

    Inside a project whose country is known (`help_country`), a command
    the OTHER country owns (lib/country.COMMAND_COUNTRY) is left out and
    a closing line counts them; `help --all` (`show_all`) lists every
    command. Outside a project, or with --all, a one-country command is
    marked "(Canada)" / "(USA)". Running a hidden command still gets the
    dispatcher's refusal naming why. The Maintainer group (the release
    commands) is listed with --all or on a development checkout only."""

    help_country: Optional[str] = None
    show_all: bool = False

    def format_help(self) -> str:
        import copy
        from taxjson.lib.country import COMMAND_COUNTRY

        def display_name(c: str) -> str:
            return {"canada": "Canada", "usa": "USA"}.get(c, c)
        formatter = self._get_formatter()
        formatter.add_usage(self.usage, self._actions,
                            self._mutually_exclusive_groups)
        formatter.add_text(self.description)
        subs = next((a for a in self._actions
                     if isinstance(a, argparse._SubParsersAction)), None)
        by_name = {}
        hidden = 0
        for a in getattr(subs, "_choices_actions", []):
            owner = COMMAND_COUNTRY.get(a.dest)
            if owner and self.help_country and not self.show_all \
                    and owner != self.help_country:
                hidden += 1
                continue
            if owner and (self.show_all or not self.help_country):
                a = copy.copy(a)
                a.help = f"({display_name(owner)}) {a.help or ''}"
            by_name[a.dest] = a
        placed = set()
        sections = []
        maintainer = self.show_all or _dev_checkout_present()
        for title, names in _COMMAND_GROUPS:
            acts = [by_name[n] for n in names if n in by_name]
            placed.update(n for n in names if n in by_name)
            if title == _MAINTAINER_GROUP and not maintainer:
                continue
            sections.append((title, acts))
        rest = [a for n, a in by_name.items() if n not in placed]
        if rest:
            sections.append(("Other commands", rest))
        for title, acts in sections:
            if not acts:
                continue
            formatter.start_section(title)
            formatter.add_arguments(acts)
            formatter.end_section()
        for group in self._action_groups:
            acts = [a for a in group._group_actions if a is not subs]
            if not acts:
                continue
            formatter.start_section(group.title)
            formatter.add_text(group.description)
            formatter.add_arguments(acts)
            formatter.end_section()
        formatter.add_text(self.epilog)
        if hidden:
            formatter.add_text(
                f"{hidden} command{'s' if hidden != 1 else ''} hidden for "
                f"{display_name(self.help_country)} — `{self.prog} help "
                f"--all` lists every command.")
        return formatter.format_help()


# The groups, in help-page order: an unknown command's error lists the
# names in this order.
_GROUPS = (setup, maps, migrate, elect, books, summaries, estimate,
           positions, technicals, listings, totals, tips, plan, file, unclose, slips, explain,
           maintainer, tools)


def _build_parser(prog: str = "tjs"
                  ) -> Tuple[argparse.ArgumentParser, Any]:
    """The `taxjson` / `tjs` argument parser and its subparsers action.
    `prog` is the name the program was invoked as (usage lines and the
    help page show it)."""
    p = _GroupedHelpParser(
        prog=prog, description=_TOP_DESCRIPTION,
        formatter_class=_CappedHelpFormatter)
    p.add_argument("-C", "--dir", default=".", help="Project root (default: cwd)")
    try:
        from importlib.metadata import version as _pkg_version
        _ver = _pkg_version("taxjson")
    except Exception:
        _ver = "unknown"
    p.add_argument("--version", action="version",
                   version=f"taxjson {_ver}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    for group in _GROUPS:
        group.register(sub)

    p_help = sub.add_parser(
        "help", help="Show this page, or one command's help",
        description="Show the top-level help page, or the help for one "
                    "COMMAND; --all also lists the other country's "
                    "commands, which a project's help page leaves "
                    "out.")
    p_help.add_argument("topic", nargs="?", help="Subcommand to explain")
    p_help.add_argument("--all", action="store_true",
                        help="List every command, also the ones the "
                             "other country owns (hidden inside a project)")

    def _help(a: argparse.Namespace) -> None:
        if a.all:
            p.show_all = True
        if a.topic and a.topic in sub.choices:
            sub.choices[a.topic].print_help()
        elif a.topic:
            from taxjson.lib.out import error
            error(f"no such command {a.topic!r}", prog="tjs help")
            p.print_help()
            raise SystemExit(2)
        else:
            p.print_help()
    p_help.set_defaults(func=_help)

    # Synopsis in every subcommand's own -h: argparse prints only a
    # parser's `description` there, and add_parser does NOT inherit it
    # from the `help` one-liner shown in the top-level listing — so
    # `tjs run -h` listed options with no statement of what the
    # command does. Reuse the curated help text as the description.
    # (_choices_actions is argparse's stable-in-practice registry of
    # the per-command help strings; there is no public accessor.)
    for _act in getattr(sub, "_choices_actions", []):
        _sp = sub.choices.get(_act.dest)
        if _sp is None:
            continue
        if _sp.description is None and _act.help:
            # argparse %-expands `help=` but NOT `description=`, so a
            # curated "50%%" leaked as a literal double percent.
            _sp.description = _act.help.replace("%%", "%")
        # Same width cap as the top-level parser — add_parser doesn't
        # inherit formatter_class, and 40 call-site edits would drift.
        _sp.formatter_class = _CappedHelpFormatter
        if _act.dest in _DETAILS_CMDS:
            # Essentials first (docs/output-style.md): the default view
            # is the essentials; --details prints what it leaves out.
            _sp.add_argument("--details", action="store_true",
                             help="Also print the explanations, caveats "
                                  "and notes the default view leaves out")
    return p, sub


# The commands whose default view is the essentials and whose --details
# prints the rest (docs/output-style.md, Essentials first).
_DETAILS_CMDS = frozenset({
    "run", "checklist", "sum", "estimate", "amt", "instalments",
    "carryover", "t1135", "fx-cash", "form-export", "reconcile-slips",
    "slip-audit", "wash-sales", "wash-radar", "sell-check", "buy-check",
    "rebuy", "div-estimate", "harvest", "tips", "sanity", "audit", "list",
    "check-dates", "compare", "clean",
    "edge-cases", "find-missing-history", "ticker-map",
    "update-tobase-map", "years", "close-year", "unclose-year", "check-filed", "handoff",
    "redact", "migrate", "new-year", "init", "elect", "journals",
    "renames", "opening", "transfers", "format", "crypto-sends", "stats",
    "fees-sum", "option-boundary", "spinoffs", "splits", "staking", "staking-sum",
    "technicals"})
