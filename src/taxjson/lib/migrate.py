"""`tjs migrate`: fold an old project's per-purpose files into
ticker.map and taxjson.toml.

The old files and where their contents go:

    yf_ticker.map                    -> ticker.map  QUOTE lines
    crypto_ticker.map                -> ticker.map  CRYPTO lines
    ticker_extraction_overrides.txt  -> ticker.map  EXTRACT lines
    t1135.map                        -> ticker.map  T1135 lines
    amt_carryover.txt                -> taxjson.toml [estimate] amt_carryover
    claimed_losses.txt               -> taxjson.toml [carryover] claimed
    capital_gains_dividends.map      -> taxjson.toml [[capital_gains_dividends]]
    distributions.map                -> taxjson.toml [[distributions]]

Each old file is read with its old reader's own rules (what it meant
to the old version is what the new entries mean). The converted lines
are APPENDED to ticker.map and the tables to taxjson.toml — the user's
content and comments are never rewritten (a key that belongs in an
existing [estimate] / [carryover] section is inserted right under that
section's header line). Each old file is then renamed to
`<name>.migrated` (never deleted).

tv_exchange.map (the removed TradingView export's exchange prefixes) is
not converted: nothing reads it any more, so migrate only renames it to
tv_exchange.map.migrated and says it is no longer used. It does not stop
other commands either (RETIRED_MAP_FILES) — no rule can be lost. Nothing is written when any file
cannot be converted, or when the target already holds a conflicting
entry; the result is re-read and compared with what was intended
before anything is replaced.

While any old file (other than a retired one) is present every other
`taxjson` command stops with a message naming it
(lib/migrate.legacy_message) — no silent fallback.

An account's elections file, inputs/<account>/manifest.json before
v3.0.0, is renamed elections.json in every open year's inputs folder
(plan_elections_migration); a filed year's frozen copy keeps its name.
"""
from __future__ import annotations

import copy
import datetime as _dt
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from taxjson.project.toml_io import parse_toml, read_toml_text
from taxjson.lib.ticker_map import (LEGACY_MAP_FILES, RETIRED_MAP_FILES,
                                    extract_symbol)

# Every old file, in the order migrate handles (and names) them.
LEGACY_DATA_FILES = {
    "amt_carryover.txt": "[estimate] amt_carryover",
    "claimed_losses.txt": "[carryover] claimed",
    "capital_gains_dividends.map": "[[capital_gains_dividends]]",
    "distributions.map": "[[distributions]]",
}
# The old missing-history file (and its older name): converted into the
# accounts' .tt OPENING cost=unknown lines by `tjs migrate`
# (lib/history_migration.plan_missing_history_migration), not by plan().
MISSING_HISTORY_FILES = {
    "missing_history.json": "OPENING <date> <SYMBOL> <qty> cost=unknown "
                            "lines in inputs/<account>/missing_history.tt",
    "phantoms.json": "OPENING <date> <SYMBOL> <qty> cost=unknown lines in "
                     "inputs/<account>/missing_history.tt",
}
# Files whose contents still matter: commands stop while one is here.
_GUARDED_FILES: Tuple[str, ...] = tuple(LEGACY_MAP_FILES) + tuple(
    LEGACY_DATA_FILES) + tuple(MISSING_HISTORY_FILES)
# Every file migrate handles: the guarded ones plus the retired ones
# (renamed only).
LEGACY_FILES: Tuple[str, ...] = _GUARDED_FILES + tuple(RETIRED_MAP_FILES)


class MigrateError(ValueError):
    """An old file migrate cannot convert, or a target that already
    holds a conflicting entry. Nothing was written."""


def legacy_files(root, retired: bool = False) -> List[str]:
    """The old per-purpose files present in the project root (a name
    that exists in any form — a dangling link or a directory too) whose
    contents now live elsewhere — the ones every command stops for.
    With retired=True also the files of removed features
    (RETIRED_MAP_FILES: tv_exchange.map), which migrate renames."""
    r = Path(root)
    names = LEGACY_FILES if retired else _GUARDED_FILES
    return [n for n in names if os.path.lexists(r / n)]


def retired_files(root) -> List[str]:
    """The removed features' old files present in the project root."""
    r = Path(root)
    return [n for n in RETIRED_MAP_FILES if os.path.lexists(r / n)]


def retired_file_note(names: List[str]) -> str:
    """The once-per-run NOTE for a leftover retired file."""
    what = ", ".join(f"{n} ({RETIRED_MAP_FILES[n]})" for n in names)
    return (f"{what} is no longer used and is ignored — delete it, or "
            f"run `tjs migrate` to rename it to *.migrated.")


