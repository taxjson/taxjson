"""The missing-history migration (`tjs migrate`): each year folder's
legacy missing_history.json entries, sized from a run's gains stage
(project_view, ProjectView), planned as .tt OPENING lines
(plan_missing_history_migration, MissingHistoryMigration) and written,
each file renamed to <name>.migrated (apply_missing_history_migration).
Out of lib/missing_history, which the pipeline imports, while the plan
runs the pipeline's book preparation (lib/pipeline.prepare_books)
(step 8 of the architecture plan: lib's import cycle)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from taxjson.project.accounts import account_of, accounts_of
from taxjson.project.settings import settings_of
from taxjson.model.transaction import TaxTransaction
from taxjson.lib.missing_history import (MissingHistoryPairs, TtLine,
                                         TtWritePlan, apply_tt_plan,
                                         default_opening_date,
                                         legacy_missing_history_file,
                                         plan_tt_write,
                                         read_legacy_entries,
                                         sizing_until,
                                         synthesize_openings)


MIGRATED_SUFFIX = ".migrated"


@dataclass
class ProjectView:
    """What one project's missing_history.json opens, sized as its run
    sizes it (the rows through its tax year's end; a recorded
    `quantity` as it is): `sized` {pair: units}, `later` {pair: units}
    for an entry short only after the year end (the deepest shortage of
    every row), `anchor` {pair: the symbol the opening is booked on},
    `stale` {pair: why it opens nothing}."""
    root: Path
    year: Optional[int]
    file: Path
    sized: Dict[Tuple[str, str], float] = field(default_factory=dict)
    later: Dict[Tuple[str, str], float] = field(default_factory=dict)
    stale: Dict[Tuple[str, str], str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return str(self.year) if self.year else self.root.name

    def view(self, pair: Tuple[str, str]) -> Optional[float]:
        if pair in self.sized:
            return self.sized[pair]
        return self.later.get(pair)

    def listed(self) -> Set[Tuple[str, str]]:
        return set(self.sized) | set(self.later) | set(self.stale)


class MigrationNeedsRun(ValueError):
    """A project whose books are not built: its entries cannot be
    sized (run `tjs run` there first)."""


def _project_year(root: Path) -> Optional[int]:
    from taxjson.lib import project_layout as _PL
    y = settings_of(_PL.read_config_soft(root)).year
    return y if isinstance(y, int) and not isinstance(y, bool) else None


def _log_of(cache: Path, account: str, mh_file: Path
            ) -> Optional[List[Dict[str, Any]]]:
    """The account's applied missing-history log from its last run
    (work/<acct>_gains.json), None when there is none or it is older
    than `mh_file` (the run did not apply this file)."""
    g = Path(cache) / f"{account}_gains.json"
    try:
        if g.stat().st_mtime < mh_file.stat().st_mtime:
            return None
        doc = json.loads(g.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    log = doc.get("missing_history_log") if isinstance(doc, dict) else None
    return log if isinstance(log, list) else None


def _gains_stage_rows(root: Path, account: str,
                      txs: List[TaxTransaction],
                      listed: Set[Tuple[str, str]]) -> List[TaxTransaction]:
    """The rows the account's gains stage sizes its openings on when the
    books were built WITHOUT the file (a run with it moved aside): its
    merged books less the transfer-in arrivals booked at the broker's
    book value for a listed pair (with the file, the entry covers the
    arrival and nothing is booked — transfer_in.mark_missing_history),
    after the same transfer handling (pipeline.prepare_books), so a
    converted entry opens what the run with the file opened."""
    txs = [t for t in txs
           if not (str(getattr(t, 'type', '') or '') == 'transfer_book_value'
                   and (str(t.symbol), str(t.account)) in listed)]
    import contextlib
    import io
    from taxjson.lib import project_layout as _PL
    from taxjson.lib.pipeline import prepare_books
    doc = _PL.read_config_soft(root)
    acfg = account_of(accounts_of(doc), account)
    st = doc.get("settings") or {}
    try:
        from taxjson.lib.country import settings_country
        country = settings_country(st)
    except Exception:                                   # noqa: BLE001
        country = None
    with contextlib.redirect_stderr(io.StringIO()):
        out, _sh, _af, _log = prepare_books(
            txs, [], [], taxable=acfg.taxable,
            phantom_hint=False, country=country,
            base_currency=st.get("base_currency"),
            spot_crypto=bool(acfg.crypto),
            transfers_as_acquisitions=bool(
                st.get("transfers_as_acquisitions")))
    return out


def project_view(root: Any) -> Optional[ProjectView]:
    """The ProjectView of project `root`'s missing_history.json (None
    without one). Each entry is sized as the project's run sized it: its
    last run's applied log (work/<acct>_gains.json) when that run read
    this file, else the same synthesize_openings call on the account's
    merged books. Raises MigrationNeedsRun when an account's books are
    not built; what load_missing_history raises."""
    from taxjson.lib.tx_load import load_transactions
    root = Path(root)
    mh = legacy_missing_history_file(root)
    if mh is None:
        return None
    pairs = read_legacy_entries(mh)
    year = _project_year(root)
    until = sizing_until(year)
    v = ProjectView(root, year, mh)
    cache = root / "work"
    for acct in sorted({a for _s, a in pairs}):
        mine = MissingHistoryPairs({p for p in pairs if p[1] == acct})
        mine.quantities = {k: q for k, q in pairs.quantities.items()
                           if k[1] == acct}
        log = _log_of(cache, acct, mh)
        if log is not None and not mine <= {
                (str(e.get('symbol') or ''), str(e.get('account') or ''))
                for e in log if isinstance(e, dict)}:
            # (that run did not apply this file — a run with it moved
            # aside: sized from the books instead)
            log = None
        if log is None:
            base = cache / f"{acct}_base.json"
            if not base.is_file():
                raise MigrationNeedsRun(
                    f"{root}: no work/{acct}_base.json — its {mh.name} "
                    f"entries are sized from its books: move the file "
                    f"aside (`mv {mh.name} {mh.name}.hold`), `tjs "
                    f"run` there, move it back and run `tjs migrate` "
                    f"again")
            txs = _gains_stage_rows(root, acct, load_transactions(base),
                                    mine)
            _o, log = synthesize_openings(txs, mine, flag_stale=False,
                                          until=until)
        for e in log:
            pair = (str(e.get('symbol') or ''), str(e.get('account') or ''))
            if pair not in mine:
                continue
            if e.get('inserted'):
                v.sized[pair] = float(e.get('opening_qty') or 0.0)
            elif float(e.get('opening_all_rows') or 0.0) > 0:
                v.later[pair] = float(e['opening_all_rows'])
            else:
                v.stale[pair] = str(e.get('note') or 'opens nothing')
        for pair in mine:
            if pair not in v.listed():
                v.stale[pair] = 'opens nothing'
    return v


@dataclass
class MissingHistoryMigration:
    """`tjs migrate`'s plan for missing_history.json: the views of
    every project that has one (the year folders sharing the exports),
    the merged lines (each entry as the NEWEST project listing it sizes
    it), `agreed` (every project listing it the same), `differ` (lines
    saying how the projects disagree: another quantity, or not listed
    in some), `stale` (opens nothing in any project: dropped) and the
    write plan."""
    views: List[ProjectView]
    lines: List[TtLine] = field(default_factory=list)
    agreed: List[Tuple[str, str]] = field(default_factory=list)
    differ: List[str] = field(default_factory=list)
    stale: List[str] = field(default_factory=list)
    plan: Optional[TtWritePlan] = None
    nodate: List[str] = field(default_factory=list)
    # The files listing an entry that cannot be converted (`nodate`):
    # never archived (issue #25).
    unconverted: Set[Path] = field(default_factory=set)


def migration_projects(root: Any) -> List[Path]:
    """The projects whose missing_history.json `tjs migrate` merges:
    with exports shared by every year (`inputs_dir`), every year folder
    beside `root` reading the same folder; else `root` alone."""
    from taxjson.lib import project_layout as _PL
    root = Path(root).resolve()
    if not _PL.shared_inputs(root):
        return [root]
    mine = _PL.inputs_dir(root).resolve()
    out = []
    for _y, d in _PL.year_dirs(root.parent):
        try:
            if _PL.inputs_dir(d).resolve() == mine:
                out.append(d.resolve())
        except OSError:
            continue
    return out or [root]


def _view_parts(views: List[ProjectView], pair: Tuple[str, str]) -> str:
    """How each project's view sizes `pair` ("2024: 5, 2025: opens
    nothing (...)")."""
    parts = []
    for v in views:
        x = v.view(pair)
        if pair not in v.listed():
            parts.append(f"{v.label}: not listed")
        elif x is None:
            parts.append(f"{v.label}: opens nothing "
                         f"({v.stale.get(pair, '')})")
        else:
            parts.append(f"{v.label}: {x:g}"
                         + (" (short only after its year)"
                            if pair in v.later else ""))
    return ", ".join(parts)


