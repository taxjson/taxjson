"""Shared test helpers: what `tjs run` leaves for the cash commands, for books
a test built by hand.

`tjs cash`, `tjs fx-cash --ledger v2` and `tjs t1135` read the cash events of
the exports from the last run's parse (taxjson-brokerage --cash-out:
work/<account>_<broker>.cash), and refuse books older than the inputs (cli/common
`_refuse_cash_older_than_inputs`). A test whose books are an empty
work/<account>_sorted.json writes, as the run would, each taxable account's
sources list and cash sidecars and the inputs' record (`record_run`), right
before the command: the ledger then reads the inputs as they are.

Shared from: rules/ca/fx/test_foreign_cash_carry_and_damaged_inputs,
test_foreign_cash_carried_cost_and_same_day_funding,
test_foreign_cash_wallets_rewards_and_move_order (PLAN step 16 commit 6).
"""
import json
from pathlib import Path


def record_run(root: Path) -> None:
    """The sources lists, the cash sidecars and the inputs' record of a
    full run over `root`'s inputs as they are now (the books stay the
    test's)."""
    from _support.parser_cash import cash_sidecar
    from taxjson.lib import cash_events as CE
    from taxjson.lib.checklist import record_input_fingerprint
    from taxjson.lib.country import settings_country
    from taxjson.pipeline.config import load_config, settings_of
    from taxjson.pipeline.freshness import package_fingerprint
    from taxjson.pipeline.inputs import _is_generic_group, _split_generic_groups, group_inputs
    from taxjson.project.accounts import typed_account
    root = Path(root)
    cfg = load_config(root)
    country = settings_country(settings_of(cfg))
    work = root / "work"
    work.mkdir(parents=True, exist_ok=True)
    for label, acfg in sorted((cfg.get("accounts") or {}).items()):
        if not typed_account(acfg, label).taxable:
            continue
        grouped = _split_generic_groups(group_inputs(root / "inputs" / label))
        (work / f"{label}_sources.list").write_text(
            "".join(f"{b}/{p.name}\n" for b, ps in sorted(grouped.items())
                    for p in sorted(ps)), encoding="utf-8")
        for b, ps in grouped.items():
            doc = cash_sidecar("generic" if _is_generic_group(b) else b, label,
                               list(ps), country)
            CE.sidecar_path(work / f"{label}_{b}.json").write_text(
                json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    record_input_fingerprint(root, cfg, code=package_fingerprint())
