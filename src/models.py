"""Pydantic models for config and API."""

from __future__ import annotations

from typing import Any, Literal

try:
    from pydantic import BaseModel, ConfigDict, Field, field_validator
except ModuleNotFoundError:  # pragma: no cover - fallback for minimal envs
    class BaseModel:  # type: ignore[override]
        """Very small compatibility shim when pydantic is unavailable."""

        def __init__(self, **kwargs: Any) -> None:
            for key, value in kwargs.items():
                setattr(self, key, value)

        @classmethod
        def model_validate(cls, data: Any) -> "BaseModel":
            if isinstance(data, cls):
                return data
            return cls(**dict(data))

        def model_dump(self) -> dict[str, Any]:
            return dict(self.__dict__)

    def ConfigDict(**_kwargs: Any) -> dict[str, Any]:
        return {}

    def Field(default: Any = None, **kwargs: Any) -> Any:
        if "default_factory" in kwargs and default is None:
            return kwargs["default_factory"]()
        return default

    def field_validator(*_args: Any, **_kwargs: Any):
        def _wrap(func: Any) -> Any:
            return func

        return _wrap


class CheckTarget(BaseModel):
    """Backend probe target override."""

    model_config = ConfigDict(extra="forbid")

    ip: str
    port: int = Field(ge=1, le=65535)
    type: Literal["tcp", "http", "https", "dns"] = "tcp"
    path: str | None = None
    host: str | None = None
    query_name: str | None = None
    query_type: str | None = None
    timeout: float | None = Field(default=None, gt=0)


class HealthCheck(BaseModel):
    """Group healthcheck settings."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["tcp", "http", "https", "dns"]
    interval: float = Field(default=10.0, gt=0)
    timeout: float = Field(default=3.0, gt=0)
    rise: int = Field(default=2, ge=1)
    fall: int = Field(default=3, ge=1)
    path: str | None = None
    host: str | None = None
    expected_status: int = Field(default=200, ge=100, le=599)
    query_name: str | None = None
    query_type: str | None = None
    disable: bool = False


class Backend(BaseModel):
    """Backend endpoint definition."""

    model_config = ConfigDict(extra="forbid")

    ip: str
    weight: int = Field(default=1, ge=0, le=65535)
    port_map: dict[str, int] = Field(default_factory=dict)
    check_target: CheckTarget | None = None
    check_ref: str | None = None
    disabled: bool = False
    method: Literal["routing", "nat"] = "routing"
    proxy_method: Literal["routing", "nat"] | None = None

    @field_validator("port_map", mode="before")
    @classmethod
    def validate_port_map(cls, value: Any) -> dict[str, int]:
        """Validate backend port map keys and values.

        Input:
        - value: Mapping or list of one-key mappings.

        Output:
        - Normalized dict[str, int] for runtime resolution.
        """
        if value is None:
            return {}
        if isinstance(value, list):
            merged: dict[str, int] = {}
            for item in value:
                if not isinstance(item, dict):
                    raise ValueError("port_map list items must be mappings")
                for key, port in item.items():
                    merged[str(key)] = int(port)
            value = merged
        if not isinstance(value, dict):
            raise ValueError("port_map must be mapping or list of mappings")
        for key, port in value.items():
            if key != "*" and not key:
                raise ValueError("port_map key cannot be empty")
            if not (1 <= int(port) <= 65535):
                raise ValueError("port_map value out of range")
        return value


class Frontend(BaseModel):
    """Frontend virtual service."""

    model_config = ConfigDict(extra="forbid")

    name: str
    proto: Literal["tcp", "udp"]
    port: int | str
    vip: str | list[str] | None = None
    scheduler: str | None = None
    disabled: bool = False


class Group(BaseModel):
    """Top-level service group."""

    model_config = ConfigDict(extra="forbid")

    group: str
    vip: str | list[str] | None = None
    scheduler: str | None = None
    frontends: list[Frontend]
    backends: list[Backend] = Field(default_factory=list)
    backend_files: list[str] = Field(default_factory=list)
    backend_map_ref: str | None = None
    healthcheck: HealthCheck
    disabled: bool = False


class RuntimeCheckResult(BaseModel):
    """Health cache line."""

    model_config = ConfigDict(extra="forbid")

    state: int
    ready: bool
    fail_count: int
    success_count: int
    changed_at: float
    updated_at: float
    message: str | None = None


class ApiConfigPut(BaseModel):
    """PUT payload model."""

    model_config = ConfigDict(extra="forbid")

    groups: list[Group]
