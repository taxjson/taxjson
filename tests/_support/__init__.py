"""What test modules share: helpers, fixture text and rows (PLAN.md
step 9 B1). A test module imports from here, never from another test
module (tests/test_test_layout.py).
"""

import _hermetic  # noqa: F401  (a synthetic HOME, offline: tests/_hermetic)
