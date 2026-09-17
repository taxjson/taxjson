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


def create_app(ctx: ProjectContext, allowed_hosts=None) -> FastAPI:
    # No Swagger UI: it loads assets from a third-party CDN in the
    # viewer's browser — the only external fetch a local-only tool
    # would make. The JSON schema stays at /openapi.json.
    app = FastAPI(title="taxjson", docs_url=None, redoc_url=None)
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
            except Exception:
                pass
        return st.ctx

    def page(name, request, **ctx_vars):
        # Starlette ≥0.29 signature: request first, then name, then context
        # (the old TemplateResponse(name, {"request": ...}) form is gone).
        return templates.TemplateResponse(
            request=request, name=name,
            context={"ctx": cur(), "accounts": cur().accounts,
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
        rows, error = [], None
        if account:
            try:
                rows = data.load_holdings(cur(), account)
            except (UnknownAccountError, ReportArtifactError) as e:
                error = str(e)
        return page("holdings.html", request, account=account,
                    rows=rows, error=error)

    @app.get("/holdings/{account}/{symbol}", response_class=HTMLResponse)
    def holding_detail(request: Request, account: str, symbol: str):
        try:
            holding = data.find_holding(cur(), account, symbol)
            error = None
        except (UnknownAccountError, ReportArtifactError) as e:
            holding, error = None, str(e)
        return page("holding_detail.html", request, account=account,
                    symbol=symbol, holding=holding, error=error)

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
        acct = account or (with_reports[0] if with_reports
                           else (candidates[0] if candidates else ""))
        sections, error = [], None
        if acct:
            try:
                sections = data.wash_radar_sections(cur(), acct)
            except (UnknownAccountError, ReportArtifactError) as e:
                error = str(e)
        return page("wash_radar.html", request, account=acct,
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
        return {"ok": True, "root": str(cur().root),
                "accounts": [a.name for a in cur().accounts]}

    return app
