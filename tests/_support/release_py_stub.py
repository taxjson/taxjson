"""Shared test helpers: a python stand-in for the installer.

Shared from: test_fix_misc_low
"""


_PY_STUB = """#!/usr/bin/env bash
# python stand-in for the installer test: no venv, no pip, no network.
case "$1" in
  -c) exit 0 ;;
  --version) echo "Python 3.12.0" ;;
  -m)
    case "$2" in
      venv) mkdir -p "$3/bin"; cp "$0" "$3/bin/python"
            printf '#!/bin/sh\\necho "taxjson 0.0.0"\\n' > "$3/bin/taxjson"
            chmod +x "$3/bin/taxjson"
            cp "$3/bin/taxjson" "$3/bin/tjs" ;;
    esac ;;
esac
exit 0
"""
