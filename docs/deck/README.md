# taxjson deck

`taxjson-deck.pdf` — 12 slides (16:9): the problem (a slip is not a cost basis), what the tool does, rules with their sources, a worked superficial-loss example, verification, the commands, privacy, scope (Canada supported, US experimental), brokers, install, roadmap.

`taxjson-deck.html` is the source; the PDF is rendered from it with WeasyPrint. System fonts only (Liberation / DejaVu), so no font files ship with it.

```sh
python3 -m venv /tmp/wp && /tmp/wp/bin/pip install weasyprint
/tmp/wp/bin/weasyprint docs/deck/taxjson-deck.html docs/deck/taxjson-deck.pdf
```

Edit the HTML, re-render, look at every page, commit both files. Numbers on the slides (test count, audit rounds) come from README's Verification section — keep them in step.
