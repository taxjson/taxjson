"""`taxjson migrate`: fold an old project's per-purpose files into
ticker.map and taxjson.toml.

The old files and where their contents go:

    yf_ticker.map                    -> ticker.map  QUOTE lines
    tv_exchange.map                  -> ticker.map  TRADINGVIEW lines
    crypto_ticker.map                -> ticker.map  CRYPTO lines
    ticker_extraction_overrides.txt  -> ticker.map  EXTRACT lines
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
`<name>.migrated` (never deleted). Nothing is written when any file
cannot be converted, or when the target already holds a conflicting
entry; the result is re-read and compared with what was intended
before anything is replaced.

While any old file is present every other `taxjson` command stops with
a message naming it (lib/migrate.legacy_message) — no silent fallback.
"""
from __future__ import annotations

import copy
import datetime as _dt
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from taxjson.lib.ticker_map import LEGACY_MAP_FILES

# Every old file, in the order migrate handles (and names) them.
LEGACY_DATA_FILES = {
    "amt_carryover.txt": "[estimate] amt_carryover",
    "claimed_losses.txt": "[carryover] claimed",
    "capital_gains_dividends.map": "[[capital_gains_dividends]]",
    "distributions.map": "[[distributions]]",
}
LEGACY_FILES: Tuple[str, ...] = tuple(LEGACY_MAP_FILES) + tuple(
    LEGACY_DATA_FILES)


class MigrateError(ValueError):
    """An old file migrate cannot convert, or a target that already
    holds a conflicting entry. Nothing was written."""


def legacy_files(root) -> List[str]:
    """The old per-purpose files present in the project root (a name
    that exists in any form — a dangling link or a directory too)."""
    r = Path(root)
    return [n for n in LEGACY_FILES if os.path.lexists(r / n)]


def legacy_message(names: List[str]) -> str:
    """The one message every command stops with while old files sit in
    the project."""
    where = []
    for n in names:
        target = (f"ticker.map {LEGACY_MAP_FILES[n]} lines"
                  if n in LEGACY_MAP_FILES
                  else f"taxjson.toml {LEGACY_DATA_FILES[n]}")
        where.append(f"{n} (now {target})")
    return ("this project still has " + ", ".join(where)
            + " — these files are no longer read, so their contents "
              "would be silently ignored. Run `taxjson migrate` to move "
              "them into ticker.map / taxjson.toml (preview with "
              "`taxjson migrate --dry-run`); nothing was run.")


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


def _conv_tradingview(text: str, out: _MapOut) -> None:
    """tv_exchange.map: `SYMBOL EXCHANGE`, a line STARTING with `#` is a
    comment (a `#` later in the line was not), extra words were ignored,
    later lines won. Keys are upper-cased now, like every ticker.map
    symbol (a lower-case key used to match nothing)."""
    eff: Dict[str, Tuple[int, str, str]] = {}
    order: List[str] = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            out.lines.append("# " + line.lstrip("#").strip())
            continue
        parts = line.split()
        if len(parts) < 2:
            out.notes.append(f"{out.name}:{n}: {line!r} has no exchange — "
                             f"it was ignored; kept as a comment")
            out.lines.append(f"# (ignored by the old loader) {line}")
            continue
        if "#" in parts[0] or "#" in parts[1]:
            raise MigrateError(
                f"{out.name}:{n}: {line!r} has a `#` inside the symbol or "
                f"exchange, which ticker.map reads as a comment — fix the "
                f"line first")
        note = " ".join(parts[2:]).lstrip("#").strip()
        sym = parts[0].upper()
        if sym != parts[0]:
            out.notes.append(f"{out.name}:{n}: key {parts[0]!r} is now "
                             f"{sym} (ticker.map symbols are upper case; "
                             f"the lower-case key matched nothing before)")
        if sym in eff:
            if eff[sym][1] != parts[1]:
                out.notes.append(f"{out.name}:{n}: {sym} repeats line "
                                 f"{eff[sym][0]} — the later line won; "
                                 f"only it is kept")
        else:
            order.append(sym)
        eff[sym] = (n, parts[1], note)
    for sym in order:
        _n, ex, note = eff[sym]
        out.lines.append(f"TRADINGVIEW {sym} {ex}{_note(note)}")
        out.rules.append(("tradingview", sym, ex))


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
        out.rules.append(("extract", key, sym))


_MAP_CONVERTERS = {
    "yf_ticker.map": _conv_quote,
    "tv_exchange.map": _conv_tradingview,
    "crypto_ticker.map": _conv_crypto,
    "ticker_extraction_overrides.txt": _conv_extract,
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
            + f" }}  # from {src} (taxjson migrate, {stamp})")
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
            from taxjson.bin.taxjson_carryover import load_claimed
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
    out = [f"\n# --- from {src} (taxjson migrate, {stamp}) ---"]
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
        will = "would " if dry_run else ""
        out = list(self.done)
        out += [f"note: {n}" for n in self.notes]
        for a, b in self.moves:
            out.append(f"{will}move {a} -> {b}")
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
    """What `taxjson migrate` would do in `root`. Raises MigrateError
    when an old file cannot be converted or a target conflicts."""
    from taxjson.lib.tomlcompat import tomllib
    root = Path(root).resolve()
    pl = Plan(root)
    pl.names = legacy_files(root)
    if not pl.names:
        return pl
    # ---------------------------------------------------- ticker.map
    map_names = [n for n in pl.names if n in LEGACY_MAP_FILES]
    if map_names:
        from taxjson.lib.ticker_map import (SideRules, add_side_rule,
                                            read_side_rules)
        from taxjson.bin.taxjson_ticker_map import _parse_map_file
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
                chunks.append(f"\n# --- from {name} (taxjson migrate, "
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
        text = cfg_path.read_text(encoding="utf-8-sig")
        try:
            cfg = tomllib.loads(text)
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
            got = tomllib.loads(new_text)
        except ValueError as e:
            raise MigrateError(
                f"taxjson.toml could not take the new entries safely ({e}) "
                f"— add them by hand (`taxjson migrate --dry-run` shows "
                f"them)") from None
        if _canon_cfg(got) != _canon_cfg(expect):
            raise MigrateError(
                "taxjson.toml did not read back as intended after adding "
                "the entries (an unusual layout of [estimate] or "
                "[carryover]) — add them by hand (`taxjson migrate "
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
    for n in pl.names:
        pl.moves.append((n, _migrated_name(root, n)))
    return pl


def _joined(before: str, add: str) -> str:
    if before and not before.endswith("\n"):
        before += "\n"
    return before + add


def apply(pl: Plan) -> None:
    """Write the plan: ticker.map and taxjson.toml replaced atomically
    (temp file + rename), then each old file renamed."""
    root = pl.root

    def _write(path: Path, text: str) -> None:
        tmp = path.with_name(path.name + ".migrate.part")
        tmp.write_text(text, encoding="utf-8")
        try:
            os.chmod(tmp, path.stat().st_mode & 0o777)
        except OSError:
            pass
        tmp.replace(path)

    if pl.map_append:
        tm = root / "ticker.map"
        before = tm.read_text(encoding="utf-8-sig") if tm.is_file() else (
            "# ticker.map — symbol rules and lookups (see `taxjson-ticker-"
            "map --help`).\n")
        _write(tm, _joined(before, pl.map_append))
    if pl.toml_text is not None:
        _write(root / "taxjson.toml", pl.toml_text)
    for a, b in pl.moves:
        os.replace(root / a, root / b)