def legacy_message(names: List[str]) -> str:
    """The one message every command stops with while old files sit in
    the project."""
    mh = [n for n in names if n in MISSING_HISTORY_FILES]
    rest = [n for n in names if n not in MISSING_HISTORY_FILES]
    if mh and not rest:
        return (f"{' and '.join(mh)} "
                f"{'is' if len(mh) == 1 else 'are'} no longer read — "
                f"missing history is dated "
                f".tt lines (OPENING <date> <SYMBOL> <qty> cost=unknown in "
                f"inputs/<account>/missing_history.tt): run `tjs "
                f"migrate` to convert it (preview with `tjs migrate "
                f"--dry-run`; in a year folder it merges every year's "
                f"file); nothing was run.")
    where = []
    for n in names:
        target = (f"ticker.map {LEGACY_MAP_FILES[n]} lines"
                  if n in LEGACY_MAP_FILES
                  else MISSING_HISTORY_FILES[n] if n in MISSING_HISTORY_FILES
                  else f"taxjson.toml {LEGACY_DATA_FILES[n]}")
        where.append(f"{n} (now {target})")
    return ("this project still has " + ", ".join(where)
            + " — these files are no longer read, so their contents "
              "would be silently ignored. Run `tjs migrate` to move "
              "them (preview with `tjs migrate --dry-run`); nothing "
              "was run.")


# ------------------------------------------------------------------ helpers

def _read(root: Path, name: str) -> str:
    p = root / name
    if p.is_symlink() and not p.exists():
        raise MigrateError(f"{name} is a symlink to a file that does not "
                           f"exist — fix or remove it first")
    if p.is_dir():
        raise MigrateError(f"{name} is a directory, not a file — remove "
                           f"it first")
    try:
        return p.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise MigrateError(f"{name} is not UTF-8 text ({e}) — save it as "
                           f"UTF-8 first") from None
    except OSError as e:
        raise MigrateError(f"cannot read {name}: {e.strerror or e}") \
            from None


def _split_comment(raw: str) -> Tuple[str, str]:
    """(content, comment text) of a `#`-commented line."""
    body, _hash, note = raw.partition("#")
    return body.strip(), note.strip()


def _note(note: str) -> str:
    return f"  # {note}" if note else ""


def _toml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def _toml_num(v: float) -> str:
    f = float(v)
    if f == int(f) and abs(f) < 1e15:
        return f"{f:.1f}" if f != 0 else "0.0"
    return repr(f)


# ------------------------------------------------- ticker.map conversions

class _MapOut:
    """Converted ticker.map lines of one old file plus what the summary
    says about them."""

    def __init__(self, name: str):
        self.name = name
        self.lines: List[str] = []          # text to append (no header)
        self.rules: List[Tuple[str, Any, Any]] = []   # (kind, key, value)
        self.notes: List[str] = []


def _conv_quote(text: str, out: _MapOut) -> None:
    """yf_ticker.map: `SYMBOL YF_SYMBOL [QTY_RATIO]`, `#` comments; a
    short line was ignored, a bad ratio read as 1, later lines won."""
    eff: Dict[str, Tuple[int, str, float, str]] = {}
    order: List[str] = []
    for n, raw in enumerate(text.splitlines(), 1):
        body, note = _split_comment(raw)
        if not body:
            if note:
                out.lines.append(f"# {note}")
            continue
        parts = body.split()
        if len(parts) < 2:
            out.notes.append(f"{out.name}:{n}: {body!r} has no Yahoo "
                             f"symbol — it was ignored; kept as a comment")
            out.lines.append(f"# (ignored by the old loader) {body}")
            continue
        ratio = 1.0
        if len(parts) >= 3:
            try:
                ratio = float(parts[2])
            except ValueError:
                ratio = 1.0
                out.notes.append(f"{out.name}:{n}: ratio {parts[2]!r} was "
                                 f"not a number — the old loader used 1")
            if not (ratio > 0 and ratio != float("inf")):
                raise MigrateError(
                    f"{out.name}:{n}: ratio {parts[2]!r} is not a positive "
                    f"number — fix the line first")
        if len(parts) > 3:
            out.notes.append(f"{out.name}:{n}: extra words {parts[3:]} "
                             f"were ignored; kept as a comment")
            note = " ".join(parts[3:] + ([note] if note else []))
        sym = parts[0].upper()
        if sym in eff:
            out.notes.append(f"{out.name}:{n}: {sym} repeats line "
                             f"{eff[sym][0]} — the later line won; only it "
                             f"is kept")
        else:
            order.append(sym)
        eff[sym] = (n, parts[1], ratio, note)
    for sym in order:
        _n, yf, ratio, note = eff[sym]
        r = "" if ratio == 1.0 else f" {ratio!r}"
        out.lines.append(f"QUOTE {sym} {yf}{r}{_note(note)}")
        out.rules.append(("quote", sym, (yf, ratio)))


def _conv_crypto(text: str, out: _MapOut) -> None:
    """crypto_ticker.map: `SYMBOL YF_ID`, `#` comments; any other shape
    was skipped; later lines won."""
    eff: Dict[str, Tuple[int, str, str]] = {}
    order: List[str] = []
    for n, raw in enumerate(text.splitlines(), 1):
        body, note = _split_comment(raw)
        if not body:
            if note:
                out.lines.append(f"# {note}")
            continue
        parts = body.split()
        if len(parts) != 2:
            out.notes.append(f"{out.name}:{n}: {body!r} is not `SYMBOL "
                             f"YF_ID` — it was skipped; kept as a comment")
            out.lines.append(f"# (skipped by the old loader) {body}")
            continue
        sym = parts[0].upper()
        if sym in eff:
            out.notes.append(f"{out.name}:{n}: {sym} repeats line "
                             f"{eff[sym][0]} — the later line won; only it "
                             f"is kept")
        else:
            order.append(sym)
        eff[sym] = (n, parts[1], note)
    for sym in order:
        _n, yid, note = eff[sym]
        out.lines.append(f"CRYPTO {sym} {yid}{_note(note)}")
        out.rules.append(("crypto", sym, yid))


