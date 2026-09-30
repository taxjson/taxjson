"""FastAPI app for the taxjson local UI. Imported only when serving, so the
core toolkit never requires FastAPI. Server-rendered (Jinja); a small HTMX
layer can be added later for partial updates without changing these routes."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import data
from .data import ReportArtifactError, UnknownAccountError
from .context import ProjectContext

_HERE = Path(__file__).parent

# Hosts a request may arrive as. Anything else is refused (400): a
# DNS-rebinding page in a browser on the same machine would otherwise be
# able to read /api/holdings off 127.0.0.1. "testserver" is Starlette's
# TestClient host.
_DEFAULT_ALLOWED_HOSTS = ("127.0.0.1", "localhost", "::1", "testserver")


AUTH_COOKIE = "taxjson_token"


def create_app(ctx: ProjectContext, allowed_hosts=None,
               auth_token: str = None) -> FastAPI:
    """`auth_token` (set by `taxjson serve` for any non-loopback bind):
    every request must carry it as `?token=` or the cookie that a
    first valid `?token=` sets — anyone else on the network gets 401."""
    # No Swagger UI: it loads assets from a third-party CDN in the
    # viewer's browser — the only external fetch a local-only tool
    # would make. The JSON schema stays at /openapi.json.
    app = FastAPI(title="taxjson", docs_url=None, redoc_url=None)
    if auth_token:
        import hmac

        @app.middleware("http")
        async def _require_token(request: Request, call_next):
            given = request.query_params.get("token")
            ok_query = given is not None and hmac.compare_digest(
                given.encode(), auth_token.encode())
            cookie = request.cookies.get(AUTH_COOKIE) or ""
            if not (ok_query or hmac.compare_digest(
                    cookie.encode(), auth_token.encode())):
                return JSONResponse(
                    {"ok": False, "reason": "missing or wrong access token "
                     "(open the URL `taxjson serve` printed)"},
                    status_code=401)
            response = await call_next(request)
            if ok_query:
                response.set_cookie(AUTH_COOKIE, auth_token, httponly=True,
                                    samesite="strict")
            return response
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=list(dict.fromkeys(
            (*_DEFAULT_ALLOWED_HOSTS, *(allowed_hosts or ())))))
    templates = Jinja2Templates(directory=str(_HERE / "templates"))
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")),
              name="static")
    app.state.ctx = ctx
    try:
        app.state._cfg_mtime = (ctx.root / "taxjson.toml").stat().st_mtime_ns
    except OSError:
        app.state._cfg_mtime = None

    def cur() -> ProjectContext:
        """The CURRENT project context — reloaded when taxjson.toml
        changes on disk. Routes used to close over the startup
        snapshot: an account added while serving was invisible, and
        the 404 even claimed it was "not in taxjson.toml" when it was
        (REVIEW #29). A reload failure keeps serving the last good
        snapshot."""
        st = app.state
        try:
            m = (st.ctx.root / "taxjson.toml").stat().st_mtime_ns
        except OSError:
            return st.ctx
        if m != st._cfg_mtime:
            try:
                st.ctx = ProjectContext.load(st.ctx.root)
                st._cfg_mtime = m
                st.cfg_error = None
            except Exception as exc:
                # Keep serving the last good snapshot — but SAY so: an
                # edit that `taxjson run` refuses (a mis-cased type, a
                # quoted boolean) used to be ignored in silence while
                # the pages kept the old account types (S078-15).
                st.cfg_error = (
                    f"taxjson.toml was edited but cannot be loaded "
                    f"({exc}) — these pages still use the last valid "
                    f"configuration. Fix taxjson.toml (`taxjson run` "
                    f"refuses it too).")
        return st.ctx

    def page(name, request, status_code: int = 200, **ctx_vars):
        # Starlette ≥0.29 signature: request first, then name, then context
        # (the old TemplateResponse(name, {"request": ...}) form is gone).
        return templates.TemplateResponse(
            request=request, name=name, status_code=status_code,
            context={"ctx": cur(), "accounts": cur().accounts,
                     "cfg_error": getattr(app.state, "cfg_error", None),
                     "holdings_accounts": data.holdings_accounts(cur()),
                     "freshness": data.freshness(cur()),
                     **ctx_vars})

    # --- pages ---------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        accts = data.holdings_accounts(cur())
        counts = {}
        errors = []
        for a in accts:
            # One corrupt holdings.toml must not 500 the whole dashboard.
            try:
                counts[a] = len(data.load_holdings(cur(), a))
            except ReportArtifactError as e:
                counts[a] = "?"
                errors.append(str(e))
        return page("dashboard.html", request, counts=counts, errors=errors)

    @app.get("/holdings", response_class=HTMLResponse)
    def holdings(request: Request, account: str = ""):
        accts = data.holdings_accounts(cur())
        account = account or (accts[0] if accts else "")
        rows, error, status = [], None, 200
        if account:
            try:
                rows = data.load_holdings(cur(), account)
            except UnknownAccountError as e:
                # An error page for a name that doesn't exist is a 404,
                # not a 200 (scripts/link checkers read the status).
                error, status = str(e), 404
            except ReportArtifactError as e:
                error = str(e)
        return page("holdings.html", request, status_code=status,
                    account=account, rows=rows, error=error)

    @app.get("/holdings/{account}/{symbol}", response_class=HTMLResponse)
    def holding_detail(request: Request, account: str, symbol: str):
        status = 200
        try:
            holding = data.find_holding(cur(), account, symbol)
            error = None
        except UnknownAccountError as e:
            holding, error, status = None, str(e), 404
        except ReportArtifactError as e:
            holding, error = None, str(e)
        from taxjson.lib.core import is_option_symbol
        return page("holding_detail.html", request, status_code=status,
                    account=account, symbol=symbol, holding=holding,
                    error=error, is_option=is_option_symbol(symbol))

    @app.get("/wash-radar", response_class=HTMLResponse)
    def wash_radar(request: Request, account: str = ""):
        # Radar reports exist per wash-checkable taxable account: equity
        # always; crypto only when the jurisdiction covers it (the
        # pipeline writes a crypto radar for Canada — s.54 identical
        # property — but not for US, where §1091 doesn't reach digital
        # assets). A crypto account is offered whenever its report
        # exists, so this stays country-agnostic. Default to the first
        # account with a report and honor ?account= for the others;
        # previously hardwired to taxable()[0], which broke
        # multi-taxable projects and showed "no report" when the first
        # taxable was crypto.
        candidates = [
            a.name for a in cur().taxable()
            if not getattr(a, "crypto", False)
            or (cur().reports / f"wash_radar_{a.name}.rpt").exists()]
        # The cross-account view the what-if page points at — a
        # pseudo-account, not in taxjson.toml (2026-09 audit: the
        # link 404'd into "no account 'COMBINED'").
        if (cur().reports / "wash_radar_COMBINED.rpt").exists():
            candidates.append("COMBINED")
        with_reports = [a for a in candidates
                        if (cur().reports / f"wash_radar_{a}.rpt").exists()]
        # The COMBINED radar (written only when there are two or more
        # taxable accounts) is the default: a per-account report sees
        # only its own book, so it said "CLEAR — safe to sell at a
        # loss" while a sibling account's recent buy made the loss
        # superficial (R1-229).
        default = ("COMBINED" if "COMBINED" in with_reports
                   else (with_reports[0] if with_reports
                         else (candidates[0] if candidates else "")))
        acct = account or default
        scope_note = None
        if acct and acct != "COMBINED" and "COMBINED" in with_reports:
            scope_note = (
                f"This is {acct}'s own book only. A buy in another "
                f"taxable account inside the 30-day window also makes a "
                f"loss here superficial (the blended pass denies it) — "
                f"the COMBINED view has the cross-account verdicts.")
        sections, error, status = [], None, 200
        if acct:
            try:
                sections = data.wash_radar_sections(cur(), acct)
            except UnknownAccountError as e:
                error, status = str(e), 404
            except ReportArtifactError as e:
                error = str(e)
        stale = data.radar_staleness(cur(), acct) if acct else None
        return page("wash_radar.html", request, status_code=status,
                    errors=[stale] if stale else [],
                    account=acct, scope_note=scope_note,
                    radar_accounts=(with_reports or candidates),
                    sections=sections, error=error)

    @app.post("/whatif", response_class=HTMLResponse)
    def whatif(request: Request, account: str = Form(...),
               symbol: str = Form(...), qty: float = Form(...),
               price: float = Form(...), price_currency: str = Form(None)):
        try:
            result = data.what_if_sell(cur(), account, symbol, qty, price,
                                       price_currency=price_currency)
        except Exception as e:  # surface, don't 500
            result = {"ok": False, "reason": str(e)}
        return page("_whatif_result.html", request, result=result)

    # --- JSON API (for tooling / a future SPA) -------------------------
    @app.get("/api/holdings")
    def api_holdings(account: str):
        try:
            return data.load_holdings(cur(), account)
        except UnknownAccountError as e:
            return JSONResponse({"ok": False, "reason": str(e)},
                                status_code=404)
        except ReportArtifactError as e:
            # Deliberate JSON error body (not an unhandled traceback).
            return JSONResponse({"ok": False, "reason": str(e)},
                                status_code=500)

    @app.get("/api/whatif")
    def api_whatif(account: str, symbol: str, qty: float, price: float,
                   price_currency: str = None):
        try:
            return data.what_if_sell(cur(), account, symbol, qty, price,
                                     price_currency=price_currency)
        except Exception as e:  # surface as JSON, don't 500 (fresh project
            return {"ok": False, "reason": str(e)}  # has no _base.json yet)

    @app.get("/healthz")
    def healthz():
        # No filesystem path: it names the user (home dir) and the
        # project, and a health probe has no need for either.
        return {"ok": True,
                "accounts": [a.name for a in cur().accounts]}

    return app
