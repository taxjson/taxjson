"""One home for the tomllib/tomli fallback import.

`tomllib` is stdlib from 3.11; pyproject guarantees `tomli` on older
interpreters, so `tomllib is None` is near-theoretical — but the two
soft-read consumers (taxjson_run's query wrappers, taxjson-export)
degrade gracefully on it, so the None convention is preserved. Callers
that hard-require TOML keep their own `if tomllib is None` guard.
"""
try:
    import tomllib                       # Python 3.11+
except ImportError:                      # pragma: no cover
    try:
        import tomli as tomllib          # type: ignore
    except ImportError:
        tomllib = None                   # type: ignore

__all__ = ["tomllib"]