_CUR_RE = re.compile(r"^(?:[A-Z]{3}|\*)$")


def _conv_extract(text: str, out: _MapOut) -> None:
    """ticker_extraction_overrides.txt: `description | currency |
    symbol`, a line STARTING with `#` is a comment; a malformed line
    stopped the old run, so it stops the migration too. The first
    matching line won: a later line for the same description and
    currency never applied and is kept as a comment."""
    seen: Dict[Tuple[str, str], Tuple[int, str]] = {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip().lstrip("﻿")
        if not line:
            continue
        if line.startswith("#"):
            out.lines.append("# " + line.lstrip("#").strip())
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 3 or not parts[0] or not parts[2]:
            raise MigrateError(
                f"{out.name}:{n}: {line!r} is not `description | currency "
                f"| symbol` (the old run refused it too) — fix the line "
                f"first")
        desc, cur, sym = parts
        cur = cur.upper()
        if not _CUR_RE.match(cur):
            raise MigrateError(
                f"{out.name}:{n}: currency {parts[1]!r} is not a 3-letter "
                f"code or '*' (the old run refused it too) — fix the line "
                f"first")
        if "#" in line:
            raise MigrateError(
                f"{out.name}:{n}: {line!r} contains `#`, which ticker.map "
                f"reads as the start of a comment — shorten the "
                f"description so it has no `#`, then migrate")
        if len(sym.split()) != 1:
            raise MigrateError(
                f"{out.name}:{n}: symbol {sym!r} has a space — ticker.map "
                f"symbols are one word; fix the line first")
        # The symbol as ticker.map reads it (upper case, a Canadian
        # venue as ROOT.TO: lib/ticker_map.extract_symbol), so a line
        # already in the map in another spelling is the same rule, not
        # a contradiction.
        try:
            book_sym = extract_symbol(desc, cur, sym)
        except ValueError as e:
            raise MigrateError(f"{out.name}:{n}: {e}; fix the line "
                               f"first") from None
        key = (desc.lower(), cur)
        if key in seen:
            out.notes.append(
                f"{out.name}:{n}: the same description and currency as "
                f"line {seen[key][0]} (the first line won) — kept as a "
                f"comment")
            out.lines.append(f"# (never applied: line {seen[key][0]} "
                             f"matched first) EXTRACT {desc} | {cur} | {sym}")
            continue
        seen[key] = (n, sym)
        out.lines.append(f"EXTRACT {desc} | {cur} | {sym}")
        out.rules.append(("extract", key, book_sym))


def _conv_t1135(text: str, out: _MapOut) -> None:
    """t1135.map: `SYMBOL COUNTRY`, `#` comments, symbols upper-cased;
    a line of another shape, or whose COUNTRY is neither an ISO 3166
    alpha-3 code nor CA/CAN/CANADA/EXCLUDE, was ignored with a warning
    (the symbol kept its listing-suffix country) — kept as a comment
    here; later lines won."""
    from taxjson.lib.t1135_country import parse_country
    eff: Dict[str, Tuple[int, str, str, str]] = {}
    order: List[str] = []
    for n, raw in enumerate(text.splitlines(), 1):
        body, note = _split_comment(raw)
        if not body:
            if note:
                out.lines.append(f"# {note}")
            continue
        parts = body.split()
        if len(parts) != 2:
            out.notes.append(f"{out.name}:{n}: {body!r} is not `SYMBOL "
                             f"COUNTRY` — the old loader ignored it; kept "
                             f"as a comment")
            out.lines.append(f"# (ignored by the old loader) {body}")
            continue
        try:
            code = parse_country(parts[1])
        except ValueError as e:
            out.notes.append(f"{out.name}:{n}: {e} — the old loader "
                             f"ignored the line ({parts[0].upper()} kept "
                             f"its listing-suffix country); kept as a "
                             f"comment")
            out.lines.append(f"# (ignored by the old loader) {body}")
            continue
        sym = parts[0].upper()
        if sym in eff:
            out.notes.append(f"{out.name}:{n}: {sym} repeats line "
                             f"{eff[sym][0]} — the later line won; only it "
                             f"is kept")
        else:
            order.append(sym)
        eff[sym] = (n, parts[1].upper(), code, note)
    for sym in order:
        _n, word, code, note = eff[sym]
        out.lines.append(f"T1135 {sym} {word}{_note(note)}")
        out.rules.append(("t1135", sym, code))


_MAP_CONVERTERS = {
    "yf_ticker.map": _conv_quote,
    "crypto_ticker.map": _conv_crypto,
    "ticker_extraction_overrides.txt": _conv_extract,
    "t1135.map": _conv_t1135,
}


# ----------------------------------------------- taxjson.toml conversions

def _legacy_distributions(text: str, name: str
                          ) -> List[Tuple[str, str, float, str]]:
    """The old distributions.map reader's rules, raising instead of
    exiting: `SYMBOL YYYY-MM-DD PER_SHARE`, `#` comments, a plain
    decimal amount, a real date; repeated SYMBOL+DATE lines both
    applied (they add)."""
    amount_re = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)")
    rows = []
    for n, raw in enumerate(text.splitlines(), 1):
        body, note = _split_comment(raw)
        if not body:
            continue
        parts = body.split()
        if len(parts) != 3:
            raise MigrateError(f"{name}:{n}: expected 'SYMBOL DATE "
                               f"PER_SHARE', got {raw.strip()!r} (the old "
                               f"run refused it too) — fix the line first")
        sym, date, amt = parts
        if not amount_re.fullmatch(amt):
            raise MigrateError(f"{name}:{n}: bad per-share amount {amt!r} "
                               f"— fix the line first")
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                raise ValueError
            _dt.date.fromisoformat(date)
        except ValueError:
            raise MigrateError(f"{name}:{n}: bad date {date!r} (want "
                               f"YYYY-MM-DD) — fix the line first") from None
        rows.append((sym.upper(), date, float(amt), note))
    return rows


