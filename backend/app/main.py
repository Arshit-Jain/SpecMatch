import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.logging import configure_logging, log_event
from app.routers import console, health, matches, records
from app.services.ingest import run_ingest
from app.services.matching.engine import run_matching

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    run_ingest()
    run_matching()
    log_event(logger, logging.INFO, "app_started")
    yield


app = FastAPI(title="SpecMatch", lifespan=lifespan)

app.include_router(health.router)
app.include_router(records.router)
app.include_router(matches.router)
app.include_router(console.router)
