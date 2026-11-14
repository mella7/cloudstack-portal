from __future__ import annotations

from pydantic import BaseModel


class Zone(BaseModel):
    id: str
    name: str


class ServiceOffering(BaseModel):
    id: str
    name: str
    cpu_number: int | None = None
    memory_mb: int | None = None


class Template(BaseModel):
    id: str
    name: str
    zone_id: str | None = None  # None means available in every zone


class Network(BaseModel):
    id: str
    name: str
    zone_id: str


class DiskOffering(BaseModel):
    id: str
    name: str
    size_gb: int | None = None  # None for custom-size offerings
    customized: bool = False  # True: the size is chosen per disk


class Limits(BaseModel):
    min_disk_gb: int
    max_disk_gb: int
    max_instances: int


class Catalog(BaseModel):
    zones: list[Zone]
    service_offerings: list[ServiceOffering]
    templates: list[Template]
    networks: list[Network]
    keypairs: list[str] = []
    disk_offerings: list[DiskOffering] = []
    limits: Limits
