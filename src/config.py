"""CLI config parsing."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

from src import constants


def _list_mask_from_cli(
    list_services: bool,
    list_frontends: bool,
    list_backends: bool,
    list_healthchecks: bool,
) -> int | None:
    """Merge --list-* flags into a single bitmask for listing."""
    m = 0
    if list_services:
        m |= constants.LIST_MASK_SERVICES
    if list_frontends:
        m |= constants.LIST_MASK_FRONTENDS
    if list_backends:
        m |= constants.LIST_MASK_BACKENDS
    if list_healthchecks:
        m |= constants.LIST_MASK_HEALTHCHECKS
    return m if m else None


@dataclass
class Config:
    """Runtime configuration."""

    version: bool
    config_dir: Path
    interval: float
    reload_interval: float
    stats_interval: float
    lock_file: Path
    check_workers: int
    shutdown_timeout: float
    stale_grace_sec: float
    cold_start_sec: float
    no_syslog: bool
    log_level: str
    debug: bool
    prometheus_metrics: bool
    prometheus_metrics_stats: bool
    prometheus_metrics_healthchecks: bool
    prometheus_metrics_stats_labels: str
    prometheus_host: str
    prometheus_port: int
    api_enable: bool
    api_host: str
    api_port: int
    api_enable_write: bool
    api_token: str | None
    api_max_body_bytes: int
    clear_on_exit: bool
    startup_full_replace: bool
    test_mode: bool
    status_mode: bool
    stats_mode: bool
    status_detailed: bool
    reset_mode: bool
    dump_mode: bool
    reload_mode: bool
    pid_hint: int | None
    healthcheck_now: bool
    healthcheck_now_group: str | None
    healthcheck_now_backend: str | None
    list_mask: int | None
    show_counters: bool
    show_rates: float | None
    only_active: bool
    filter_group: str | None
    filter_frontend: str | None
    filter_backend: str | None
    output: str
    watch: float | None
    no_color: bool
    disable_group: str | None
    disable_frontend: str | None
    disable_backend: str | None
    enable_group: str | None
    enable_frontend: str | None
    enable_backend: str | None
    service_mode: bool


def has_cli_action(cfg: Config) -> bool:
    """Return True if cfg selects a one-shot CLI action (not long-lived service)."""
    if cfg.test_mode:
        return True
    if cfg.list_mask is not None:
        return True
    if cfg.healthcheck_now or cfg.healthcheck_now_group or cfg.healthcheck_now_backend:
        return True
    if cfg.status_mode or cfg.status_detailed:
        return True
    if cfg.stats_mode:
        return True
    if cfg.reset_mode:
        return True
    if cfg.dump_mode:
        return True
    if cfg.reload_mode:
        return True
    if cfg.disable_group or cfg.disable_frontend or cfg.disable_backend:
        return True
    if cfg.enable_group or cfg.enable_frontend or cfg.enable_backend:
        return True
    return False


def build_parser() -> argparse.ArgumentParser:
    """Build command line parser."""
    p = argparse.ArgumentParser(
        prog=constants.SCRIPT_NAME,
        description="Manage Linux IPVS services from YAML config.",
        epilog=(
            "Examples:\n"
            f"  {constants.SCRIPT_NAME} --test\n"
            f"  {constants.SCRIPT_NAME} -s\n"
            f"  {constants.SCRIPT_NAME} --stats\n"
            f"  {constants.SCRIPT_NAME} --status-detailed --show-counters\n"
            f"  {constants.SCRIPT_NAME} --service"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    core = p.add_argument_group("Core runtime")
    core.add_argument("--version", action="store_true", help="Print build version and exit")
    core.add_argument(
        "--config-dir",
        type=Path,
        default=Path(constants.DEFAULT_CONFIG_DIR),
        help=f"Config root directory (default: {constants.DEFAULT_CONFIG_DIR})",
    )
    core.add_argument("--interval", type=float, default=constants.DEFAULT_TICK_INTERVAL, help=f"Main loop tick seconds (default: {constants.DEFAULT_TICK_INTERVAL})")
    core.add_argument(
        "--reload-interval",
        type=float,
        default=constants.DEFAULT_RELOAD_INTERVAL,
        help=f"Config reload trigger seconds (default: {constants.DEFAULT_RELOAD_INTERVAL})",
    )
    core.add_argument(
        "--stats-interval",
        type=float,
        default=constants.DEFAULT_STATS_INTERVAL,
        help=f"Live IPVS poll seconds (default: {constants.DEFAULT_STATS_INTERVAL})",
    )
    core.add_argument("--lock-file", type=Path, default=Path(constants.DEFAULT_LOCK_FILE), help=f"Single-instance lock file (default: {constants.DEFAULT_LOCK_FILE})")
    core.add_argument("--check-workers", type=int, default=constants.DEFAULT_CHECK_WORKERS, help=f"Healthcheck worker count (default: {constants.DEFAULT_CHECK_WORKERS})")
    core.add_argument(
        "--shutdown-timeout",
        type=float,
        default=constants.DEFAULT_SHUTDOWN_TIMEOUT,
        help=f"Worker shutdown timeout seconds (default: {constants.DEFAULT_SHUTDOWN_TIMEOUT})",
    )
    core.add_argument(
        "--stale-grace-sec",
        type=float,
        default=constants.DEFAULT_STALE_GRACE_SEC,
        help=f"Keep configured weight if health is stale (default: {constants.DEFAULT_STALE_GRACE_SEC})",
    )
    core.add_argument("--cold-start-sec", type=float, default=constants.DEFAULT_COLD_START_SEC, help=f"Startup grace for unknown health (default: {constants.DEFAULT_COLD_START_SEC})")
    core.add_argument("--no-syslog", action="store_true", help="Disable syslog handler")
    core.add_argument("--log-level", default="info", help="Log level: debug|info|warning|error (default: info)")
    core.add_argument("--debug", action="store_true", help="Enable debug output")
    core.add_argument("--clear-on-exit", action="store_true", help="Clear managed IPVS entries on shutdown")
    core.add_argument("--startup-full-replace", action="store_true", help="Clear live managed state before first apply")
    core.add_argument(
        "--service",
        dest="service_mode",
        action="store_true",
        help="Run as long-lived daemon (use under systemd); mutually exclusive with one-shot actions",
    )

    ops = p.add_argument_group("One-shot actions")
    ops.add_argument("--test", dest="test_mode", action="store_true", help="Validate config and exit")
    ops.add_argument("-s", "--status", dest="status_mode", action="store_true", help="Print one-shot status")
    ops.add_argument("-S", "--stats", dest="stats_mode", action="store_true", help="Print one-shot live IPVS stats")
    ops.add_argument("--status-detailed", action="store_true", help="Print detailed status report")
    ops.add_argument("--reset", dest="reset_mode", action="store_true", help="Clear managed state and exit")
    ops.add_argument("--dump", dest="dump_mode", action="store_true", help="Dump live IPVS config and exit")
    ops.add_argument("--reload", dest="reload_mode", action="store_true", help="Validate config, signal daemon reload, and exit")
    ops.add_argument("--pid", dest="pid_hint", type=int, default=None, help="Daemon PID hint for --reload")
    ops.add_argument("--healthcheck-now", action="store_true", help="Run all checks once and exit")
    ops.add_argument("--healthcheck-now-group", default=None, help="Run checks for one group")
    ops.add_argument("--healthcheck-now-backend", default=None, help="Run checks for one backend group/ip")
    ops.add_argument("--list-services", action="store_true", help="List virtual services from config")
    ops.add_argument("--list-frontends", action="store_true", help="List frontends from config")
    ops.add_argument("--list-backends", action="store_true", help="List backends from config")
    ops.add_argument("--list-healthchecks", action="store_true", help="List healthcheck bindings from config")

    obs = p.add_argument_group("Output and filters")
    obs.add_argument("--show-counters", action="store_true", help="Include counters in detailed output")
    obs.add_argument("--show-rates", type=float, default=None, help="Show rate deltas over N seconds")
    obs.add_argument("--only-active", action="store_true", help="Show only active rows")
    obs.add_argument("--filter-group", default=None, help="Filter by group name")
    obs.add_argument("--filter-frontend", default=None, help="Filter by group/frontend")
    obs.add_argument("--filter-backend", default=None, help="Filter by backend IP")
    obs.add_argument("--output", choices=["json", "table"], default="table", help="Output format (default: table)")
    obs.add_argument("--watch", type=float, default=None, help="Refresh output every N seconds")
    obs.add_argument("--no-color", action="store_true", help="Disable ANSI colors")

    api = p.add_argument_group("API and metrics")
    api.add_argument("--prometheus-metrics", action="store_true", help="Enable Prometheus metrics endpoint")
    api.add_argument(
        "--no-prometheus-metrics-stats",
        dest="prometheus_metrics_stats",
        action="store_false",
        help="Disable live IPVS stats metrics in Prometheus output",
    )
    api.add_argument(
        "--no-prometheus-metrics-healthcheks",
        dest="prometheus_metrics_healthchecks",
        action="store_false",
        help="Disable healthcheck metrics in Prometheus output",
    )
    api.add_argument(
        "--prometheus-metrics-stats-labels",
        choices=["configured", "route", "both"],
        default="configured",
        help="IPVS stats labels mode: configured|route|both (default: configured)",
    )
    api.add_argument("--prometheus-host", default=constants.DEFAULT_PROM_HOST, help=f"Metrics bind host (default: {constants.DEFAULT_PROM_HOST})")
    api.add_argument("--prometheus-port", type=int, default=constants.DEFAULT_PROM_PORT, help=f"Metrics bind port (default: {constants.DEFAULT_PROM_PORT})")
    api.add_argument("--api-enable", action="store_true", help="Enable HTTP API")
    api.add_argument("--api-host", default=constants.DEFAULT_API_HOST, help=f"API bind host (default: {constants.DEFAULT_API_HOST})")
    api.add_argument("--api-port", type=int, default=constants.DEFAULT_API_PORT, help=f"API bind port (default: {constants.DEFAULT_API_PORT})")
    api.add_argument("--api-enable-write", action="store_true", help="Enable API write endpoints")
    api.add_argument("--api-token", default=None, help=f"API token (fallback env: {constants.DEFAULT_API_TOKEN_ENV})")
    api.add_argument("--api-max-body-bytes", type=int, default=constants.DEFAULT_API_MAX_BODY_BYTES, help=f"Max API request body bytes (default: {constants.DEFAULT_API_MAX_BODY_BYTES})")

    runtime = p.add_argument_group("Runtime disable toggles")
    runtime.add_argument("--disable-group", default=None, help="Disable all services in group")
    runtime.add_argument("--disable-frontend", default=None, help="Disable one frontend (group/frontend)")
    runtime.add_argument("--disable-backend", default=None, help="Set backend weight to 0 (group/backend_ip)")
    runtime.add_argument("--enable-group", default=None, help="Enable all services in group")
    runtime.add_argument("--enable-frontend", default=None, help="Enable one frontend (group/frontend)")
    runtime.add_argument("--enable-backend", default=None, help="Restore backend configured weight (group/backend_ip)")
    return p


def parse_config(argv: list[str] | None = None) -> Config:
    """Parse CLI args into Config.

    Input:
    - argv: Optional list of CLI arguments.

    Output:
    - Parsed runtime Config with API token fallback from env.
    """
    args = build_parser().parse_args(argv)
    if not args.api_token:
        args.api_token = os.getenv(constants.DEFAULT_API_TOKEN_ENV)
    fields = vars(args).copy()
    fields["list_mask"] = _list_mask_from_cli(
        fields.pop("list_services"),
        fields.pop("list_frontends"),
        fields.pop("list_backends"),
        fields.pop("list_healthchecks"),
    )
    return Config(**fields)