def _toml_tables(root: Path, cfg: Dict[str, Any], text: str,
                 names: List[str], array_cb
                 ) -> Tuple[List[Tuple[str, str, str]], Dict[str, Any],
                            List[str], List[str]]:
    """(edits, expected config, done lines, notes) for the keyed tables;
    each edit is (kind, section, text) — kind "insert" (a key under an
    existing [section] header) or "append" (text at the end of the
    file). The array tables go to array_cb(table, entries, source)."""
    edits: List[Tuple[str, str, str]] = []
    expect = copy.deepcopy(cfg)
    done: List[str] = []
    notes: List[str] = []
    stamp = _dt.date.today().isoformat()

    def _keyed(section: str, key: str, by_year: Dict[int, float],
               src: str) -> None:
        tbl = cfg.get(section)
        if tbl is not None and not isinstance(tbl, dict):
            raise MigrateError(f"taxjson.toml: `{section}` is not a table — "
                               f"cannot add {key} to it")
        want = {str(y): float(a) for y, a in sorted(by_year.items())}
        have = (tbl or {}).get(key)
        if have is not None:
            same = isinstance(have, dict) and {
                str(k): float(v) for k, v in have.items()
                if isinstance(v, (int, float))} == want \
                and len(have) == len(want)
            if same:
                done.append(f"{src}: [{section}] {key} already holds the "
                            f"same amounts — nothing to add")
                return
            raise MigrateError(
                f"taxjson.toml already has [{section}] {key} with other "
                f"amounts than {src} — keep one: delete the key or the "
                f"file, then migrate again")
        if not want:
            done.append(f"{src}: no entries — nothing to add")
            return
        line = (f"{key} = {{ " + ", ".join(
            f"{y} = {_toml_num(a)}" for y, a in want.items())
            + f" }}  # from {src} (tjs migrate, {stamp})")
        expect.setdefault(section, {})[key] = want
        has_header = re.search(rf"(?m)^[ \t]*\[[ \t]*{section}[ \t]*\]"
                               rf"[ \t]*(#.*)?$", text) is not None
        edits.append(("insert" if has_header else "append", section,
                      line if has_header else f"\n[{section}]\n{line}\n"))
        done.append(f"{src} -> taxjson.toml [{section}] {key}: "
                    f"{len(want)} year(s)")

    for name in names:
        if name == "amt_carryover.txt":
            from taxjson.lib.carryforward import (CarryInputError,
                                                  load_amt_file)
            _read(root, name)
            try:
                by_year = load_amt_file(root / name)
            except CarryInputError as e:
                raise MigrateError(f"{e} — fix the line first") from None
            _keyed("estimate", "amt_carryover", by_year, name)
        elif name == "claimed_losses.txt":
            from taxjson.lib.carryover_inputs import load_claimed
            import contextlib
            import io
            _read(root, name)
            ignored: List[str] = []
            with contextlib.redirect_stderr(io.StringIO()):
                by_year = load_claimed(root / name, ignored=ignored)
            if ignored:
                raise MigrateError(
                    f"{ignored[0]} cannot be read (the old ledger skipped "
                    f"it with a warning) — fix or delete the line first")
            _keyed("carryover", "claimed", by_year, name)
        elif name == "capital_gains_dividends.map":
            from taxjson.lib.cg_dividends import (CgDividendMapError,
                                                  parse_map)
            text = _read(root, name)
            try:
                entries = parse_map(text)
            except CgDividendMapError as e:
                raise MigrateError(f"{e} — fix the line first") from None
            notes_by_line = {n: _split_comment(raw)[1]
                             for n, raw in enumerate(text.splitlines(), 1)}
            new = []
            for e in entries:
                t: Dict[str, Any] = {"symbol": e.symbol}
                if len(e.when) == 4:
                    t["year"] = int(e.when)
                else:
                    t["date"] = _dt.date.fromisoformat(e.when)
                t["amount"] = "all" if e.amount is None else e.amount
                if e.account:
                    t["account"] = e.account
                new.append((t, notes_by_line.get(e.line, "")))
            array_cb("capital_gains_dividends", new, name)
        elif name == "distributions.map":
            rows = _legacy_distributions(_read(root, name), name)
            new = [({"symbol": s, "record_date": _dt.date.fromisoformat(d),
                     "per_share": a}, note) for s, d, a, note in rows]
            array_cb("distributions", new, name)
    return edits, expect, done, notes


