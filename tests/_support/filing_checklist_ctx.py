"""Shared test helpers: a filing checklist project and its context.

Shared from: test_fix_filing_a
"""
from pathlib import Path

from datetime import date as _date


class _Sub:
    def __init__(self, table=None):
        self.table = table or {}
        self.calls = []

    def __call__(self, argv, timeout=900):
        self.calls.append(list(argv))
        return self.table.get(argv[0], (0, "", ""))


_CL_TOML = ('[settings]\nyear = 2026\ncountry = "canada"\n'
            '[accounts.margin]\ntype = "taxable"\n'
            '[accounts.rrsp]\ntype = "sheltered"\n')


def _cl_project(root: Path, toml: str = _CL_TOML):
    from taxjson.pipeline import config as pipeline_config
    (root / "taxjson.toml").write_text(toml)
    for a in ("margin", "rrsp"):
        (root / "inputs" / a).mkdir(parents=True)
        (root / "inputs" / a / f"{a}.csv").write_text("x\n")
    (root / "work").mkdir()
    (root / "reports").mkdir()
    for a in ("margin", "rrsp"):
        (root / "reports" / f"{a}.sum").write_text(
            "DIAGNOSTICS\nvalidation: 0 error(s)\n")
    return pipeline_config.load_config(root)


def _cl_ctx(root, cfg, table=None, year=2026):
    from taxjson.lib import checklist as cl
    return cl.Ctx(root=root, cfg=cfg, year=year, today=_date(2026, 9, 29),
                  run_sub=_Sub(table))
