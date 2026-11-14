"""Allowed values for the form, fetched through the CloudStack client and cached."""

from __future__ import annotations

import asyncio
import time

from ..config import Settings
from ..models.catalog import Catalog, Limits
from .cloudstack_api import CloudStackClient


class CatalogService:
    def __init__(self, client: CloudStackClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self._cached: Catalog | None = None
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> Catalog:
        async with self._lock:
            fresh = time.monotonic() - self._fetched_at < self._settings.catalog_ttl_seconds
            if self._cached is None or not fresh:
                zones, offerings, templates, networks, keypairs, disk_offerings = await asyncio.gather(
                    self._client.list_zones(),
                    self._client.list_service_offerings(),
                    self._client.list_templates(),
                    self._client.list_networks(),
                    self._client.list_keypairs(),
                    self._client.list_disk_offerings(),
                )
                self._cached = Catalog(
                    zones=zones,
                    service_offerings=offerings,
                    templates=templates,
                    networks=networks,
                    keypairs=keypairs,
                    disk_offerings=disk_offerings,
                    limits=Limits(
                        min_disk_gb=self._settings.min_disk_gb,
                        max_disk_gb=self._settings.max_disk_gb,
                        max_instances=self._settings.max_instances,
                    ),
                )
                self._fetched_at = time.monotonic()
            return self._cached
