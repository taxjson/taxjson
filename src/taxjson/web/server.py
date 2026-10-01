"""Launch the local taxjson web UI. Lazy-imports FastAPI/uvicorn so the rest
of the toolkit works without the [web] extra installed."""
from __future__ import annotations

import ipaddress
import secrets
import sys


def is_loopback(host: str) -> bool:
    if host.strip("[]").lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def serve(root=".", host: str = "127.0.0.1", port: int = 8765,
          require_token: bool = False) -> int:
    """`require_token` (`taxjson serve --token`) issues the per-run token on
    a loopback bind too: 127.0.0.1 is reachable by every account on the
    machine, so on a shared host another user could read the books
    through the server that the 0600 files deny them (R1-346)."""
    try:
        import uvicorn  # noqa: F401
    except ModuleNotFoundError:
        print("taxjson serve needs the web extra: pip install -e '.[web]'",
              file=sys.stderr)
        return 1

    from .app import create_app
    from .context import ProjectContext

    if not 1 <= int(port) <= 65535:
        # Port 0 let the OS pick one, but the banner then advertised
        # http://host:0 — an address nobody can open.
        print(f"taxjson serve: --port must be 1-65535, got {port}",
              file=sys.stderr)
        return 2
    from taxjson.lib.tomlcompat import tomllib
    try:
        ctx = ProjectContext.load(root)
    except FileNotFoundError as e:
        print(f"taxjson serve: {e}", file=sys.stderr)
        return 1
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        # Same wording as every other command (load_config) instead of
        # a raw tomllib traceback — a non-UTF-8 file too (S079-09).
        from pathlib import Path
        print(f"taxjson serve: {Path(root).resolve() / 'taxjson.toml'} "
              f"is not valid TOML: {e}", file=sys.stderr)
        return 1
    except (ValueError, OSError) as e:
        # A config `taxjson run` refuses (account type/name, settings)
        # or an unreadable file: one line, like run (S079-09).
        print(f"taxjson serve: {e}", file=sys.stderr)
        return 1
    # The bound host must also be an accepted Host header (create_app's
    # TrustedHostMiddleware refuses everything else). Binding a
    # wildcard address is the explicit "expose on the network" opt-in
    # (warned below) — clients then arrive with Host: <the machine's
    # LAN name/IP>, never "0.0.0.0", so an allowlist of the bind
    # address rejected EVERY network request with 400 (REVIEW #17).
    # Any non-loopback bind also requires a per-run random token (URL
    # ?token= once, then an HttpOnly cookie): without it anyone on the
    # network could read the books (2026-09 security audit).
    token = (secrets.token_urlsafe(24)
             if require_token or not is_loopback(host) else None)
    if host in ("0.0.0.0", "::", "*"):
        app = create_app(ctx, allowed_hosts=["*"], auth_token=token)
    else:
        app = create_app(ctx, allowed_hosts=[host], auth_token=token)
    if token and is_loopback(host):
        print(f"taxjson serve → http://{host}:{port}/?token={token}   "
              f"(project: {ctx.root}; token required)", file=sys.stderr)
    elif token:
        print(f"warning: binding to {host} exposes your tax data on the "
              f"network (plain HTTP, token-protected). Prefer 127.0.0.1 "
              f"and reach it remotely via an SSH tunnel.",
              file=sys.stderr)
        print(f"taxjson serve → http://{host}:{port}/?token={token}   "
              f"(project: {ctx.root})", file=sys.stderr)
    else:
        print(f"taxjson serve → http://{host}:{port}   (project: {ctx.root})",
              file=sys.stderr)
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0
