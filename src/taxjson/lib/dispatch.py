"""In-process dispatch for taxjson sub-tools.

The orchestrator historically spawned every sub-tool as
`[sys.executable, "-m", "taxjson.bin.<module>"]`. That breaks the
moment the app is frozen (PyInstaller's sys.executable is the bundled
app, not Python) and pays interpreter start-up per stage. `run_cmd`
recognizes that command shape and executes the tool IN-PROCESS instead:
import the module, shim sys.argv, redirect stdio, map SystemExit to a
return code — returning a subprocess.CompletedProcess so call sites
keep their shape.

Anything that isn't a taxjson tool command, needs a TTY
(interactive=True — the corp-action election prompts), or is forced by
TAXJSON_DISPATCH=subprocess, still runs as a real subprocess.

In-process caveats (accepted; the tools are argv-driven and
stateless-by-design): module-level caches persist across calls within
one orchestrator run (a feature — parsers and price caches stay warm),
and `cwd=` is honored by chdir around the call (the orchestrator runs
tools sequentially).
"""

import contextlib
import importlib
import io
import os
import subprocess
import sys
from typing import IO, List, Optional, Tuple

_ENV_FLAG = "TAXJSON_DISPATCH"


def tool_module(cmd: List[str]) -> Optional[Tuple[str, List[str]]]:
    """(module_name, argv) when `cmd` is a `python -m taxjson.bin.X`
    invocation, else None."""
    if (len(cmd) >= 3 and cmd[0] == sys.executable and cmd[1] == "-m"
            and str(cmd[2]).startswith("taxjson.bin.")):
        return str(cmd[2]), [str(a) for a in cmd[3:]]
    return None


def _use_subprocess() -> bool:
    return os.environ.get(_ENV_FLAG, "").strip().lower() == "subprocess"


def run_cmd(cmd: List[str], *,
            capture_output: bool = False,
            stdout: Optional[IO] = None,
            cwd: Optional[str] = None,
            interactive: bool = False,
            ) -> subprocess.CompletedProcess:
    """Run a tool command, in-process when possible.

    - capture_output: stdout+stderr returned as text on the result.
    - stdout: a writable TEXT file object stdout streams into
      (stderr is captured and returned — the run_to_file diag shape).
    - neither: the tool streams LIVE to the current stdio (wrapper
      passthrough); result stdout/stderr are None.
    - interactive: inherit the TTY (elections); always a subprocess.
    """
    spec = tool_module(cmd)
    if spec is None or interactive or _use_subprocess():
        if stdout is not None:
            return subprocess.run(cmd, stdout=stdout,
                                  stderr=(None if interactive
                                          else subprocess.PIPE),
                                  stdin=(None if interactive
                                         else subprocess.DEVNULL),
                                  cwd=cwd, text=not interactive)
        return subprocess.run(cmd, capture_output=capture_output,
                              text=True, cwd=cwd)

    module_name, argv = spec
    out_buf = io.StringIO() if capture_output else None
    err_buf = io.StringIO() if (capture_output or stdout is not None) \
        else None
    out_target = stdout if stdout is not None else out_buf

    old_argv = sys.argv
    old_cwd = os.getcwd() if cwd else None
    rc = 0
    try:
        if cwd:
            os.chdir(cwd)
        sys.argv = [module_name.rsplit(".", 1)[-1]] + argv
        module = importlib.import_module(module_name)
        with contextlib.ExitStack() as stack:
            if out_target is not None:
                stack.enter_context(contextlib.redirect_stdout(out_target))
            if err_buf is not None:
                stack.enter_context(contextlib.redirect_stderr(err_buf))
            try:
                r = module.main()
                rc = int(r) if r is not None else 0
            except SystemExit as e:
                if e.code is None:
                    rc = 0
                elif isinstance(e.code, int):
                    rc = e.code
                else:                       # sys.exit("message")
                    print(e.code, file=(err_buf if err_buf is not None
                                        else sys.stderr))
                    rc = 1
            except KeyboardInterrupt:
                raise
            except Exception:              # noqa: BLE001 — subprocess
                # isolation semantics: a crashing tool is a non-zero
                # exit with its traceback on stderr, never a crash of
                # the orchestrator.
                import traceback
                traceback.print_exc(file=(err_buf if err_buf is not None
                                          else sys.stderr))
                rc = 1
    finally:
        sys.argv = old_argv
        if old_cwd is not None:
            os.chdir(old_cwd)

    return subprocess.CompletedProcess(
        cmd, rc,
        stdout=(out_buf.getvalue() if out_buf is not None else None),
        stderr=(err_buf.getvalue() if err_buf is not None else None))
