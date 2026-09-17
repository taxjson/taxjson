"""Local web UI for taxjson (see taxjson.web.server.serve / `taxjson serve`).

Split so the heavy/optional FastAPI dependency is isolated:
  - context.py, data.py : pure-Python (config + data access + what-if). No
    FastAPI import — importable and unit-testable without the [web] extra.
  - app.py, server.py   : FastAPI/uvicorn glue. Imported only when serving.
"""
