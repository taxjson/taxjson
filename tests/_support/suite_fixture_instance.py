"""Shared test helpers: another module's fixture class, set up for a test.

Shared from: test_fix_1029_r7b_tooling
"""


def _fixture(case, cls, name):
    """An instance of another module's fixture class, set up for `case`
    (its own tests are not run again here)."""
    t = cls(name)
    t.setUp()
    case.addCleanup(t.tearDown)
    return t