def _array_text(table: str, entries: List[Tuple[Dict[str, Any], str]],
                src: str) -> str:
    stamp = _dt.date.today().isoformat()
    out = [f"\n# --- from {src} (tjs migrate, {stamp}) ---"]
    for t, note in entries:
        if note:
            out.append(f"# {note}")
        out.append(f"[[{table}]]")
        for k, v in t.items():
            if isinstance(v, str):
                out.append(f"{k} = {_toml_str(v)}")
            elif isinstance(v, _dt.date):
                out.append(f"{k} = {v.isoformat()}")
            elif isinstance(v, bool):
                out.append(f"{k} = {'true' if v else 'false'}")
            elif isinstance(v, int):
                out.append(f"{k} = {v}")
            else:
                out.append(f"{k} = {_toml_num(v)}")
        out.append("")
    return "\n".join(out)


# ------------------------------------------------------------------ plan

class Plan:
    def __init__(self, root: Path):
        self.root = root
        self.names: List[str] = []
        self.map_append: str = ""
        self.map_new_file = False
        self.toml_text: Optional[str] = None
        self.toml_before: str = ""
        self.done: List[str] = []
        self.notes: List[str] = []
        self.moves: List[Tuple[str, str]] = []

    def summary(self, dry_run: bool) -> List[str]:
        out = list(self.done)
        out += [f"note: {n}" for n in self.notes]
        for a, b in self.moves:
            out.append(f"{'would move' if dry_run else 'moved'} {a} -> {b}")
        return out


def _migrated_name(root: Path, name: str) -> str:
    cand = f"{name}.migrated"
    n = 2
    while os.path.lexists(root / cand):
        cand = f"{name}.migrated.{n}"
        n += 1
    return cand


def _canon_cfg(v: Any) -> Any:
    """A parsed config with numbers as floats (an int and a float of the
    same value compare equal) — for the before/after comparison."""
    if isinstance(v, dict):
        return {str(k): _canon_cfg(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_canon_cfg(x) for x in v]
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, _dt.date) and not isinstance(v, _dt.datetime):
        return v.isoformat()
    return v


