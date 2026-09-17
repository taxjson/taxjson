#!/bin/bash
set -e
cd "$(dirname "$0")"

echo "Running unit tests for taxjson..."

# Prefer the project venv (set up by `./setup.sh`) — it has the
# package installed via `pip install -e .` so imports resolve
# correctly. Falling back to system `python3` fails with import
# errors on a fresh clone where the package isn't installed
# globally. CI sets `PYTHON` to override.
if [ -n "$PYTHON" ]; then
    PY="$PYTHON"
elif [ -x "venv/bin/python3" ]; then
    PY="venv/bin/python3"
else
    PY="python3"
fi

"$PY" -m unittest discover -s tests -p "test_*.py" -v
echo "All tests passed successfully!"
