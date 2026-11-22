"""Shared test helpers: what a full `tjs run` leaves beside the books, for a
test that builds the books by hand: the record of the inputs it read and of
the taxjson that built them (work/.inputs_fingerprint.json), and each taxable
equity account's wash-adjusted gains (the pass across accounts writes them
for an account alone too). close-year, check-filed and form-export refuse
books with no record, another taxjson's (backlog TJ-4309, TJ-4310), or an
equity account with plain gains only (TJ-4305).

Shared from: test_partition_foundation, test_partition_commands, test_form_export,
test_schedule3_lines, test_ui_findings_minimal_repros, test_cross_tool_flow_consistency,
test_other_year_banner, test_form_export_footing, test_lock_read_checks,
test_deleted_lock_dividend_on_a_short_and_blend_messages
"""
import shutil
from pathlib import Path


def record_full_run(root) -> None:
    """Leave `root`'s hand-built books as a full `tjs run` by this taxjson
    would: a taxable equity account with plain gains only gets the same rows
    as its wash-adjusted gains (a fixture's books wash nothing across
    accounts), and work/.inputs_fingerprint.json records the inputs."""
    from taxjson.lib import checklist
    from taxjson.lib.project_layout import read_config
    from taxjson.pipeline.freshness import package_fingerprint
    from taxjson.project.accounts import accounts_of
    root = Path(root)
    cfg = read_config(root)
    work = root / "work"
    for name, acct in accounts_of(cfg).items():
        if not acct.taxable or acct.crypto:
            continue
        plain, wash = work / f"{name}_gains.json", work / f"{name}_gains_wash.json"
        if plain.is_file() and not wash.exists():
            shutil.copyfile(plain, wash)
    checklist.record_input_fingerprint(root, cfg, code=package_fingerprint())