def plan(root) -> Plan:
    """What `tjs migrate` would do in `root`. Raises MigrateError
    when an old file cannot be converted or a target conflicts."""
    root = Path(root).resolve()
    pl = Plan(root)
    # (missing_history.json: its own converter, cmd_migrate)
    pl.names = [n for n in legacy_files(root, retired=True)
                if n not in MISSING_HISTORY_FILES]
    if not pl.names:
        return pl
    for n in pl.names:
        if n in RETIRED_MAP_FILES:
            pl.done.append(f"{n}: no longer used ({RETIRED_MAP_FILES[n]})"
                           f" — not converted, only renamed")
    # ---------------------------------------------------- ticker.map
    map_names = [n for n in pl.names if n in LEGACY_MAP_FILES]
    if map_names:
        from taxjson.lib.ticker_map import (SideRules, add_side_rule,
                                            read_side_rules)
        from taxjson.lib.map_file import _parse_map_file
        tm = root / "ticker.map"
        existing = SideRules()
        old_problems: List[str] = []
        if os.path.lexists(tm):
            if not tm.is_file():
                raise MigrateError("ticker.map exists but is not a "
                                   "readable file — fix it first")
            try:
                existing = read_side_rules(tm)
                old_problems = _parse_map_file(tm)[1]
            except (OSError, ValueError) as e:
                raise MigrateError(f"cannot read ticker.map: {e}") from None
        else:
            pl.map_new_file = True
        chunks: List[str] = []
        stamp = _dt.date.today().isoformat()
        for name in map_names:
            out = _MapOut(name)
            _MAP_CONVERTERS[name](_read(root, name), out)
            keep_lines: List[str] = []
            added = skipped = 0
            rule_iter = iter(out.rules)
            for line in out.lines:
                if line.startswith("#"):
                    keep_lines.append(line)
                    continue
                kind, key, value = next(rule_iter)
                table = (existing.extract if kind == "extract"
                         else getattr(existing, kind))
                if kind == "extract":
                    hit = next((s for d, c, s in table if (d, c) == key),
                               None)
                else:
                    hit = table.get(key)
                if hit is not None:
                    if hit == value:
                        skipped += 1
                        continue
                    shown = (f"{key[0]!r} | {key[1]}" if kind == "extract"
                             else key)
                    raise MigrateError(
                        f"ticker.map already has a {kind.upper()} line for "
                        f"{shown} that says {hit!r}, but {name} says "
                        f"{value!r} — keep one (edit ticker.map or the "
                        f"old file), then migrate again")
                add_side_rule(existing, kind, key, value, name, line)
                keep_lines.append(line)
                added += 1
            if keep_lines:
                chunks.append(f"\n# --- from {name} (tjs migrate, "
                              f"{stamp}) ---\n" + "\n".join(keep_lines)
                              + "\n")
            kw = LEGACY_MAP_FILES[name]
            pl.done.append(f"{name} -> ticker.map: {added} {kw} line(s)"
                           + (f" ({skipped} already there)" if skipped
                              else ""))
            pl.notes += out.notes
        pl.map_append = "".join(chunks)
        # The appended map must read exactly as intended: no problem the
        # map did not already have.
        if pl.map_append:
            import tempfile
            before = tm.read_text(encoding="utf-8-sig") \
                if tm.is_file() else ""
            with tempfile.TemporaryDirectory() as td:
                tp = Path(td) / "ticker.map"
                tp.write_text(_joined(before, pl.map_append),
                              encoding="utf-8")
                new_problems = [p for p in _parse_map_file(tp)[1]
                                if p not in old_problems]
            if new_problems:
                raise MigrateError("the converted lines would not read "
                                   "cleanly in ticker.map: "
                                   + new_problems[0])
    # ---------------------------------------------------- taxjson.toml
    data_names = [n for n in pl.names if n in LEGACY_DATA_FILES]
    if data_names:
        cfg_path = root / "taxjson.toml"
        if not cfg_path.is_file():
            raise MigrateError(f"no taxjson.toml in {root} — the contents "
                               f"of {', '.join(data_names)} go there")
        text = read_toml_text(cfg_path)
        try:
            cfg = parse_toml(text)
        except ValueError as e:
            raise MigrateError(f"taxjson.toml is not valid TOML ({e}) — "
                               f"fix it first") from None
        appends: List[str] = []

        def _arr(table, new, src):
            have = cfg.get(table)
            if have is not None and not isinstance(have, list):
                raise MigrateError(f"taxjson.toml: `{table}` is not an "
                                   f"array of tables — fix it first")
            have = have or []
            if not new:
                pl.done.append(f"{src}: no entries — nothing to add")
                return
            want = [t for t, _n in new]
            if have:
                if _canon_cfg(have) == _canon_cfg(want):
                    pl.done.append(f"{src}: taxjson.toml already holds the "
                                   f"same [[{table}]] entries — nothing "
                                   f"to add")
                    return
                raise MigrateError(
                    f"taxjson.toml already has [[{table}]] entries that "
                    f"differ from {src} — keep one (delete the entries or "
                    f"the file), then migrate again")
            appends.append(_array_text(table, new, src))
            expect_arr[table] = want
            pl.done.append(f"{src} -> taxjson.toml [[{table}]]: "
                           f"{len(want)} entr{'y' if len(want) == 1 else 'ies'}")

        expect_arr: Dict[str, Any] = {}
        edits, expect, done, notes = _toml_tables(root, cfg, text,
                                                  data_names, _arr)
        pl.done += done
        pl.notes += notes
        expect.update(expect_arr)
        new_text = text
        for kind, section, line in edits:
            if kind == "insert":
                new_text = re.sub(
                    rf"(?m)^([ \t]*\[[ \t]*{section}[ \t]*\][^\n]*\n?)",
                    lambda m: (m.group(1)
                               + ("" if m.group(1).endswith("\n") else "\n")
                               + line + "\n"),
                    new_text, count=1)
            else:
                appends.insert(0, line)
        for a in appends:
            new_text = _joined(new_text, a)
        try:
            got = parse_toml(new_text)
        except ValueError as e:
            raise MigrateError(
                f"taxjson.toml could not take the new entries safely ({e}) "
                f"— add them by hand (`tjs migrate --dry-run` shows "
                f"them)") from None
        if _canon_cfg(got) != _canon_cfg(expect):
            raise MigrateError(
                "taxjson.toml did not read back as intended after adding "
                "the entries (an unusual layout of [estimate] or "
                "[carryover]) — add them by hand (`tjs migrate "
                "--dry-run` shows them)")
        from taxjson.lib.config_check import settings_problems

        def _all_problems(c):
            # Types, dates, duplicates AND country ownership (a US
            # project refuses the Canada-only box 18 table).
            return settings_problems(copy.deepcopy(c))
        probs = [p for p in _all_problems(got)
                 if p not in _all_problems(cfg)]
        if probs:
            raise MigrateError("the converted entries would not pass the "
                               "config check: " + probs[0]
                               + " — fix the old file first")
        pl.toml_text = new_text
        pl.toml_before = text
    for name, written in (("ticker.map", bool(pl.map_append)),
                          ("taxjson.toml", pl.toml_text is not None)):
        _refuse_outside_link(root, name, written)
    for n in pl.names:
        pl.moves.append((n, _migrated_name(root, n)))
    return pl


def _refuse_outside_link(root: Path, name: str, written: bool) -> None:
    """A ticker.map / taxjson.toml that migrate would rewrite and that is
    a symlink leaving the project is refused before anything is written:
    migrate never writes through a link to a file outside the project
    (security review M1). A link inside the project is followed — its
    target, a file of this project, is the one replaced."""
    if not written:
        return
    from taxjson.lib.safe_write import link_outside
    target = link_outside(root / name, root)
    if target is not None:
        raise MigrateError(
            f"{name} is a symlink to {target}, outside the project — "
            f"migrate rewrites {name} and never writes through such a "
            f"link: run migrate in the folder that holds the real file, "
            f"or replace the link with a copy, then migrate again")


