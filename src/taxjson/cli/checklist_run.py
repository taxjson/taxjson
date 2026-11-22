"""What lib/checklist's steps run and read of the run's own code, built
here because lib may not import the commands, the pipeline or bin (step 8
of the architecture plan): the run's readers (checklist.RunHelpers, on the
Ctx and to question_answers) and `default_run_sub` (Ctx.run_sub: `tjs`
itself as a child process). cli/setup and cli/file pass them."""
import os
import subprocess
from pathlib import Path
from typing import Callable, List, Tuple

from taxjson.cli.common import _double_roc_warnings, _year_keep
from taxjson.cli.estimate import _fx_cash_status
from taxjson.lib.checklist import RunHelpers
from taxjson.lib.dispatch import python_module_argv
from taxjson.pipeline.stages import _crypto_broker_files


def _double_roc(root, accounts, year):
    return _double_roc_warnings(root, accounts, _year_keep(year))


def _broker_files(root, cfg):
    return _crypto_broker_files(root, cfg)


def _fx_cash(root, work):
    return _fx_cash_status(root, work)


def checklist_run_helpers() -> RunHelpers:
    """The roc-entered, crypto-sends and fx-cash readers. Each calls this
    module's binding when the step runs, so a test's patch_run of the
    name reaches the step."""
    return RunHelpers(double_roc=_double_roc, crypto_broker_files=_broker_files,
                      fx_cash_status=_fx_cash)


def default_run_sub(root: Path) -> Callable[..., Tuple[int, str, str]]:
    """Run `taxjson <argv>` on this project as a subprocess (the sub-
    commands sys.exit and print; a subprocess keeps that contained): the
    checklist's Ctx.run_sub. Here, not in lib/checklist: it names the
    entry point, a bin module (step 8)."""
    def run(argv: List[str], timeout: int = 900) -> Tuple[int, str, str]:
        # Never with the project folder on sys.path (security review H1).
        cmd = python_module_argv("taxjson.bin.taxjson_run",
                                 ["-C", str(root), *argv])
        try:
            p = subprocess.run(cmd, capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=timeout,
                               # Read by the detectors below, never
                               # shown as is: unwrapped (lib/out).
                               env={**os.environ, "NO_COLOR": "1",
                                    "TAXJSON_WIDTH": "0"})
        except subprocess.TimeoutExpired:
            return 124, "", f"timed out after {timeout}s"
        return p.returncode, p.stdout or "", p.stderr or ""
    return run
