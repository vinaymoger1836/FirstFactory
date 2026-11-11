import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from fl_api import __version__
from fl_api.db import dispose
from fl_api.routes import events, health, projects
from fl_api.settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    yield
    await app.state.redis.aclose()
    await dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    app = FastAPI(title="Factory Layout Platform API", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-Project-Token"],
        allow_credentials=False,  # no cookies (docs/11 §3)
    )
    app.include_router(health.router)
    app.include_router(projects.router)
    app.include_router(events.router)
    return app


app = create_app()


def run() -> None:
    uvicorn.run(
        "fl_api.main:app",
        host="0.0.0.0",  # noqa: S104 (inside the container; only Caddy reaches it)
        port=8000,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