def _joined(before: str, add: str) -> str:
    if before and not before.endswith("\n"):
        before += "\n"
    return before + add


def apply(pl: Plan) -> None:
    """Write the plan: ticker.map and taxjson.toml replaced atomically
    (temp file + rename), then each old file renamed."""
    root = pl.root

    def _write(path: Path, text: str) -> None:
        # The .migrate.part is created fresh (never through a symlink
        # planted there) and keeps the file's permission bits; a link at
        # `path` (inside the project — plan() refused one leaving it) is
        # resolved so the link stays and its target is replaced.
        from taxjson.lib.safe_write import link_outside, write_atomic
        if path.is_symlink():
            if link_outside(path, root) is not None:
                raise MigrateError(f"{path.name} became a symlink leaving "
                                   f"the project — nothing more written")
            path = path.resolve()
        write_atomic(path, text, suffix=".migrate.part", keep_mode=True)

    # A file made read-only is refused before anything is written
    # (lib/safe_write.ReadOnlyFileError; backlog TJ-0107): ticker.map
    # rewritten and taxjson.toml refused would leave the two out of step.
    from taxjson.lib.safe_write import refuse_read_only
    for _p, _todo in ((root / "ticker.map", pl.map_append),
                      (root / "taxjson.toml", pl.toml_text is not None)):
        if _todo:
            refuse_read_only(_p.resolve() if _p.is_symlink() else _p)
    if pl.map_append:
        tm = root / "ticker.map"
        # A new map starts with the header `tjs format-map` writes.
        from taxjson.lib.ticker_map_format import header_text
        before = (tm.read_text(encoding="utf-8-sig") if tm.is_file()
                  else header_text())
        _write(tm, _joined(before, pl.map_append))
    if pl.toml_text is not None:
        _write(root / "taxjson.toml", pl.toml_text)
    for a, b in pl.moves:
        os.replace(root / a, root / b)


# ------------------------------------------------- the elections file
# An account's elections are inputs/<account>/elections.json since
# v3.0.0; manifest.json was the name before (lib/project_layout
# ELECTIONS, OLD_ELECTIONS). `tjs migrate` renames it in every open
# year's inputs folder (the shared one too); a filed year's frozen copy
# keeps the name it was frozen with.

class ElectionsMigration:
    """`tjs migrate`'s plan for the elections file: `renames` [(old,
    new)], `same` [(old, its .migrated name)] for a manifest.json beside
    an elections.json of the same bytes, and `legacy` [(work/<account>_
    manifest.json, where it goes)] — into the account's inputs folder, or
    beside itself as .migrated when the account already has an elections
    file there (the run read that one). `refused` [(the file, why)]: two
    names of other contents, one that cannot be read, a folder at the
    old name, an older work/ copy for a filed year with no copy of its
    own — any of them, and nothing is written. `frozen`: the filed years
    whose frozen copy keeps the old name."""

    def __init__(self) -> None:
        self.renames: List[Tuple[Path, Path]] = []
        self.same: List[Tuple[Path, Path]] = []
        self.legacy: List[Tuple[Path, Path]] = []
        self.refused: List[Tuple[Path, str]] = []
        self.frozen: List[int] = []

    @property
    def work(self) -> bool:
        return bool(self.renames or self.same or self.legacy
                    or self.refused)


def _elections_projects(root: Path) -> List[Path]:
    """The projects `tjs migrate` in `root` covers: `root`, and in a
    multi-year project every year folder and the shared inputs/ beside
    them (a folder, no project)."""
    from taxjson.lib import project_layout as PL
    projects = [root] if PL.has_config(root) else []
    top = PL.multi_root(root)
    if top is not None:
        projects += [d for _y, d in PL.year_dirs(top) if d.resolve() != root]
        if (top / PL.INPUTS).is_dir() and not (top / PL.INPUTS).is_symlink():
            projects.append(top / PL.INPUTS)
    return projects


def _elections_folders(projects: List[Path]
                       ) -> Tuple[List[Tuple[Path, Path]], List[int]]:
    """([(an open inputs folder the elections file is renamed in, the
    folder its links must stay inside)], the filed years with frozen
    inputs that hold one under the old name): each project's inputs
    folder (inputs_dir: the shared one) and the shared inputs/."""
    from taxjson.lib import project_layout as PL
    folders: List[Tuple[Path, Path]] = []
    frozen: List[int] = []
    for d in projects:
        if d.name == PL.INPUTS and not PL.has_config(d):
            folder, bound = d, d.parent
        elif PL.frozen_project(d):
            year = (PL.read_config_soft(d).get("settings") or {}).get("year")
            if isinstance(year, int) and any(
                    os.path.lexists(a / PL.OLD_ELECTIONS)
                    for a in _account_folders(d / PL.INPUTS, d)):
                frozen.append(year)
            continue
        else:
            folder, bound = PL.inputs_dir(d), PL.write_boundary(d)
        if folder.resolve() not in [f.resolve() for f, _b in folders]:
            folders.append((folder, bound))
    return folders, sorted(frozen)


