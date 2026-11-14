from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import Settings, get_settings
from .jobs.broker import Broker
from .jobs.manager import JobManager
from .jobs.store import JobStore
from .routes import jobs, meta, vms
from .services.catalog import CatalogService
from .services.cloudstack_api import CloudStackClient, FakeCloudStackClient, SignedCloudStackClient
from .services.masking import Masker
from .services.terraform import FakeTerraform, RealTerraform, TerraformRunner

log = logging.getLogger("portal")


def build_backends(settings: Settings) -> tuple[TerraformRunner, CloudStackClient]:
    """The one place that decides between real and fake implementations."""
    if settings.dry_run:
        cloudstack = FakeCloudStackClient()
        return FakeTerraform(cloudstack, settings.fake_step_delay_seconds), cloudstack
    settings.check_live_config()
    return RealTerraform(settings), SignedCloudStackClient(
        settings.cloudstack_api_url,
        settings.cloudstack_api_key.get_secret_value(),
        settings.cloudstack_secret_key.get_secret_value(),
        action_timeout=settings.vm_action_timeout_seconds,
    )


def create_app(
    settings: Settings | None = None,
    terraform: TerraformRunner | None = None,
    cloudstack: CloudStackClient | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    if terraform is None or cloudstack is None:
        default_tf, default_cs = build_backends(settings)
        terraform, cloudstack = terraform or default_tf, cloudstack or default_cs

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        for d in (settings.data_dir, settings.workdirs_dir, settings.state_dir):
            d.mkdir(parents=True, exist_ok=True)
        store = JobStore(settings.db_path)
        manager = JobManager(store, Broker(), terraform, settings, Masker(settings.secrets()))
        app.state.manager = manager
        interrupted = await manager.recover_interrupted()
        if interrupted:
            log.warning("marked %d interrupted job(s) as failed", interrupted)
        log.info("starting with DRY_RUN=%s", settings.dry_run)
        cleaner = asyncio.create_task(manager.cleanup_forever())
        try:
            yield
        finally:
            cleaner.cancel()
            await manager.shutdown()
            close = getattr(cloudstack, "aclose", None)
            if close:
                await close()
            store.close()

    app = FastAPI(title="CloudStack provisioning portal", lifespan=lifespan)
    app.state.settings = settings
    app.state.cloudstack = cloudstack
    app.state.catalog = CatalogService(cloudstack, settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        detail = []
        for err in exc.errors():
            loc = [str(p) for p in err["loc"] if p not in ("body", "path", "query")]
            detail.append({"field": loc[-1] if loc else "form", "message": err["msg"]})
        return JSONResponse({"detail": detail}, status_code=422)

    app.include_router(meta.router)
    app.include_router(jobs.router)
    app.include_router(vms.router)
    return app


def app_factory() -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    return create_app()
