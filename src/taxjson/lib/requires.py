"""The oldest taxjson a project runs on: `[settings] requires_taxjson`.

A project records the minimum taxjson version its layout and files need
(`requires_taxjson = ">=0.27.1"`), and every command of an older taxjson
refuses it with one line naming the version to install. An older
release ignored a setting it did not know: a v0.27.1 project (one
tobase.map shared by the year folders, `[settings] tobase_map`) run with
v0.27.0 read no tobase.map at all and failed confusingly — or could
have computed other figures without a word.

FEATURES is the one list of layout features and the release that first
reads each: a future layout change adds one line. The writers name what
they write and raise the key to the newest feature's release (never
lower it): `tjs init` (the layout it makes), `new-year` (the copy
keeps the key, raised for its own layout), `migrate` (the layout it
migrated to), `update-tobase-map --write`, `find-missing-history
--write-missing-history` and `align --write`.

A feature added between releases names the release that will first read
it, and scripts/release.sh --prepare refuses a release whose new features
name another. Until then the development tree's install still reports
the last release (the version commit is made at the release): this code
reads every feature FEATURES names, so it runs a project asking for the
newest of them (problem), while the last release refuses it.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import (Any, Iterable, List, Mapping, NamedTuple, Optional,
                    Set, Tuple)

from taxjson.project.toml_io import decode_toml_bytes, parse_toml

KEY = "requires_taxjson"


class Feature(NamedTuple):
    name: str
    version: str        # the first taxjson release that reads it
    what: str


# feature -> the first release that reads it. One line per layout
# change; the writers name features, never versions.
FEATURES: Tuple[Feature, ...] = (
    Feature("shared_exports", "0.26.0",
            "a year folder reading exports shared by every year "
            "([settings] inputs_dir / holdings_dir / exports_dir)"),
    Feature("missing_history_tt", "0.27.0",
            "missing history as .tt `OPENING ... cost=unknown` lines "
            "(missing_history.json is no longer read)"),
    Feature("tobase_map", "0.27.0",
            "tobase.map beside ticker.map: the interlisted pairs (Canada)"),
    Feature("shared_tobase_map", "0.27.1",
            "one tobase.map every year reads ([settings] tobase_map)"),
    Feature("elections_json", "3.0.0",
            "each account's elections in inputs/<account>/elections.json "
            "(manifest.json, the name before, is refused)"),
)
_BY_NAME = {f.name: f for f in FEATURES}

# What `tjs init` writes into every new project: the form its
# missing history takes (find-missing-history writes .tt lines) and
# the name of its elections file.
INIT_FEATURES: Tuple[str, ...] = ("missing_history_tt", "elections_json")

_SPEC_RE = re.compile(r"\s*>=\s*(\d+)\.(\d+)\.(\d+)\s*")
_EXAMPLE = '">=0.27.1"'
UPGRADE = "upgrade: `tjs deploy` or re-run the installer"

Version = Tuple[int, int, int]


class RequiresError(ValueError):
    """A requires_taxjson value that is not `">=X.Y.Z"`."""


def parse_spec(value: Any) -> Version:
    """(X, Y, Z) of a `">=X.Y.Z"` value; RequiresError otherwise."""
    m = _SPEC_RE.fullmatch(value) if isinstance(value, str) else None
    if m is None:
        raise RequiresError(
            f"[settings] {KEY} must be \">=X.Y.Z\", the oldest taxjson "
            f"this project runs on, such as {_EXAMPLE} (got {value!r})")
    return (int(m[1]), int(m[2]), int(m[3]))


def version_tuple(text: str) -> Optional[Version]:
    """(X, Y, Z) of an installed version ("0.28.2", "0.28.2.dev3+g1a2b"):
    its first three numbers, or None when it has none."""
    m = re.match(r"\s*v?(\d+)\.(\d+)\.(\d+)", str(text or ""))
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def shown(v: Version) -> str:
    return ".".join(str(n) for n in v)


def spec(v: Version) -> str:
    return f">={shown(v)}"


def running_version() -> Optional[str]:
    """The installed taxjson's version (what `tjs --version` says),
    or None when the package metadata cannot be read."""
    try:
        from importlib.metadata import version
        return version("taxjson")
    except Exception:                                   # noqa: BLE001
        return None


def newest_read() -> Version:
    """The newest release FEATURES names: this code reads every feature
    up to it, whatever version its install reports."""
    return max(version_tuple(f.version) or (0, 0, 0) for f in FEATURES)


def problem(table: Any, where: str = "this project") -> Optional[str]:
    """Why this taxjson cannot run a project whose [settings] are
    `table`: a requires_taxjson newer than both the installed version
    and every feature this code reads (newest_read: a development tree
    between releases reports the last one), or one that is not
    `">=X.Y.Z"`. None when it may run (no key, or the installed version
    is unknown)."""
    v = table.get(KEY) if isinstance(table, Mapping) else getattr(table, KEY, None)
    if v is None:
        return None
    try:
        need = parse_spec(v)
    except RequiresError as e:
        return str(e)
    have_text = running_version()
    have = version_tuple(have_text) if have_text else None
    if have is None or max(have, newest_read()) >= need:
        return None
    return (f"{where} needs taxjson {shown(need)} or newer (installed: "
            f"{have_text}) — {UPGRADE}")


def features_of(table: Mapping[str, Any],
                own_tobase: bool = False) -> Set[str]:
    """The layout features a project with these [settings] uses (and,
    `own_tobase`, a tobase.map of its own beside ticker.map)."""
    from taxjson.lib import project_layout as PL
    if hasattr(table, "as_dict"):          # the typed object: its raw table
        table = table.as_dict()
    out: Set[str] = set()
    if any(table.get(k) is not None for k in PL.FOLDER_KEYS):
        out.add("shared_exports")
    if table.get(PL.TOBASE_KEY) is not None:
        out.add("shared_tobase_map")
    elif own_tobase and _canada(table):
        out.add("tobase_map")
    return out


def _canada(settings: Mapping[str, Any]) -> bool:
    from taxjson.lib.country import CountryError, settings_country
    try:
        return settings_country(settings) == "canada"
    except CountryError:
        return False


def minimum(features: Iterable[str]) -> Optional[Version]:
    """The release that reads every one of `features` (None: none)."""
    vs = [version_tuple(_BY_NAME[f].version) for f in features]
    return max(vs) if vs else None


def raised(current: Any, features: Iterable[str]) -> Optional[str]:
    """The requires_taxjson value a project with `features` needs given
    its `current` value: None when `current` already covers them (never
    lowered). RequiresError when `current` is not `">=X.Y.Z"`."""
    need = minimum(features)
    if need is None:
        return None
    if current is not None and parse_spec(current) >= need:
        return None
    return spec(need)


def raised_text(text: str, features: Iterable[str] = (),
                own_tobase: bool = False, since: Optional[str] = None
                ) -> str:
    """taxjson.toml `text` with requires_taxjson raised to cover the
    layout its settings show (features_of) plus `features` — with
    `since` (the text before a change), only the layout features the
    change added; the same text when the key already covers them."""
    from taxjson.lib import project_layout as PL

    def _settings(t: str) -> Mapping[str, Any]:
        s = parse_toml(t).get("settings") or {}
        return s if isinstance(s, dict) else {}
    table = _settings(text)
    feats = features_of(table, own_tobase)
    if since is not None:
        feats -= features_of(_settings(since), own_tobase)
    new = raised(table.get(KEY), feats | set(features))
    if new is None:
        return text
    return PL.set_key_after(text, f"settings.{KEY}", new, "country")


def raise_requirement(root: Any, features: Iterable[str] = (),
                      keep_layout: bool = False) -> Optional[str]:
    """Raise project `root`'s requires_taxjson for its layout and
    `features` (taxjson.toml rewritten in place, through safe_write;
    never lowered) — with `keep_layout`, a file in the template layout
    stays in it (_laid_out: a raise no command asked for leaves `tjs
    format` and the checklist nothing to say). The value written, or
    None when unchanged."""
    from taxjson.lib import project_layout as PL
    from taxjson.lib.safe_write import write_user_file
    root = Path(root)
    path = root / PL.CONFIG
    text = decode_toml_bytes(path.read_bytes())
    new = raised_text(text, features,
                      own_tobase=(root / PL.TOBASE_MAP).is_file())
    if new == text:
        return None
    if keep_layout:
        new = _laid_out(text, new)
    write_user_file(path, new, root, backup=False)
    return parse_toml(new)["settings"][KEY]


def _laid_out(before: str, after: str) -> str:
    """`after` (`before` with a key set) in the template layout when
    `before` was in it and `after` can be laid out losslessly; else
    `after` as it is (lib/config_template.format_config)."""
    from taxjson.lib.config_template import FormatError, format_config
    try:
        if format_config(before).changed:
            return after
        res = format_config(after)
    except FormatError:
        return after
    return res.text if res.changed else after


def raise_for_elections(path: Any) -> List[Tuple[Path, str]]:
    """Raise requires_taxjson to the release that reads elections.json
    (`elections_json`) in each project that reads the elections file
    `path`: the project holding its inputs folder, or each year folder
    beside it that reads it as the shared inputs folder (an older
    taxjson there would see no elections: it asks them again, or books
    a sheltered account's event by the zero default). Called by the one
    writer of the file, lib/elections_save `update`, before it saves: `tjs elections --set` / `--redo` / `--reset`, the run's
    prompt and auto-elections, taxjson-corp-actions. A year frozen with
    its elections as manifest.json is written under that name: nothing
    is raised. [(project folder, value written)]; ValueError naming the
    taxjson.toml (from the folder holding the inputs folder) when one
    cannot be read, parsed or written: which file it reads cannot be
    told, so it is never skipped, and every one is read before the
    first is written."""
    import os
    from taxjson.lib import project_layout as PL
    path = Path(path)
    if path.name != PL.ELECTIONS:
        return []
    acct, folder = path.parent.name, path.parent.parent.parent
    want = os.path.realpath(path)

    def _cannot(d: Path, e: Exception) -> ValueError:
        return ValueError(f"cannot record the taxjson version "
                          f"{PL.shown(d / PL.CONFIG, folder)} needs: "
                          f"{getattr(e, 'strerror', None) or e}")
    readers: List[Path] = []
    for d in [folder] + [d for _y, d in PL.year_dirs(folder)]:
        if not PL.has_config(d):
            continue
        try:
            parse_toml(decode_toml_bytes((d / PL.CONFIG).read_bytes()))
            src = PL.folder_setting(d, PL.INPUTS_KEY) or d / PL.INPUTS
            if os.path.realpath(PL.elections_file(src / acct, d)) == want:
                readers.append(d)
        except (OSError, ValueError) as e:
            raise _cannot(d, e) from e
    out: List[Tuple[Path, str]] = []
    for d in readers:
        try:
            new = raise_requirement(d, ("elections_json",), keep_layout=True)
        except (OSError, ValueError) as e:
            raise _cannot(d, e) from e
        if new is not None:
            out.append((d, new))
    return out


def raised_note(raised: Iterable[Tuple[Path, str]], base: Any) -> List[str]:
    """One line per value raise_for_elections wrote, its taxjson.toml
    shown from `base`, as the other raisers say it (cli/common
    `_raise_requirements`)."""
    from taxjson.lib import project_layout as PL
    return [f"[settings] {KEY} = \"{new}\" (the oldest taxjson that "
            f"reads elections.json) in {PL.shown(d / PL.CONFIG, Path(base))}"
            for d, new in raised]


def lowers(key: str, here: Any, there: Any) -> bool:
    """`tjs align` bringing `key` = `there` over `here` would lower
    or remove this project's requires_taxjson (never done)."""
    if key != f"settings.{KEY}" or here is None:
        return False
    try:
        return there is None or parse_spec(there) <= parse_spec(here)
    except RequiresError:
        return True
