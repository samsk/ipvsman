"""Tests for CLI config parsing."""

from __future__ import annotations

import unittest
import os
from unittest.mock import patch

from src.config import has_cli_action, parse_config
from src.config import build_parser


class ConfigTest(unittest.TestCase):
    def test_api_disabled_by_default(self) -> None:
        cfg = parse_config([])
        self.assertFalse(cfg.api_enable)

    def test_api_enabled_only_with_flag(self) -> None:
        cfg = parse_config(["--api-enable"])
        self.assertTrue(cfg.api_enable)

    def test_version_flag(self) -> None:
        cfg = parse_config(["--version"])
        self.assertTrue(cfg.version)

    def test_service_flag(self) -> None:
        cfg = parse_config(["--service"])
        self.assertTrue(cfg.service_mode)
        self.assertFalse(has_cli_action(cfg))

    def test_has_cli_action_list_services(self) -> None:
        cfg = parse_config(["--list-services"])
        self.assertTrue(has_cli_action(cfg))
        self.assertEqual(cfg.list_mask, 1)

    def test_list_mask_merged_from_multiple_flags(self) -> None:
        cfg = parse_config(["--list-services", "--list-frontends", "--list-backends", "--list-healthchecks"])
        self.assertEqual(cfg.list_mask, 15)

    def test_status_short_flag(self) -> None:
        cfg = parse_config(["-s"])
        self.assertTrue(cfg.status_mode)
        self.assertTrue(has_cli_action(cfg))

    def test_enable_runtime_toggles(self) -> None:
        cfg = parse_config(
            [
                "--enable-group",
                "g1",
                "--enable-frontend",
                "g1/f1",
                "--enable-backend",
                "g1/10.0.0.1",
            ]
        )
        self.assertEqual(cfg.enable_group, "g1")
        self.assertEqual(cfg.enable_frontend, "g1/f1")
        self.assertEqual(cfg.enable_backend, "g1/10.0.0.1")
        self.assertTrue(has_cli_action(cfg))

    def test_disable_runtime_toggles_are_cli_action(self) -> None:
        cfg = parse_config(["--disable-group", "g1"])
        self.assertTrue(has_cli_action(cfg))

    def test_reload_interval_zero_allowed(self) -> None:
        cfg = parse_config(["--reload-interval", "0"])
        self.assertEqual(cfg.reload_interval, 0.0)

    def test_api_token_from_env(self) -> None:
        with patch.dict(os.environ, {"IPVSMAN_API_TOKEN": "env-secret"}, clear=False):
            cfg = parse_config([])
            self.assertEqual(cfg.api_token, "env-secret")

    def test_cli_api_token_overrides_env(self) -> None:
        with patch.dict(os.environ, {"IPVSMAN_API_TOKEN": "env-secret"}, clear=False):
            cfg = parse_config(["--api-token", "cli-secret"])
            self.assertEqual(cfg.api_token, "cli-secret")

    def test_help_contains_structured_sections(self) -> None:
        help_text = build_parser().format_help()
        self.assertIn("Core runtime:", help_text)
        self.assertIn("One-shot actions:", help_text)
        self.assertIn("Output and filters:", help_text)
        self.assertIn("API and metrics:", help_text)
        self.assertIn("Examples:", help_text)


if __name__ == "__main__":
    unittest.main()
