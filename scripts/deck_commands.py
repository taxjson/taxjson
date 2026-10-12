#!/usr/bin/env python3
"""The deck's commands slide (docs/deck/taxjson-deck.html, slide 7),
rendered from the help page's groups (`_COMMAND_GROUPS` in
src/taxjson/bin/taxjson_run.py), the Maintainer group left out (the
help page shows it only on a development checkout).

    scripts/deck_commands.py            print the slide's command cards
    scripts/deck_commands.py --write    replace them in the deck HTML
    scripts/deck_commands.py --check    exit 1 when the deck differs

The cards sit between `<!-- commands:begin -->` and
`<!-- commands:end -->`. Standard library only: the groups are read
with `ast`, the package is never imported. scripts/build_deck.sh runs
`--write` before it renders the PDF; tests/test_deck.py runs `--check`.
"""
import ast
import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "src" / "taxjson" / "bin" / "taxjson_run.py"
DECK = ROOT / "docs" / "deck" / "taxjson-deck.html"
BEGIN = "<!-- commands:begin -->"
END = "<!-- commands:end -->"
# Cards per row on the slide.
ROWS = (5, 5)


def _literal(name: str):
    tree = ast.parse(RUN.read_text(encoding="utf-8"))
    for node in tree.body:
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target,
                                                          ast.Name):
            target = node.target.id
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
        if target == name:
            return ast.literal_eval(node.value)
    raise SystemExit(f"{RUN}: no {name} assignment")


def groups():
    """The help page's (title, commands) groups shown to a user."""
    hidden = _literal("_MAINTAINER_GROUP")
    return [(t, c) for t, c in _literal("_COMMAND_GROUPS") if t != hidden]


def render() -> str:
    gs = groups()
    if sum(ROWS) != len(gs):
        raise SystemExit(f"{len(gs)} groups do not fill the slide's rows "
                         f"{ROWS}: change ROWS")
    out, i = [], 0
    for n in ROWS:
        out.append('  <div class="row cmds">')
        for title, cmds in gs[i:i + n]:
            names = " ".join(f"<code>{html.escape(c)}</code>" for c in cmds)
            out.append(f'    <div class="card"><h3>{html.escape(title)}'
                       f'</h3><p>{names}</p></div>')
        out.append("  </div>")
        i += n
    return "\n".join(out)


def splice(text: str, block: str) -> str:
    a, b = text.index(BEGIN) + len(BEGIN), text.index(END)
    return text[:a] + "\n" + block + "\n  " + text[b:]


def main(argv) -> int:
    block = render()
    if not argv:
        print(block)
        return 0
    text = DECK.read_text(encoding="utf-8")
    new = splice(text, block)
    if argv == ["--check"]:
        if new != text:
            print(f"{DECK.relative_to(ROOT)}: the commands slide differs "
                  f"from the help page's groups; run "
                  f"scripts/deck_commands.py --write", file=sys.stderr)
            return 1
        return 0
    if argv == ["--write"]:
        if new != text:
            DECK.write_text(new, encoding="utf-8")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
