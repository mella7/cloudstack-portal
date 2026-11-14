from __future__ import annotations

from fastapi import Request

from .config import Settings
from .jobs.manager import JobManager
from .services.catalog import CatalogService
from .services.cloudstack_api import CloudStackClient


def get_manager(request: Request) -> JobManager:
    return request.app.state.manager


def get_catalog(request: Request) -> CatalogService:
    return request.app.state.catalog


def get_cloudstack(request: Request) -> CloudStackClient:
    return request.app.state.cloudstack


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings
