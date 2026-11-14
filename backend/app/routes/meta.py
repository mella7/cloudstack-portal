from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..config import Settings
from ..deps import get_app_settings, get_catalog
from ..models.catalog import Catalog
from ..services.catalog import CatalogService
from ..services.cloudstack_api import CloudStackError

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/health")
async def health(settings: Settings = Depends(get_app_settings)) -> dict[str, object]:
    return {"status": "ok", "dry_run": settings.dry_run, "plan_ttl_minutes": settings.plan_ttl_minutes}


@router.get("/catalog", response_model=Catalog)
async def catalog(service: CatalogService = Depends(get_catalog)) -> Catalog:
    try:
        return await service.get()
    except CloudStackError as exc:
        raise HTTPException(502, f"CloudStack: {exc}") from None
