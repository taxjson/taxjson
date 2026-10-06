---
name: Bug report
about: Wrong tax math, a parser error or crash, or a regression
labels: bug
---

<!--
This repository is public. Never paste amounts, account or slip numbers,
names, a raw broker CSV, or anything copied from one. Reproduce the problem
on a made-up file instead (see "Made-up input" below).
-->

**What happened**

What you ran, what you expected, what you got.

**The command and its messages**

```
$ tjs run
(paste the console's Error: / Warning: lines; replace amounts and account
numbers with made-up ones)
```

**Versions**

- taxjson (`tjs --version`):
- Python (`python3 --version`):
- OS:
- Country (taxjson.toml `country`): canada / usa
- Broker (if a parser is involved):

**Checks**

The summary lines of `tjs checklist` (the `[!]` steps) and of `tjs sanity`,
with figures removed.

**Made-up input that reproduces it**

Start from the demo CSV for your broker in `examples/` (`*_demo.csv`) and
edit its rows to the same shape as the rows that fail: the same columns,
actions and wording pattern, with made-up values, symbols and ids. Run
taxjson on it, confirm it fails the same way, and attach that file.
`tjs redact` output is a fallback only, after you have read all of it.
