# Derive the project root from this script's own location — the old
# hardcoded $HOME/taxjson-ai silently skipped activation (and still
# printed success) for any other clone path. Works under `source` in
# bash and zsh.
if [ -n "${BASH_SOURCE:-}" ]; then
    _taxjson_self="${BASH_SOURCE[0]}"
else
    _taxjson_self="${(%):-%N}"      # zsh
fi
export TAXJSON_ROOT="$(cd "$(dirname "$_taxjson_self")" && pwd)"
unset _taxjson_self

if [ -d "$TAXJSON_ROOT/venv" ]; then
    source "$TAXJSON_ROOT/venv/bin/activate"
    export PATH="$TAXJSON_ROOT/venv/bin:$PATH"
    # No PYTHONPATH needed: `pip install -e .` makes `taxjson` importable
    # from the venv. (Under the src/ layout the package lives in
    # src/taxjson, so a bare PYTHONPATH=$TAXJSON_ROOT would no longer
    # find it anyway.)
    echo "✅ taxjson environment activated ($TAXJSON_ROOT)."
else
    echo "❌ taxjson: no venv at $TAXJSON_ROOT/venv — run scripts/dev-setup.sh first." >&2
    return 1 2>/dev/null || exit 1
fi
