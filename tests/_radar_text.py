"""Read the wash radar's text report back into rows, for tests.

The report (taxjson-wash-radar / `taxjson wash-radar`, house style) is one
section per advisory category — a heading `<CATEGORY> — <title> (N)`, a
TICKER / TAXABLE / SHELTERED / CLEARS table, and each advisory printed
once (without its `<CATEGORY>:` lead) under the rows it applies to, its
appended flags as `note:` lines. `radar_rows(text)` gives each row back
as one line in the radar's row shape,

    TICKER | TAXABLE | SHELTERED | CLEARS | <CATEGORY>: <advisory>

with the notes re-joined as ` NOTE: ...`, which is the advisory the
--json document carries. Tests that need the structure itself should
read `--json`."""
import re

_HEADING = re.compile(r"^(\S.*) \((\d+)\)$")


def _flush(rows, group, cat, adv):
    text = " ".join(adv).strip()
    if text:
        text = f"{cat}: {text}" if cat and cat != "OTHER" else text
    for cells in group:
        rows.append(" | ".join(cells + [text]))


def radar_rows(text: str) -> list:
    """Every table row of the report as `T | TQ | SQ | CLEARS | ADV`."""
    rows: list = []
    cat = ""
    group: list = []
    adv: list = []
    for ln in text.splitlines():
        m = _HEADING.match(ln)
        if m or not ln.strip() or not ln.startswith(" "):
            _flush(rows, group, cat, adv)
            group, adv = [], []
            if m:
                cat = m.group(1).split(" — ", 1)[0].strip()
            continue
        body = ln.strip()
        if ln.startswith("    "):                    # advisory / note
            if body.startswith("note: "):
                adv.append("NOTE: " + body[len("note: "):])
            else:
                adv.append(body)
            continue
        if (body.startswith("TICKER") or set(body) <= {"-"}
                or body == "(none)"):
            continue
        if adv:                                     # a new row group
            _flush(rows, group, cat, adv)
            group, adv = [], []
        group.append(re.split(r"\s{2,}", body)[:4])
    _flush(rows, group, cat, adv)
    return rows


def radar_row(text: str, ticker: str):
    """The row of `ticker` (or None)."""
    return next((r for r in radar_rows(text)
                 if r.split(" | ", 1)[0] == ticker), None)
