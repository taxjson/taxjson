# taxjson deck

`taxjson-deck.pdf` — 12 slides (16:9): the problem (a slip is not a cost basis), what the tool does, rules with their sources, a worked superficial-loss example, verification, the commands, privacy, scope (Canada supported, US experimental), brokers, install, roadmap.

`taxjson-deck.html` is the source; the PDF is rendered from it with WeasyPrint 70.0. System fonts only (Liberation / DejaVu), so no font files ship with it.

```sh
scripts/build_deck.sh
```

It renders the commands slide from the help page's groups (`scripts/deck_commands.py --write`), installs WeasyPrint 70.0 from PyPI into a throwaway virtualenv, writes the PDF and records the HTML's sha256 in `taxjson-deck.pdf.sha256`. `tests/test_deck.py` fails when the HTML changed without a rebuild, when the commands slide differs from `taxjson help`, or when the test count on slide 6 is not the one docs/limits.md states ("How the numbers are checked"). Look at every page before committing the HTML, the PDF and the `.sha256` together. Slide 5 quotes `tjs wash-sales` on the demo project (`tjs init --demo`): re-run it there when the output changes.
