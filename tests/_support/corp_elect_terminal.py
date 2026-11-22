"""Shared test helpers: IB spin-offs, election manifests and a terminal.

Shared from: test_fix_1029_r7b_elect
"""
import os
import subprocess
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
import _paths


REPO = _paths.REPO


def _env():
    env = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "src")]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    return env


def _project(d, books, ticker_map="", settings=""):
    """A Canadian project of taxable accounts, each with a book.tt."""
    root = Path(d) / "proj"
    for a, text in books.items():
        (root / "inputs" / a).mkdir(parents=True)
        (root / "inputs" / a / "book.tt").write_text(text)
    (root / "taxjson.toml").write_text(
        '[settings]\ncountry = "canada"\nyear = 2025\nprovince = "ON"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        'local_timezone = "America/Toronto"\n' + settings
        + "".join(f'[accounts.{a}]\ntype = "taxable"\n' for a in books))
    (root / "ticker.map").write_text(ticker_map)
    return root


_IB_HEADER = (
    "Statement,Header,Field Name,Field Value\n"
    "Statement,Data,BrokerName,Interactive Brokers\n"
    'Statement,Data,Period,"January 1, 2025 - December 31, 2025"\n'
    "Account Information,Header,Field Name,Field Value\n"
    "Account Information,Data,Account,U1234567\n"
    "Corporate Actions,Header,Asset Category,Currency,Report Date,"
    "Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,Code\n")


def _ib_spinoff(day, child):
    return (f'Corporate Actions,Data,Stocks,CAD,{day},"{day}, 10:00:00",'
            f'"PARENT(US0000000001) Spinoff 1 for 2 ({child}, SYNTHETIC '
            f'CHILD, US0000000002)",5,0,50,0,\n')


def _save(path, records):
    """manifest.json holding `records`: {id: (election, summary, hints)}."""
    from taxjson.lib import corp_actions as ca
    m = ca.Manifest()
    for eid, (election, summary, hints) in records.items():
        m.set(ca.ElectionRecord(event_id=eid, summary=summary,
                                election=election, hints=dict(hints)))
    m.save(path)


def _on_a_terminal(root, *args, answers=""):
    """The CLI with a pseudo-terminal as its stdin, `answers` typed
    ahead ("\x04" at a line start is an end of input)."""
    import pty
    import signal
    master, slave = pty.openpty()
    argv = python_module_argv("taxjson.bin.taxjson_run")
    z = subprocess.Popen(argv + ["-C", str(root), *args], stdin=slave,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, start_new_session=True, env=_env())
    os.close(slave)
    try:
        if answers:
            os.write(master, answers.encode())
        try:
            out, err = z.communicate(timeout=120)
        except subprocess.TimeoutExpired:
            os.killpg(z.pid, signal.SIGKILL)
            out, err = z.communicate(timeout=10)
            err += "\nTIMEOUT"
    finally:
        os.close(master)
    return z.returncode, out, err
