"""OpenAPI document (single source of truth for /openapi.json and /openapi.yaml)."""

from __future__ import annotations

import json
from typing import Any

import yaml

from src.version import __version__

# Paths documented here must match routes in api.ApiServer handlers.
DOCUMENTED_PATH_KEYS: frozenset[str] = frozenset(
    {
        "/v1/services",
        "/v1/frontends",
        "/v1/backends",
        "/v1/healthchecks",
        "/v1/status/detailed",
        "/v1/healthchecks/run",
        "/v1/config",
        "/metrics",
        "/openapi.json",
        "/openapi.yaml",
    }
)


def _responses_rate_auth() -> dict[str, Any]:
    """Common 401 + 429 for rate-limited authenticated routes."""
    return {
        "401": {"$ref": "#/components/responses/Unauthorized"},
        "429": {"$ref": "#/components/responses/RateLimited"},
    }


def openapi_dict() -> dict[str, Any]:
    """Return OpenAPI 3.0.3 document."""
    ja = "application/json"
    ra = _responses_rate_auth()
    err = {"$ref": "#/components/schemas/Error"}
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "ipvsman API",
            "version": __version__,
            "description": (
                "Control-plane HTTP API. When --api-token is set, use Authorization: Bearer <token>. "
                "All routes are subject to per-IP rate limiting (default 300 requests/minute per IP; 429 when exceeded). "
                "GET /metrics is only registered when Prometheus metrics are enabled on the API listener."
            ),
        },
        "paths": {
            "/v1/services": {
                "get": {
                    "summary": "List desired services",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {ja: {"schema": {"type": "object", "properties": {"services": {"type": "array"}}}}},
                        },
                        **ra,
                    },
                },
            },
            "/v1/frontends": {
                "get": {
                    "summary": "List frontends",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {ja: {"schema": {"type": "object", "properties": {"frontends": {"type": "array"}}}}},
                        },
                        **ra,
                    },
                },
            },
            "/v1/backends": {
                "get": {
                    "summary": "List backends",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {ja: {"schema": {"type": "object", "properties": {"backends": {"type": "array"}}}}},
                        },
                        **ra,
                    },
                },
            },
            "/v1/healthchecks": {
                "get": {
                    "summary": "List healthcheck configs",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {ja: {"schema": {"type": "object", "properties": {"healthchecks": {"type": "array"}}}}},
                        },
                        **ra,
                    },
                },
            },
            "/v1/status/detailed": {
                "get": {
                    "summary": "Desired vs live report",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {ja: {"schema": {"type": "object"}}},
                        },
                        **ra,
                    },
                },
            },
            "/v1/healthchecks/run": {
                "post": {
                    "summary": "Run all health checks once",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {ja: {"schema": {"$ref": "#/components/schemas/ManualCheckSummary"}}},
                        },
                        **ra,
                    },
                },
            },
            "/v1/config": {
                "put": {
                    "summary": "Replace api-put.yaml and reload desired state",
                    "requestBody": {
                        "required": True,
                        "content": {
                            ja: {
                                "schema": {"$ref": "#/components/schemas/ApiConfigPutBody"},
                            },
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Written and reloaded",
                            "content": {ja: {"schema": {"$ref": "#/components/schemas/ConfigPutResponse"}}},
                        },
                        **ra,
                        "400": {
                            "description": "Invalid Content-Length header",
                            "content": {ja: {"schema": err}},
                        },
                        "403": {
                            "description": "Write API disabled",
                            "content": {ja: {"schema": err}},
                        },
                        "413": {
                            "description": "Body larger than --api-max-body-bytes",
                            "content": {ja: {"schema": err}},
                        },
                        "422": {
                            "description": "JSON or Pydantic validation failed",
                            "content": {ja: {"schema": {"$ref": "#/components/schemas/ErrorDetail"}}},
                        },
                    },
                },
            },
            "/metrics": {
                "get": {
                    "summary": "Prometheus/OpenMetrics (only when enabled on API)",
                    "description": "Returns 503 text/plain if prometheus_client is not installed.",
                    "responses": {
                        "200": {
                            "description": "Metrics text (format by Accept header)",
                            "content": {
                                "text/plain": {"schema": {"type": "string"}},
                                "application/openmetrics-text": {"schema": {"type": "string"}},
                            },
                        },
                        **ra,
                        "503": {
                            "description": "prometheus_client missing",
                            "content": {"text/plain": {"schema": {"type": "string"}}},
                        },
                    },
                },
            },
            "/openapi.json": {
                "get": {
                    "summary": "OpenAPI document (JSON)",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {ja: {"schema": {"type": "object"}}},
                        },
                        **ra,
                    },
                },
            },
            "/openapi.yaml": {
                "get": {
                    "summary": "OpenAPI document (YAML)",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {"application/yaml": {"schema": {"type": "string"}}},
                        },
                        **ra,
                    },
                },
            },
        },
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "Used when the daemon is started with --api-token (or IPVSMAN_API_TOKEN).",
                },
            },
            "responses": {
                "Unauthorized": {
                    "description": "Missing or wrong Bearer token when token is configured",
                    "content": {ja: {"schema": err}},
                },
                "RateLimited": {
                    "description": "Per-IP sliding window exceeded",
                    "content": {ja: {"schema": err}},
                },
            },
            "schemas": {
                "Error": {
                    "type": "object",
                    "properties": {"error": {"type": "string"}},
                },
                "ErrorDetail": {
                    "type": "object",
                    "properties": {"error": {"type": "string"}, "detail": {"type": "string"}},
                },
                "ManualCheckSummary": {
                    "type": "object",
                    "properties": {
                        "total": {"type": "integer"},
                        "ok": {"type": "integer"},
                        "failed": {"type": "integer"},
                        "results": {"type": "array"},
                    },
                },
                "ApiConfigPutBody": {
                    "type": "object",
                    "required": ["groups"],
                    "properties": {
                        "groups": {
                            "type": "array",
                            "description": "Same shape as YAML group documents; validated with Pydantic Group models.",
                            "items": {"type": "object"},
                        },
                    },
                },
                "ConfigPutResponse": {
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                },
            },
        },
    }


def openapi_json() -> bytes:
    """Return OpenAPI JSON bytes."""
    return json.dumps(openapi_dict(), indent=2, sort_keys=True).encode("utf-8")


def openapi_yaml_text() -> str:
    """Return OpenAPI YAML text."""
    return yaml.safe_dump(openapi_dict(), sort_keys=True)
