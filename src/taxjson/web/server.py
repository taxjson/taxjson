"""Launch the local taxjson web UI. Lazy-imports FastAPI/uvicorn so the rest
of the toolkit works without the [web] extra installed."""
from __future__ import annotations

import sys


def serve(root=".", host: str = "127.0.0.1", port: int = 8765) -> int:
    try:
        import uvicorn  # noqa: F401
    except ModuleNotFoundError:
        print("taxjson serve needs the web extra: pip install -e '.[web]'",
              file=sys.stderr)
        return 1

    from .app import create_app
    from .context import ProjectContext

    ctx = ProjectContext.load(root)
    # The bound host must also be an accepted Host header (create_app's
    # TrustedHostMiddleware refuses everything else). Binding a
    # wildcard address is the explicit "expose on the network" opt-in
    # (warned below) — clients then arrive with Host: <the machine's
    # LAN name/IP>, never "0.0.0.0", so an allowlist of the bind
    # address rejected EVERY network request with 400 (REVIEW #17).
    if host in ("0.0.0.0", "::", "*"):
        app = create_app(ctx, allowed_hosts=["*"])
    else:
        app = create_app(ctx, allowed_hosts=[host])
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"warning: binding to {host} exposes your tax data on the "
              f"network. Prefer 127.0.0.1 (reach it remotely via a tunnel).",
              file=sys.stderr)
    print(f"taxjson serve → http://{host}:{port}   (project: {ctx.root})",
          file=sys.stderr)
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0
