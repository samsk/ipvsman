"""Tests for OpenAPI output and API contract."""

from __future__ import annotations

import json
import unittest

import yaml

from src.openapi import DOCUMENTED_PATH_KEYS, openapi_dict, openapi_json, openapi_yaml_text
from src.version import __version__


class OpenApiTest(unittest.TestCase):
    def test_yaml_roundtrip_matches_dict_canonical_json(self) -> None:
        as_dict = openapi_dict()
        as_yaml = yaml.safe_load(openapi_yaml_text())
        self.assertEqual(
            json.loads(json.dumps(as_dict, sort_keys=True)),
            json.loads(json.dumps(as_yaml, sort_keys=True)),
        )

    def test_documented_paths_match_dict_paths(self) -> None:
        self.assertEqual(set(openapi_dict()["paths"].keys()), DOCUMENTED_PATH_KEYS)

    def test_openapi_json_is_valid_utf8_json(self) -> None:
        raw = openapi_json()
        data = json.loads(raw.decode("utf-8"))
        self.assertEqual(data["openapi"], "3.0.3")
        self.assertEqual(data["info"]["version"], __version__)
        self.assertIn("paths", data)
        self.assertIn("components", data)

    def test_rate_limit_429_on_all_routed_operations(self) -> None:
        """Regression: every HTTP method must document 429 (per-IP limit)."""
        paths = openapi_dict()["paths"]
        for _path, ops in paths.items():
            for method, spec in ops.items():
                if method not in ("get", "post", "put", "patch", "delete"):
                    continue
                with self.subTest(path=_path, method=method):
                    responses = spec.get("responses", {})
                    self.assertIn(
                        "429",
                        responses,
                        msg=f"missing 429 on {_path} {method}",
                    )
                    ref = responses["429"]
                    self.assertIn("$ref", ref)
                    self.assertIn("RateLimited", ref["$ref"])

    def test_put_config_documents_request_body_and_errors(self) -> None:
        put = openapi_dict()["paths"]["/v1/config"]["put"]
        self.assertIn("requestBody", put)
        self.assertIn("422", put["responses"])
        self.assertIn("413", put["responses"])
        self.assertIn("400", put["responses"])

    def test_components_include_error_and_rate_schemas(self) -> None:
        comp = openapi_dict()["components"]
        self.assertIn("responses", comp)
        self.assertIn("RateLimited", comp["responses"])
        self.assertIn("Unauthorized", comp["responses"])
        self.assertIn("Error", comp["schemas"])
        self.assertIn("ApiConfigPutBody", comp["schemas"])


if __name__ == "__main__":
    unittest.main()