def plan_missing_history_migration(root: Any) -> Optional[
        MissingHistoryMigration]:
    """The plan (None when no project has a missing_history.json)."""
    projects = migration_projects(root)
    views = [v for v in (project_view(p) for p in projects) if v is not None]
    if not views:
        return None
    views.sort(key=lambda v: (v.year or 0, str(v.root)))
    m = MissingHistoryMigration(views)
    newest = views[-1]
    pairs = set()
    for v in views:
        pairs |= v.listed()
    multi = len(views) > 1
    for pair in sorted(pairs, key=lambda p: (p[1], p[0])):
        sym, acct = pair
        vals = [(v, v.view(pair)) for v in views if pair in v.listed()]
        sized = [(v, q) for v, q in vals if q is not None]
        # The NEWEST view listing the entry decides (issue #28), an
        # explicit "opens nothing" (its books never go short) included:
        # an older year's opening is not reinstated over it. A view whose
        # books have no row of the pair (`no rows`: the account or the
        # symbol is not in that year's books) cannot size it, and does
        # not decide — as a year that does not list it.
        decisive = [(v, q) for v, q in vals
                    if q is not None or not v.stale.get(pair, '')
                    .startswith('no rows')]
        if not sized or not decisive or decisive[-1][1] is None:
            if sized:
                src = decisive[-1][0]
                m.differ.append(
                    f"{sym} / {acct}: " + _view_parts(views, pair)
                    + f" — written: nothing ({src.label}'s view: "
                    f"{src.stale.get(pair, 'opens nothing')})")
                continue
            why = "; ".join(f"{v.label}: {v.stale.get(pair, '')}"
                            for v, _q in vals) if multi else \
                vals[0][0].stale.get(pair, '')
            m.stale.append(f"{sym} / {acct}: {why}")
            continue
        src, q = decisive[-1]
        agree = (len(sized) == len(views)
                 and all(abs(x - q) <= 1e-9 * max(1.0, q) for _v, x in sized))
        if agree:
            m.agreed.append(pair)
        else:
            m.differ.append(f"{sym} / {acct}: " + _view_parts(views, pair)
                            + f" — written: {q:g} ({src.label}'s view)")
        # The date: the day before the account's first row in the books
        # of the view that sized it (issue #25: an account only an older
        # year's books carry), else of another view listing it.
        d = None
        for v in [src] + [w for w, _q in reversed(vals) if w is not src]:
            d = default_opening_date(v.root / "work", acct)
            if d is not None:
                break
        if d is None:
            m.nodate.append(f"{sym} / {acct}")
            m.unconverted.update(v.file for v, _q in vals)
            continue
        where = (f"{src.label}/{src.file.name}" if multi else src.file.name)
        m.lines.append(TtLine(acct, sym, q, d, f"migrated from {where}"))
    m.plan = plan_tt_write(newest.root, m.lines)
    return m


def apply_missing_history_migration(m: MissingHistoryMigration
                                    ) -> Tuple[List[Path], List[Path]]:
    """Write the plan's lines, then rename each project's
    missing_history.json to <name>.migrated (never deleted). Returns
    (the .tt files written, the files renamed). A plan with an entry it
    cannot convert (`nodate`) is refused, nothing written or renamed
    (issue #25: a file is archived only once all of it is converted)."""
    if m.nodate:
        raise MigrationNeedsRun(
            "cannot convert " + ", ".join(m.nodate) + " (no books for the "
            "account in any year folder listing it) — nothing written, "
            "nothing renamed: " + ", ".join(
                str(p) for p in sorted(m.unconverted)))
    written = apply_tt_plan(m.plan) if m.plan is not None else []
    renamed = []
    for v in m.views:
        dst = v.file.with_name(v.file.name + MIGRATED_SUFFIX)
        n = 1
        while dst.exists() or dst.is_symlink():
            n += 1
            dst = v.file.with_name(f"{v.file.name}{MIGRATED_SUFFIX}{n}")
        os.replace(v.file, dst)
        renamed.append(dst)
    return written, renamed
