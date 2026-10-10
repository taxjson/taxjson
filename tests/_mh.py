"""Missing history for tests: the .tt `OPENING <date> <SYMBOL> <qty>
cost=unknown` lines (lib/missing_history), written the way `taxjson
migrate` converts an old missing_history.json entry — each line opens
what the entry used to: the deepest shortage of the pair's rows (through
`until`, the tax year's end, when given) or its recorded `quantity`,
dated at the first row of the pair's rename chain.

    from _mh import mh_dir, tt_from_books, tt_lines

    d = mh_dir(txs, [("QZQ.TO", "margin")])        # a folder a stage's
    prepare_books(..., incomplete_history=d)       # --incomplete-history reads

    run(root); tt_from_books(root, [("QZQ.TO", "margin")]); run(root)

Synthetic data only.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

Entry = Any     # ("SYM", "acct") or {"symbol", "account", ["quantity"]}


def _pairs(entries: Iterable[Entry]):
    from taxjson.lib.missing_history import MissingHistoryPairs
    out = MissingHistoryPairs()
    for e in entries:
        if isinstance(e, dict):
            pair = (str(e["symbol"]).upper(), str(e["account"]))
            if e.get("quantity") is not None:
                out.quantities[pair] = float(e["quantity"])
        else:
            pair = (str(e[0]).upper(), str(e[1]))
        out.add(pair)
    return out


def _as_txs(txs: Iterable[Any]):
    from taxjson.lib.core import TaxTransaction
    fields = TaxTransaction.__dataclass_fields__
    out = []
    for t in txs:
        if isinstance(t, dict):
            t = TaxTransaction(**{k: v for k, v in t.items() if k in fields})
        out.append(t)
    return out


def tt_lines(txs: Iterable[Any], entries: Iterable[Entry], *,
             until: Optional[str] = None) -> Dict[str, List[str]]:
    """{account: [OPENING ... cost=unknown lines]} the entries open on
    `txs` (TaxTransaction or row dicts). An entry that opens nothing (no
    rows, never short) gives no line."""
    from taxjson.bin.taxjson_convert_tt import unknown_opening_text
    from taxjson.lib.missing_history import synthesize_openings
    _o, log = synthesize_openings(_as_txs(txs), _pairs(entries),
                                  flag_stale=False, until=until)
    out: Dict[str, List[str]] = {}
    for e in log:
        if e.get("inserted"):
            out.setdefault(e["account"], []).append(unknown_opening_text(
                e["anchor_date"], e["symbol"], float(e["opening_qty"])))
    return out


def write_lines(inputs: Path, lines: Dict[str, List[str]]) -> List[Path]:
    """Append the lines to inputs/<account>/missing_history.tt."""
    out = []
    for acct, lns in lines.items():
        f = Path(inputs) / acct / "missing_history.tt"
        f.parent.mkdir(parents=True, exist_ok=True)
        old = f.read_text() if f.exists() else ""
        f.write_text(old + "".join(ln + "\n" for ln in lns))
        out.append(f)
    return out


def mh_dir(txs: Iterable[Any], entries: Iterable[Entry], *,
           until: Optional[str] = None, accounts: Sequence[str] = (),
           root: Optional[Path] = None) -> Path:
    """A project folder holding the entries' lines — what a stage's
    --incomplete-history names: taxjson.toml with the accounts (those of
    the entries, plus `accounts`) and inputs/<account>/missing_history.tt."""
    entries = list(entries)
    if root is None:
        from _tmpfiles import private_dir
        root = Path(private_dir(prefix="taxjson-mh-"))
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    accts = sorted({a for _s, a in _pairs(entries)} | set(accounts))
    cfg = root / "taxjson.toml"
    if not cfg.exists():
        cfg.write_text("".join(f'[accounts.{a}]\ntype = "taxable"\n\n'
                               for a in accts))
    for a in accts:
        (root / "inputs" / a).mkdir(parents=True, exist_ok=True)
    write_lines(root / "inputs", tt_lines(txs, entries, until=until))
    return root


def tt_from_books(root: Path, entries: Iterable[Entry], *,
                  year: Optional[int] = None) -> List[Path]:
    """After a `taxjson run` of project `root` (its work/<acct>_base.json
    books): write the entries' lines into its accounts' inputs (the
    shared folder with `inputs_dir`), sized through `year`'s end (default
    the project's year) — then run again."""
    from taxjson.lib import project_layout as _PL
    from taxjson.lib.missing_history import sizing_until
    entries = list(entries)
    if year is None:
        st = _PL.read_config_soft(root).get("settings") or {}
        year = st.get("year")
    rows: List[Any] = []
    for a in sorted({a for _s, a in _pairs(entries)}):
        p = Path(root) / "work" / f"{a}_base.json"
        doc = json.loads(p.read_text(encoding="utf-8"))
        rows += doc.get("transactions", doc) if isinstance(doc, dict) else doc
    return write_lines(_PL.inputs_dir(root),
                       tt_lines(rows, entries, until=sizing_until(year)))


def from_json(path: Path, txs: Iterable[Any], *,
              until: Optional[str] = None) -> Path:
    """The folder of lines an old missing_history.json at `path` (as a
    test wrote it) converts to on `txs` — what --incomplete-history /
    GainsRequest.incomplete_history name now."""
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return mh_dir(list(txs), [e for e in data if isinstance(e, dict)],
                  until=until)
