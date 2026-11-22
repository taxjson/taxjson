"""Shared test helpers: a run's sales with no purchase.

Shared from: test_fix_review2_journals
"""
import json

from tax_rules.dual import cli


_USA_CAD = {"source_currencies": ["CAD"]}


def _out(r):
    return " ".join((r.stdout + r.stderr).split())


def _run(test, root, *args, ok=True):
    r = cli(root, "run", "--no-input", *args)
    if ok:
        test.assertEqual(r.returncode, 0, _out(r)[-3000:])
    return r


def _sum(test, root):
    r = cli(root, "sum", "--json")
    test.assertEqual(r.returncode, 0, _out(r)[-2000:])
    return json.loads(r.stdout)


def _short_reported(test, root, sym, said=None):
    """Whether the run, `sum` (its sales with no purchase, in a total or
    not) and find-missing-history each report the sale of `sym` as one
    with no purchase."""
    r = _run(test, root)
    if said is not None:
        said.append(_out(r))
    s = _sum(test, root)
    m = cli(root, "find-missing-history")
    return ("no purchase in your files" in _out(r),
            any(sym in json.dumps(x)
                for k in ("no_purchase_uncovered", "no_purchase_in_totals")
                for x in s.get(k) or []),
            sym in m.stdout)
