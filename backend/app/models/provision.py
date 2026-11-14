"""The provision form. Validated twice: shape and ranges always, allowed
values and configured limits when a catalog is passed as validation context."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator
from pydantic_core import PydanticCustomError

from .catalog import Catalog

# Lowercase DNS label. Capped at 50 so the "-<n>" suffix still fits in 63.
NAME_RE = re.compile(r"^[a-z]([a-z0-9-]{0,48}[a-z0-9])?$")
ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
KEYPAIR_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
GROUP_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
TAG_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,62}$")
TAG_VALUE_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,255}$")
MAX_TAGS = 10
MAX_USER_DATA_BYTES = 16 * 1024  # stays below CloudStack's default 32 KB limit once base64-encoded


class ProvisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=50)
    zone: str
    service_offering: str
    template: str
    network: str
    disk_size_gb: int = Field(ge=1, le=10_000)
    instance_count: int = Field(ge=1, le=100)
    keypair: str | None = None  # name of an existing SSH keypair, optional
    start_vm: bool = True  # False: create the instances but leave them stopped
    group: str | None = None  # instance group, for organising VMs in CloudStack
    tags: dict[str, str] = Field(default_factory=dict)
    # cloud-init or script, passed to the instance as user data. Often holds
    # secrets, so it is never logged and never returned by the API.
    user_data: str | None = Field(default=None, repr=False)
    # Optional extra volume per instance.
    data_disk_offering: str | None = None
    data_disk_size_gb: int | None = Field(default=None, ge=1, le=10_000)  # only for custom-size offerings

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME_RE.fullmatch(v):
            raise PydanticCustomError(
                "invalid_name",
                "Use lowercase letters, digits and hyphens; start with a letter, end with a letter or digit",
            )
        return v

    @field_validator("zone", "service_offering", "template", "network")
    @classmethod
    def _id(cls, v: str) -> str:
        if not ID_RE.fullmatch(v):
            raise PydanticCustomError("invalid_id", "Not a valid identifier")
        return v

    @field_validator("keypair", "group", "user_data", "data_disk_offering", mode="before")
    @classmethod
    def _empty_is_none(cls, v: Any) -> Any:
        return None if v == "" else v

    @field_validator("group")
    @classmethod
    def _group(cls, v: str | None) -> str | None:
        if v is not None and not GROUP_RE.fullmatch(v):
            raise PydanticCustomError("invalid_group", "Use letters, digits, dots, hyphens and underscores")
        return v

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > MAX_TAGS:
            raise PydanticCustomError("too_many_tags", "At most {n} tags", {"n": MAX_TAGS})
        for key, value in v.items():
            if not TAG_KEY_RE.fullmatch(key):
                raise PydanticCustomError("invalid_tag", "Invalid tag key: {key}", {"key": key[:70]})
            if not TAG_VALUE_RE.fullmatch(value):
                raise PydanticCustomError("invalid_tag", "Invalid value for tag {key}", {"key": key})
        return v

    @field_validator("user_data")
    @classmethod
    def _user_data(cls, v: str | None) -> str | None:
        if v is not None and len(v.encode()) > MAX_USER_DATA_BYTES:
            raise PydanticCustomError("user_data_too_large", "User data is limited to 16 KB")
        return v

    @field_validator("data_disk_offering")
    @classmethod
    def _disk_offering(cls, v: str | None) -> str | None:
        if v is not None and not ID_RE.fullmatch(v):
            raise PydanticCustomError("invalid_id", "Not a valid identifier")
        return v

    @field_validator("keypair")
    @classmethod
    def _keypair(cls, v: str | None) -> str | None:
        if v is not None and not KEYPAIR_RE.fullmatch(v):
            raise PydanticCustomError("invalid_keypair", "Not a valid keypair name")
        return v

    @model_validator(mode="after")
    def _against_catalog(self, info: ValidationInfo) -> "ProvisionRequest":
        catalog: Catalog | None = (info.context or {}).get("catalog")
        if catalog is None:
            return self

        def fail(field: str, msg: str) -> None:
            raise PydanticCustomError("not_allowed", "{msg}", {"field": field, "msg": msg})

        if self.zone not in {z.id for z in catalog.zones}:
            fail("zone", "unknown zone")
        if self.service_offering not in {o.id for o in catalog.service_offerings}:
            fail("service_offering", "unknown compute offering")
        template = next((t for t in catalog.templates if t.id == self.template), None)
        if template is None:
            fail("template", "unknown template")
        elif template.zone_id not in (None, self.zone):
            fail("template", "template is not available in this zone")
        network = next((n for n in catalog.networks if n.id == self.network), None)
        if network is None:
            fail("network", "unknown network")
        elif network.zone_id != self.zone:
            fail("network", "network is not in this zone")
        if self.keypair is not None and self.keypair not in catalog.keypairs:
            fail("keypair", "unknown SSH keypair")
        lim = catalog.limits
        if self.data_disk_offering is None:
            if self.data_disk_size_gb is not None:
                fail("data_disk_size_gb", "pick a data disk offering first")
        else:
            offering = next((o for o in catalog.disk_offerings if o.id == self.data_disk_offering), None)
            if offering is None:
                fail("data_disk_offering", "unknown disk offering")
            elif not offering.customized and self.data_disk_size_gb is not None:
                fail("data_disk_size_gb", "this offering has a fixed size")
            elif offering.customized and self.data_disk_size_gb is None:
                fail("data_disk_size_gb", "this offering needs a size")
            elif offering.customized and not 1 <= self.data_disk_size_gb <= lim.max_disk_gb:
                fail("data_disk_size_gb", f"must be between 1 and {lim.max_disk_gb} GB")
        if not lim.min_disk_gb <= self.disk_size_gb <= lim.max_disk_gb:
            fail("disk_size_gb", f"must be between {lim.min_disk_gb} and {lim.max_disk_gb} GB")
        if self.instance_count > lim.max_instances:
            fail("instance_count", f"at most {lim.max_instances} instances per request")
        return self

    def to_tfvars(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "zone": self.zone,
            "service_offering": self.service_offering,
            "template": self.template,
            "network_id": self.network,
            "root_disk_size_gb": self.disk_size_gb,
            "instance_count": self.instance_count,
            "keypair": self.keypair,
            "start_vm": self.start_vm,
            "group": self.group,
            "tags": self.tags,
            "user_data": self.user_data,
            "data_disk_offering": self.data_disk_offering,
            "data_disk_size_gb": self.data_disk_size_gb,
        }

    def instance_names(self) -> list[str]:
        if self.instance_count == 1:
            return [self.name]
        return [f"{self.name}-{i + 1}" for i in range(self.instance_count)]
