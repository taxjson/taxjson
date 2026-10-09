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
from typing import IO, List, Optional, Sequence, Tuple

_ENV_FLAG = "TAXJSON_DISPATCH"

# Python < 3.11 has no -P: `-c` puts the current directory first on
# sys.path too, so the bootstrap drops that entry before it imports
# anything (runpy included) and then runs the module as `-m` would.
_SAFE_BOOT = ("import sys\n"
              "if sys.path[:1] == ['']: del sys.path[0]\n"
              "import runpy\n"
              "runpy._run_module_as_main(sys.argv.pop(1))\n")


def python_module_argv(module: str, args: Sequence[str] = (), *,
                       legacy: Optional[bool] = None) -> List[str]:
    """The argv that runs `python -m <module> ARGS` in a child process
    WITHOUT the current directory on sys.path. A plain `python -m`
    imports from the cwd first, so a json.py or csv.py planted in a
    project folder ran inside `taxjson run`'s child processes (2026-10
    security review H1). Python 3.11+: `-P`; older: the `-c` bootstrap
    above. Every taxjson child Python process is launched through here
    (`legacy` forces the bootstrap, for its test)."""
    if legacy is None:
        legacy = sys.version_info < (3, 11)
    if legacy:
        return [sys.executable, "-c", _SAFE_BOOT, module, *map(str, args)]
    return [sys.executable, "-P", "-m", module, *map(str, args)]


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
        if spec is not None:
            # Out of process through the console-script trampoline: the
            # same one-line errors, umask and pipe handling as
            # `taxjson-<tool>` (re-audit A2-0161).
            # Never with the cwd on sys.path (python_module_argv).
            cmd = python_module_argv(
                "taxjson.bin._entry",
                [spec[0].rsplit(".", 1)[-1], *spec[1]])
        # Captured output is for a program (a work/ file, a .diag, a
        # caller that parses it): never wrapped (lib/out.unwrapped).
        env = (dict(os.environ, TAXJSON_WIDTH="0")
               if (capture_output or stdout is not None) and not interactive
               else None)
        if stdout is not None:
            return subprocess.run(cmd, stdout=stdout,
                                  stderr=(None if interactive
                                          else subprocess.PIPE),
                                  stdin=(None if interactive
                                         else subprocess.DEVNULL),
                                  cwd=cwd, text=not interactive, env=env)
        return subprocess.run(cmd, capture_output=capture_output,
                              text=True, cwd=cwd, env=env)

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
            if out_target is not None or err_buf is not None:
                # Captured for a program, not shown to a person: never
                # wrapped (lib/out.unwrapped) — the .diag and work/
                # files keep whole lines whatever the terminal.
                from taxjson.lib.out import unwrapped
                stack.enter_context(unwrapped())
            if out_target is not None:
                stack.enter_context(contextlib.redirect_stdout(out_target))
            if err_buf is not None:
                stack.enter_context(contextlib.redirect_stderr(err_buf))
            try:
                # The tool's main under the console script's guard: an
                # unreadable input it reports in one line (exit 2) when
                # run as `taxjson-<tool>` is the same line here, not a
                # traceback (re-audit A2-0161).
                from taxjson.lib.cli_diag import console_prog, guard_main
                r = guard_main(console_prog(module_name))(module.main)()
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
            except (KeyboardInterrupt, BrokenPipeError):
                # A closed reader is the caller's to handle (a quiet
                # exit), never a tool crash with a traceback (A2-1417).
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
