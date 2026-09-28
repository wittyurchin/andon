"""Application entry point.

One process: REST API plus the built frontend, so the whole system runs with a
single command. No microservices, no separate workers.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api.routes import router
from .config import get_settings
from .logging_setup import configure_logging
from .evidence.poller import EvidencePoller
from .service import SituationService

log = logging.getLogger(__name__)

# backend/andon/main.py -> repo root -> frontend/dist
DEFAULT_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def resolve_frontend_dist() -> Path:
    """Locate the built UI.

    The repo-relative default breaks as soon as the package is installed rather
    than run in place — an installed copy lives in site-packages, nowhere near
    ``frontend/`` — so an explicit ``ANDON_FRONTEND_DIST`` always wins.
    """
    configured = get_settings().frontend_dist
    return Path(configured).expanduser().resolve() if configured else DEFAULT_FRONTEND_DIST


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    log.info(
        "starting",
        extra={
            "version": __version__,
            "mode": settings.mode,
            "weather_providers": settings.resolved_weather_providers,
            "traffic_providers": settings.resolved_traffic_providers,
            "incident_providers": settings.resolved_incident_providers,
            "radar_providers": settings.resolved_radar_providers,
            "database": settings.database_path,
        },
    )
    if settings.history_file:
        log.warning("ANDON_HISTORY_FILE is deprecated; history now lives in the SQLite database")
    service = SituationService(settings)
    app.state.service = service
    poller = None
    if settings.background_polling:
        poller = EvidencePoller(service.evidence, settings.poll_tick_s)
        poller.start()
    try:
        yield
    finally:
        if poller is not None:
            await poller.stop()
        await service.aclose()
        log.info("stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    # Configured here rather than in the lifespan so that anything logged while
    # the app is being assembled still lands in the structured stream.
    configure_logging(settings.log_level, settings.log_json)

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        summary="What is happening around this kitchen right now, and what does it mean?",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    app.include_router(router)

    dist = resolve_frontend_dist()
    if dist.is_dir():
        log.info("serving frontend bundle", extra={"path": str(dist)})
        assets = dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/", include_in_schema=False)
        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str = ""):
            # An unmatched API path is a 404, not the SPA shell — otherwise a
            # typo'd endpoint returns HTML with a 200 and the client sees
            # "unexpected token '<'" instead of the real problem.
            if path.startswith("api/"):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            candidate = (dist / path).resolve()
            # Guard against `../` escaping the bundle directory.
            if path and candidate.is_file() and candidate.is_relative_to(dist):
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    else:

        @app.get("/", include_in_schema=False)
        async def no_frontend():
            return JSONResponse(
                {
                    "status": "api-only",
                    "detail": (
                        "Frontend bundle not found. Run `npm --prefix frontend install && "
                        "npm --prefix frontend run build`, or use the Vite dev server."
                    ),
                    "api_docs": "/docs",
                }
            )

    return app


app = create_app()
