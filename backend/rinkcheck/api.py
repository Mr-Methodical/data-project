"""HTTP boundary for the local RinkCheck workbench."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from starlette.exceptions import HTTPException as StarletteHTTPException

from .demo import demo_pair
from .engine import RULE_VERSION
from .rules import RULES
from .store import Store, StoreError

ROOT = Path(__file__).resolve().parents[2]
MAX_BODY_BYTES = 3 * 1024 * 1024
DEMO_NAMES = {"opening": "Opening release · Synthetic demo",
              "followup": "Follow-up release · Synthetic demo",
              "clean": "Clean release · Synthetic demo"}
logger = logging.getLogger(__name__)
TrimmedName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Reviewer = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=8, max_length=1000)]
Variant = Literal["opening", "followup", "clean"]


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RunPayload(Payload):
    name: TrimmedName
    scorer_csv: str
    league_csv: str


class DemoPayload(Payload):
    variant: Variant = "opening"


class PreviewPayload(Payload):
    action: Literal["select", "exclude"]
    candidate_id: Annotated[str, StringConstraints(min_length=1, max_length=120)] | None = None


class ResolutionPayload(PreviewPayload):
    expected_version: Annotated[int, Field(ge=0, le=2147483647)]
    note: Note
    reviewer: Reviewer


class ReplayPayload(Payload):
    reviewer: Reviewer


class BodyLimitMiddleware:
    """Bound buffered request bytes even with chunked/no Content-Length input."""
    def __init__(self, app, limit: int):
        self.app = app
        self.limit = limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope["method"] not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        length = headers.get(b"content-length")
        try:
            if length is not None and int(length) > self.limit:
                await JSONResponse({"detail": "Request exceeds the 3 MiB limit."}, status_code=413)(scope, receive, send)
                return
        except ValueError:
            await JSONResponse({"detail": "Invalid Content-Length header."}, status_code=400)(scope, receive, send)
            return
        chunks = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self.limit:
                await JSONResponse({"detail": "Request exceeds the 3 MiB limit."}, status_code=413)(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        delivered = False

        async def replay_body():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay_body, send)


def create_app(db_path: str | Path | None = None, seed: bool | None = None) -> FastAPI:
    database = db_path or os.environ.get("RINKCHECK_DB_PATH", str(ROOT / "data" / "rinkcheck.sqlite3"))
    should_seed = seed if seed is not None else os.environ.get("RINKCHECK_SEED_DEMO", "true").lower() in {"1", "true", "yes"}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = Store(database)
        if should_seed and app.state.store.empty():
            scorer, league = demo_pair("opening")
            app.state.store.create_run(DEMO_NAMES["opening"], scorer, league, is_demo=True)
        yield

    app = FastAPI(title="RinkCheck API", version="1.0.0", lifespan=lifespan,
                  description="Local, single-operator hockey data reconciliation. Reviewer names are self-reported.")
    app.add_middleware(BodyLimitMiddleware, limit=MAX_BODY_BYTES)

    @app.exception_handler(Exception)
    async def unexpected_error(_: Request, exc: Exception):
        logger.error("Unhandled API error", exc_info=exc)
        return JSONResponse({"detail": "Unexpected server error. Check the local server log."}, status_code=500)

    @app.exception_handler(StoreError)
    async def store_error(_: Request, exc: StoreError):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        # Do not echo full source contents from Pydantic's error input field.
        issues = [f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}" for issue in exc.errors()]
        return JSONResponse({"detail": "; ".join(issues)}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        return JSONResponse({"detail": str(exc.detail)}, status_code=exc.status_code, headers=exc.headers)

    @app.middleware("http")
    async def response_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def store(request: Request) -> Store:
        return request.app.state.store

    @app.get("/api/health")
    def health():
        return {"status": "ok", "rule_version": RULE_VERSION}

    @app.get("/api/runs")
    def list_runs(request: Request):
        return {"runs": store(request).list_runs()}

    @app.post("/api/runs")
    def create_run(payload: RunPayload, request: Request):
        return store(request).create_run(payload.name, payload.scorer_csv, payload.league_csv)

    @app.post("/api/demo")
    def create_demo(payload: DemoPayload, request: Request):
        scorer, league = demo_pair(payload.variant)
        return store(request).create_run(DEMO_NAMES[payload.variant], scorer, league, is_demo=True)

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str, request: Request):
        return store(request).get_run(run_id)

    @app.post("/api/runs/{run_id}/cases/{case_id}/preview")
    def preview(run_id: str, case_id: str, payload: PreviewPayload, request: Request):
        return store(request).preview(run_id, case_id, payload.action, payload.candidate_id)

    @app.post("/api/runs/{run_id}/cases/{case_id}/resolve")
    def resolve(run_id: str, case_id: str, payload: ResolutionPayload, request: Request):
        return store(request).resolve(run_id, case_id, payload.action, payload.candidate_id,
                                      payload.expected_version, payload.note, payload.reviewer)

    @app.post("/api/runs/{run_id}/replay")
    def replay(run_id: str, payload: ReplayPayload, request: Request):
        return store(request).replay(run_id, payload.reviewer)

    @app.get("/api/runs/{run_id}/export")
    def export(run_id: str, request: Request):
        content, filename = store(request).export(run_id)
        return Response(content, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.get("/api/template")
    def template():
        return Response((ROOT / "fixtures" / "template.csv").read_bytes(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="rinkcheck-template.csv"'})

    @app.get("/api/demo-files/{variant}/{source}")
    def demo_file(variant: Variant, source: Literal["scorer", "league"]):
        scorer, league = demo_pair(variant)
        return Response(scorer if source == "scorer" else league, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{variant}_{source}.csv"'})

    @app.get("/api/rules")
    def rulebook():
        return {"rule_version": RULE_VERSION, "rules": RULES}

    frontend = Path(os.environ.get("RINKCHECK_FRONTEND_DIR", str(ROOT / "frontend" / "dist"))).resolve()
    if (frontend / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

    @app.get("/{path:path}")
    def frontend_route(path: str):
        if path == "api" or path.startswith("api/"):
            raise StarletteHTTPException(404, "API route not found.")
        if not (frontend / "index.html").is_file():
            if not path:
                return JSONResponse({"name": "RinkCheck", "detail": "Build frontend assets or run the Vite development server.", "api_docs": "/docs"})
            raise StarletteHTTPException(404, "Resource not found.")
        candidate = (frontend / path).resolve()
        if not candidate.is_relative_to(frontend):
            raise StarletteHTTPException(404, "Resource not found.")
        if candidate.is_file():
            return FileResponse(candidate)
        if Path(path).suffix:
            raise StarletteHTTPException(404, "Resource not found.")
        return FileResponse(frontend / "index.html")

    return app


app = create_app()
