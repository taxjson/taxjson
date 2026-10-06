"""What the planning verdicts (wash radar, sell-check, buy-check, watch,
harvest) can and cannot see — one wording per country
(tax-logic CA-PLAN-04 / US-PLAN-04; the two never mix).

The superficial-loss rule (ITA s.54, s.251.1 affiliated persons) and the
wash-sale rule (IRC §1091, IRS Pub. 550) both reach purchases by people
outside the project — a spouse or common-law partner, or a corporation
you (or they) control. A project only holds its own accounts, so every
SAFE / CLEAR verdict is "safe as far as these accounts show" (audit
S054-22)."""

import re

from taxjson.lib.country import canonical_country, USA

_NOTE = {
    "canada": ("Scope: these verdicts cover this project's accounts "
               "only. A purchase by your spouse or common-law partner, "
               "or by a corporation you or they control (affiliated "
               "persons, ITA s.251.1), within 30 days before or after a "
               "loss sale also makes it superficial — those accounts "
               "are not in the project, so check them yourself."),
    "usa": ("Scope: these verdicts cover this project's accounts only. "
            "A purchase by your spouse or by a corporation you control "
            "within 30 days before or after a loss sale also makes it a "
            "wash sale (§1091; IRS Pub. 550) — those accounts are not "
            "in the project, so check them yourself."),
}


def scope_note(country: str) -> str:
    """The one-line scope disclosure for `country` (canada | usa)."""
    c = canonical_country(country)
    return _NOTE["usa" if c == USA else "canada"]


def scope_lines(country: str, width_=None, indent: str = "") -> list:
    """scope_note(country) as a report paragraph: wrapped at the house
    width (lib/out; unwrapped when captured), its `Scope:` lead kept on
    the first line. The JSON documents carry scope_note() itself."""
    from taxjson.lib import out
    return out.wrap(scope_note(country), width_, indent, indent)


# A warn-only flag the radar appends to an advisory ("... NOTE: a long
# call on these shares was bought ..."). The JSON documents keep the
# text as is; a person sees each one as its own `Info:` paragraph.
_NOTE_RE = re.compile(r"(?:^|\s)NOTE:\s+")


def advisory_parts(text: str, label: str = "note: "):
    """(body, [note, ...]): `text` with its appended `NOTE:` sentences
    split off (a body that is only a `TICKER:` lead keeps the first,
    after `label` — lib/out.label("note") for the width shown at)."""
    parts = _NOTE_RE.split(str(text or ""))
    body = parts[0].strip()
    notes = [p.strip() for p in parts[1:] if p.strip()]
    if notes and (not body or body.endswith(":")):
        body = f"{body} {label}{notes.pop(0)}".strip()
    return body, notes


def advisory_lines(text: str, width_=None, indent: str = "",
                   hang=None) -> list:
    """An advisory (or a line quoting one) as wrapped report lines: the
    body from `indent` (continuation lines at `hang`, default `indent`),
    then each appended note as an `Info:` paragraph at `indent` (`note:`
    when nothing wraps — lib/out.label)."""
    import sys
    from taxjson.lib import out
    hang = indent if hang is None else hang
    lbl = out.label("note", width_, stream=sys.stdout)
    body, notes = advisory_parts(text, lbl)
    lines = out.wrap(body, width_, indent, hang) if body else []
    for n in notes:
        lines += out.wrap(lbl + n, width_, indent, indent + "  ")
    return lines
