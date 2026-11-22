"""The repository's folders, for a test module or a helper wherever it sits under tests/.

A test module that counts folders up from its own file
(`Path(__file__).resolve().parent.parent`) names the wrong folder once it
moves into a package below tests/ (PLAN.md step 9: tests/rules/<country>/
<family>/, tests/features/<area>/): a child process it starts with that
folder's src on PYTHONPATH would then import the venv's editable install,
not this tree, and pass on the wrong code. Test modules and helpers name
these instead; tests/test_test_layout.py refuses any use of a module's own
file (`__file__`, `__spec__`) below tests/ but here and in the shims."""
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent
SRC = REPO / "src"
EXAMPLES = REPO / "examples"
FIXTURES = TESTS / "fixtures"