def _account_folders(folder: Path, bound: Path) -> List[Path]:
    """The account folders of an inputs folder: a link to a folder
    inside `bound` too, as load_config allows (one leaving it is
    refused there), each real folder once."""
    from taxjson.lib.safe_write import link_outside
    out: List[Path] = []
    seen = set()
    try:
        cands = sorted(folder.iterdir())
    except OSError:
        return []
    for p in cands:
        try:
            if not p.is_dir() or (p.is_symlink()
                                  and link_outside(p, bound) is not None):
                continue
            real = p.resolve()
        except OSError:
            continue
        if real not in seen:
            seen.add(real)
            out.append(p)
    return out


def _kind(p: Path) -> str:
    """'file' (a file, or a link to one), else what it is instead: a
    link to nothing, a folder, or why it cannot be read."""
    if p.is_symlink() and not p.exists():
        return "a link to nothing"
    if p.is_dir():
        return "a folder"
    try:
        with open(p, "rb"):
            return "file"
    except OSError as e:
        return f"cannot be read ({e.strerror or e})"


def _plan_account(plan: ElectionsMigration, acct: Path) -> None:
    """The account folder `acct`'s part of the plan: its manifest.json
    renamed, or refused (a folder at the name; both names, one of them
    no readable file, or of other contents)."""
    from taxjson.lib import project_layout as PL
    old, new = acct / PL.OLD_ELECTIONS, acct / PL.ELECTIONS
    if not os.path.lexists(old):
        return
    k_old = _kind(old)
    if k_old == "a folder":
        plan.refused.append((old, "a folder, not an elections file: move "
                                  "what it holds out, then remove it"))
        return
    if not os.path.lexists(new):
        # A link is renamed as a link; one to nothing, or a file that
        # cannot be read, the next command names under its new name.
        plan.renames.append((old, new))
        return
    bad = [(p, k) for p, k in ((old, k_old), (new, _kind(new)))
           if k != "file"]
    for p, k in bad:
        plan.refused.append((p, f"{k}, with the other name beside it: "
                                f"keep the one that holds your "
                                f"elections, delete the other"))
    if bad:
        return
    if old.read_bytes() == new.read_bytes():
        plan.same.append((old, acct / _migrated_name(acct, PL.OLD_ELECTIONS)))
    else:
        plan.refused.append((old, f"{PL.ELECTIONS} of other contents "
                                  f"beside it: keep the one that holds "
                                  f"your elections, delete the other"))


def _plan_legacy(plan: ElectionsMigration, d: Path) -> None:
    """work/<account>_manifest.json of the project `d` (the elections of
    a layout older still, read by no command now): into the account's
    inputs folder, or kept beside itself as .migrated when the account
    has an elections file there already."""
    from taxjson.lib import project_layout as PL
    if not PL.has_config(d):
        return
    for acct in sorted(PL.read_config_soft(d).get("accounts") or {}):
        w = d / PL.WORK / f"{acct}{PL.LEGACY_WORK_ELECTIONS}"
        if not os.path.lexists(w):
            continue
        a_dir = PL.inputs_dir(d) / str(acct)
        have = [a_dir / n for n in (PL.ELECTIONS, PL.OLD_ELECTIONS)
                if os.path.lexists(a_dir / n)]
        if have:
            plan.legacy.append((w, w.with_name(_migrated_name(
                w.parent, w.name))))
        elif PL.frozen_project(d):
            plan.refused.append((w, "a filed year's elections, kept in "
                                    "work/ by a layout older still: copy "
                                    f"it to inputs/{acct}/{PL.OLD_ELECTIONS}"
                                    " (the name it was filed under)"))
        elif _kind(w) != "file":
            plan.refused.append((w, f"{_kind(w)}: restore it, or delete "
                                    f"it to start with no elections"))
        else:
            plan.legacy.append((w, a_dir / PL.ELECTIONS))


def plan_elections_migration(root) -> ElectionsMigration:
    """What `tjs migrate` does with the elections file in `root` (a
    project, or the folder holding the year folders)."""
    plan = ElectionsMigration()
    projects = _elections_projects(Path(root).resolve())
    folders, plan.frozen = _elections_folders(projects)
    for folder, bound in folders:
        for acct in _account_folders(folder, bound):
            _plan_account(plan, acct)
    for d in projects:
        _plan_legacy(plan, d)
    return plan


def apply_elections_migration(plan: ElectionsMigration) -> None:
    """Rename each old elections file (an identical one beside the new
    name to <name>.migrated) and drop its lock file (.manifest.json.lock:
    lib/corp_actions.manifest_lock_path, taxjson's own, empty); move each
    work/ copy into place. Refused (MigrateError, nothing written) while
    plan.refused names a file. OSError from the rename."""
    if plan.refused:
        raise MigrateError("an elections file cannot be renamed: "
                           + "; ".join(f"{p} ({why})"
                                       for p, why in plan.refused))
    for old, new in plan.renames + plan.same + plan.legacy:
        if os.path.lexists(new):
            raise MigrateError(f"{new} appeared meanwhile — nothing more "
                               f"renamed")
        new.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.rename(old, new)
        lock = old.with_name(f".{old.name}.lock")
        if lock.is_file() and not lock.is_symlink():
            try:
                lock.unlink()
            except OSError:
                pass
