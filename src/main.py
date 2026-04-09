"""Main entrypoint."""

from __future__ import annotations

import copy
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

from src.api import ApiServer
from src.apply_runtime import ApplyRuntime
from src.check_runtime import CheckRuntime, make_backend_health_key
from src.constants import (
    HEALTH_HEALTHY,
    HEALTH_UNHEALTHY,
    LIST_MASK_BACKENDS,
    LIST_MASK_FRONTENDS,
    LIST_MASK_HEALTHCHECKS,
    LIST_MASK_SERVICES,
)
from src.config import has_cli_action, parse_config
from src.loader import load_snapshot
from src.list_views import list_backends, list_frontends, list_healthchecks, list_services
from src.lock import ProcessLock, read_lock_pid
from src.logging_util import setup_logging
from src.manual_checks import run_manual_checks
from src.metrics import MetricsServer
from src.proctitle import apply_proctitle, build_process_title
from src.reconcile import build_apply_plan, desired_services
from src.reload_runtime import ReloadRuntime
from src.state import RuntimeState, read_desired_snapshot
from src.signal_runtime import install_handlers
from src.status_cmd import print_status
from src.version import get_version_string
from src import ipvs_exec
from src.cli_observability import maybe_watch, print_detailed


def _print_listing(snapshot: dict[str, Any], cfg: Any) -> int:
    """Print listing(s) for cfg.list_mask; table or combined json by section."""
    m = cfg.list_mask
    if m is None:
        return 1

    def _table_rows(rows: list[dict[str, Any]], render: Any) -> str:
        return "\n".join(render(row) for row in rows)

    sections: list[tuple[str, list[dict[str, Any]], Any]] = []
    if m & LIST_MASK_SERVICES:
        svcs = list_services(snapshot)
        sections.append(
            (
                "services",
                svcs,
                lambda s: (
                    f"group={s['group']} frontend={s['frontend_name']} proto={s['proto']} "
                    f"vip={s['vip']} frontend_port={s['port']}"
                ),
            )
        )
    if m & LIST_MASK_FRONTENDS:
        rows = list_frontends(snapshot)
        sections.append(
            (
                "frontends",
                rows,
                lambda r: (
                    f"group={r['group']} frontend={r['name']} proto={r['proto']} "
                    f"vip={r['vip']} frontend_port={r['port']}"
                ),
            )
        )
    if m & LIST_MASK_BACKENDS:
        rows = list_backends(snapshot)
        sections.append(
            (
                "backends",
                rows,
                lambda r: (
                    f"group={r['group']} frontend={r['frontend']} "
                    f"frontend_proto={r['proto']} frontend_vip={r['vip']} frontend_port={r['frontend_port']} "
                    f"backend={r['ip']} backend_port={r['port']} weight={r['weight']}"
                ),
            )
        )
    if m & LIST_MASK_HEALTHCHECKS:
        rows = list_healthchecks(snapshot)
        sections.append(
            (
                "healthchecks",
                rows,
                lambda r: (
                    f"group={r['group']} frontend={r['frontend']} "
                    f"type={r['healthcheck'].get('type')} "
                    f"disabled={bool(r['healthcheck'].get('disable', False))} "
                    f"interval={r['healthcheck'].get('interval')} timeout={r['healthcheck'].get('timeout')} "
                    f"rise={r['healthcheck'].get('rise')} fall={r['healthcheck'].get('fall')}"
                    + (
                        f" query_name={r['healthcheck'].get('query_name')} query_type={r['healthcheck'].get('query_type')}"
                        if r['healthcheck'].get('type') == "dns"
                        else ""
                    )
                    + (
                        f" path={r['healthcheck'].get('path')} host={r['healthcheck'].get('host')} expected_status={r['healthcheck'].get('expected_status')}"
                        if r['healthcheck'].get('type') in ("http", "https")
                        else ""
                    )
                ),
            )
        )
    if not sections:
        return 1

    if cfg.output == "json":
        payload = {name: rows for name, rows, _ in sections}
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        blocks: list[str] = []
        for name, rows, render in sections:
            blocks.append(f"# {name}\n{_table_rows(rows, render)}")
        print("\n\n".join(blocks))
    return 0


def _print_live_stats(cfg: Any) -> int:
    """Print live IPVS stats in selected output format."""
    try:
        stats = ipvs_exec.read_stats()
    except Exception as exc:
        print(f"ipvsman: stats read failed: {exc}", file=sys.stderr)
        return 2
    if cfg.output == "json":
        payload = {
            "services": [
                {
                    "proto": svc.proto,
                    "vip": svc.vip,
                    "port": svc.port,
                    "conns": svc.conns,
                    "inpkts": svc.inpkts,
                    "outpkts": svc.outpkts,
                    "inbytes": svc.inbytes,
                    "outbytes": svc.outbytes,
                    "reals": [
                        {
                            "ip": rs.ip,
                            "port": rs.port,
                            "conns": rs.active_conn,
                            "inpkts": rs.inpkts,
                            "outpkts": rs.outpkts,
                            "inbytes": rs.inbytes,
                            "outbytes": rs.outbytes,
                        }
                        for rs in svc.reals
                    ],
                }
                for svc in stats.services
            ]
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    rows: list[tuple[str, str, str, int, int, int, int, int]] = []
    for svc in stats.services:
        rows.append(("svc", svc.proto, f"{svc.vip}:{svc.port}", svc.conns, svc.inpkts, svc.outpkts, svc.inbytes, svc.outbytes))
        for rs in svc.reals:
            rows.append(("rs", svc.proto, f"{rs.ip}:{rs.port}", rs.active_conn, rs.inpkts, rs.outpkts, rs.inbytes, rs.outbytes))
    col_proto = "PROTO"
    col_name = "NAME"
    col_conns = "CONNS"
    col_inpkts = "INPKTS"
    col_outpkts = "OUTPKTS"
    col_inbytes = "INBYTES"
    col_outbytes = "OUTBYTES"
    proto_w = max(len(col_proto), *(len(r[1]) for r in rows)) if rows else len(col_proto)
    name_w = max(len(col_name), *(len(r[2]) for r in rows)) if rows else len(col_name)
    conns_w = max(len(col_conns), *(len(str(r[3])) for r in rows)) if rows else len(col_conns)
    inpkts_w = max(len(col_inpkts), *(len(str(r[4])) for r in rows)) if rows else len(col_inpkts)
    outpkts_w = max(len(col_outpkts), *(len(str(r[5])) for r in rows)) if rows else len(col_outpkts)
    inbytes_w = max(len(col_inbytes), *(len(str(r[6])) for r in rows)) if rows else len(col_inbytes)
    outbytes_w = max(len(col_outbytes), *(len(str(r[7])) for r in rows)) if rows else len(col_outbytes)
    header = (
        f"{'TYPE':<4} "
        f"{col_proto:<{proto_w}} "
        f"{col_name:<{name_w}} "
        f"{col_conns:>{conns_w}} "
        f"{col_inpkts:>{inpkts_w}} "
        f"{col_outpkts:>{outpkts_w}} "
        f"{col_inbytes:>{inbytes_w}} "
        f"{col_outbytes:>{outbytes_w}}"
    )
    print(header)
    print("-" * len(header))
    for kind, proto, name, conns, inpkts, outpkts, inbytes, outbytes in rows:
        row_type = "svc" if kind == "svc" else "rs"
        print(
            f"{row_type:<4} "
            f"{proto:<{proto_w}} "
            f"{name:<{name_w}} "
            f"{conns:>{conns_w}} "
            f"{inpkts:>{inpkts_w}} "
            f"{outpkts:>{outpkts_w}} "
            f"{inbytes:>{inbytes_w}} "
            f"{outbytes:>{outbytes_w}}"
        )
    return 0


def _apply_runtime_disables(snapshot: dict[str, Any], cfg: Any) -> None:
    """Apply one-shot runtime disable flags."""
    for grp in snapshot.get("groups", []):
        for svc in grp.get("services", []):
            if cfg.disable_group and svc["group"] == cfg.disable_group:
                svc["disabled"] = True
            if cfg.disable_frontend and f"{svc['group']}/{svc['frontend_name']}" == cfg.disable_frontend:
                svc["disabled"] = True
            if cfg.enable_group and svc["group"] == cfg.enable_group:
                svc["disabled"] = False
            if cfg.enable_frontend and f"{svc['group']}/{svc['frontend_name']}" == cfg.enable_frontend:
                svc["disabled"] = False
            for rs in svc.get("reals", []):
                if cfg.disable_backend and f"{svc['group']}/{rs['ip']}" == cfg.disable_backend:
                    rs["weight"] = 0
                if cfg.enable_backend and f"{svc['group']}/{rs['ip']}" == cfg.enable_backend:
                    rs["weight"] = rs.get("configured_weight", rs["weight"])


def _has_runtime_overrides(cfg: Any) -> bool:
    """Return True when runtime override flags are present.

    Input:
    - cfg: Parsed CLI config.

    Output:
    - True if any enable/disable override is set.
    """
    return bool(
        cfg.disable_group
        or cfg.disable_frontend
        or cfg.disable_backend
        or cfg.enable_group
        or cfg.enable_frontend
        or cfg.enable_backend
    )


def _apply_health_weights(snapshot: dict[str, Any], state: Any, cold_start_sec: float, stale_grace_sec: float) -> None:
    """Apply health-cache driven effective weights."""
    now = time.time()
    for grp in snapshot.get("groups", []):
        for svc in grp.get("services", []):
            for rs in svc.get("reals", []):
                key = make_backend_health_key(
                    str(svc["group"]),
                    str(svc["frontend_name"]),
                    str(rs["ip"]),
                    int(rs["port"]),
                )
                hc = state.get_health(key)
                if not hc.ready and (now - state.started_at) <= cold_start_sec:
                    rs["weight"] = rs.get("configured_weight", rs["weight"])
                    continue
                if hc.updated_at <= 0.0 or (now - hc.updated_at) > stale_grace_sec:
                    rs["weight"] = rs.get("configured_weight", rs["weight"])
                    continue
                if hc.state == HEALTH_UNHEALTHY:
                    rs["weight"] = 0
                elif hc.state == HEALTH_HEALTHY:
                    rs["weight"] = rs.get("configured_weight", rs["weight"])


def _runtime_snapshot_with_live(base_snapshot: dict[str, Any], live_state: Any) -> dict[str, Any]:
    """Clone desired snapshot and attach live state.

    Inputs:
    - base_snapshot: desired snapshot from shared runtime state.
    - live_state: latest live IPVS state.

    Output:
    - Deep-copied runtime snapshot safe to mutate.
    """
    out = copy.deepcopy(base_snapshot)
    out["live_state"] = live_state
    return out


def main(argv: list[str] | None = None) -> int:
    """CLI main."""
    cli_args = argv if argv is not None else sys.argv[1:]
    cfg = parse_config(argv)
    if getattr(cfg, "version", False):
        print(get_version_string())
        return 0
    if not cfg.service_mode and not has_cli_action(cfg):
        print(
            "ipvsman: specify --service (daemon) or a one-shot action "
            "(--test, --list-services, --status, --stats, --status-detailed, --reset, --dump, --reload, --healthcheck-now, --disable-*, --enable-*, ...)",
            file=sys.stderr,
        )
        return 2
    if cfg.service_mode and has_cli_action(cfg):
        print("ipvsman: --service cannot be combined with one-shot actions", file=sys.stderr)
        return 2
    # In service mode, stderr is captured by journald; disable syslog handler to avoid duplicate entries.
    log = setup_logging(cfg.log_level, no_syslog=(cfg.no_syslog or cfg.service_mode), debug=cfg.debug)
    apply_proctitle(build_process_title(cli_args))
    if cfg.service_mode:
        log.info("start argv=%s config_dir=%s service=%s", cli_args, cfg.config_dir, cfg.service_mode)
    if cfg.api_enable and not cfg.api_token and cfg.api_host not in {"127.0.0.1", "localhost", "::1"}:
        log.critical("ALERT: API exposed on non-localhost without token (host=%s)", cfg.api_host)

    if cfg.dump_mode:
        try:
            print(ipvs_exec.read_dump(), end="")
            return 0
        except Exception as exc:
            log.error("ipvs dump failed: %s", exc)
            return 2

    if cfg.stats_mode:
        return _print_live_stats(cfg)

    if cfg.reload_mode:
        try:
            _ = load_snapshot(cfg.config_dir)
        except Exception as exc:
            log.error("reload validation failed: %s", exc)
            return 2
        pid = cfg.pid_hint if cfg.pid_hint is not None else read_lock_pid(cfg.lock_file)
        if pid is None:
            log.error("reload failed: cannot read daemon pid (lock_file=%s)", cfg.lock_file)
            return 2
        try:
            os.kill(pid, signal.SIGHUP)
            return 0
        except OSError as exc:
            log.error("reload failed: cannot signal pid %s: %s", pid, exc)
            return 2

    try:
        snapshot = load_snapshot(cfg.config_dir)
        for w in snapshot.get("warnings", []):
            log.warning("load warning: %s", w)
    except Exception as exc:
        log.error("load failed: %s", exc)
        return 2

    _apply_runtime_disables(snapshot, cfg)

    if cfg.test_mode:
        print("validation ok")
        return 0

    if cfg.list_mask is not None:
        return _print_listing(snapshot, cfg)

    if cfg.healthcheck_now or cfg.healthcheck_now_group or cfg.healthcheck_now_backend:
        def _on_check_result(row: dict[str, Any]) -> None:
            if not cfg.debug:
                return
            status = "UP" if row["ok"] else "DOWN"
            print(
                f"check {row['group']}/{row['frontend']} {row['backend_ip']} -> {status}"
                + (f" ({row['message']})" if row.get("message") else "")
            )

        result = run_manual_checks(
            snapshot,
            cfg.healthcheck_now_group,
            cfg.healthcheck_now_backend,
            on_result=_on_check_result,
        )
        if cfg.output == "json":
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(f"total={result['total']} ok={result['ok']} failed={result['failed']}")
            # Always print failed checks in table mode, even without --debug.
            for row in result["results"]:
                if row["ok"]:
                    continue
                print(
                    f"failed {row['group']}/{row['frontend']} {row['backend_ip']}"
                    + (f": {row['message']}" if row.get("message") else "")
                )
        return 0 if result["failed"] == 0 else 1

    if _has_runtime_overrides(cfg):
        try:
            live = ipvs_exec.read_live()
        except Exception as exc:
            log.error("ipvs read failed: %s", exc)
            live = ipvs_exec.LiveIpvsState()
        plan = build_apply_plan(desired_services(snapshot), live)
        result = ipvs_exec.apply_plan(plan)
        if cfg.output == "json":
            print(json.dumps({"ok": result.ok, "message": result.message}, indent=2, sort_keys=True))
        else:
            print("runtime overrides applied" if result.ok else f"runtime override apply failed: {result.message}")
        return 0 if result.ok else 1

    try:
        live = ipvs_exec.read_live()
    except Exception as exc:
        log.error("ipvs read failed: %s", exc)
        live = ipvs_exec.LiveIpvsState()
    snapshot["live_state"] = live

    if cfg.status_mode:
        return print_status(snapshot, live, cfg.output)

    if cfg.status_detailed:
        print_detailed(
            snapshot,
            live,
            cfg.output,
            cfg.show_counters,
            cfg.only_active,
            filter_group=cfg.filter_group,
            filter_frontend=cfg.filter_frontend,
            filter_backend=cfg.filter_backend,
        )
        return 0

    if cfg.reset_mode:
        lock = ProcessLock(cfg.lock_file)
        if not lock.acquire():
            log.error("cannot acquire lock: %s", cfg.lock_file)
            return 2
        try:
            # coarse reset: clear known live then re-apply on next cycle
            ipvs_exec.clear_managed(ipvs_exec.ManagedScope(services=live.services))
            return 0
        finally:
            lock.release()

    lock = ProcessLock(cfg.lock_file)
    if not lock.acquire():
        log.error("lock held by another process: %s", cfg.lock_file)
        return 2

    stop_event = threading.Event()

    def _stop() -> None:
        stop_event.set()

    state = RuntimeState()
    state.desired_snapshot = snapshot
    state.config_version_mtime = float(snapshot.get("config_version_mtime", 0.0))
    state.desired_generation = int(state.config_version_mtime)
    state.loaded_files_count = int(snapshot.get("loaded_files_count", 0))

    reloader = ReloadRuntime(cfg.config_dir, state, log)
    checker = CheckRuntime(cfg.check_workers, state, log=log)
    applier = ApplyRuntime(state, log)
    shutdown_timeout = max(0.5, cfg.shutdown_timeout)
    metrics_on_api = bool(cfg.api_enable and cfg.prometheus_metrics)
    metrics = (
        MetricsServer(
            state,
            cfg.prometheus_host,
            cfg.prometheus_port,
            shutdown_timeout=shutdown_timeout,
            include_ipvs_stats=cfg.prometheus_metrics_stats,
            include_healthchecks=cfg.prometheus_metrics_healthchecks,
            ipvs_stats_labels_mode=cfg.prometheus_metrics_stats_labels,
        )
        if (cfg.prometheus_metrics and not metrics_on_api)
        else None
    )
    api = (
        ApiServer(
            state=state,
            config_dir=cfg.config_dir,
            host=cfg.api_host,
            port=cfg.api_port,
            token=cfg.api_token,
            enable_write=cfg.api_enable_write,
            max_body_bytes=cfg.api_max_body_bytes,
            enable_metrics=metrics_on_api,
            metrics_include_ipvs_stats=cfg.prometheus_metrics_stats,
            metrics_include_healthchecks=cfg.prometheus_metrics_healthchecks,
            metrics_ipvs_stats_labels_mode=cfg.prometheus_metrics_stats_labels,
            shutdown_timeout=shutdown_timeout,
        )
        if cfg.api_enable
        else None
    )

    install_handlers(on_reload=reloader.trigger, on_stop=_stop)

    reloader.start()
    applier.start()
    if metrics:
        metrics.start()
    if api:
        api.start()

    if cfg.startup_full_replace:
        ipvs_exec.clear_managed(ipvs_exec.ManagedScope(services=live.services))

    last_reload = 0.0
    last_live_poll = 0.0
    live_now = live
    try:
        while not stop_event.is_set():
            now = time.time()
            if cfg.reload_interval > 0 and now - last_reload >= max(0.5, cfg.reload_interval):
                reloader.trigger()
                last_reload = now

            if now - last_live_poll >= max(0.5, cfg.stats_interval):
                try:
                    live_now = ipvs_exec.read_live()
                except Exception as exc:
                    log.error("ipvs read failed: %s", exc)
                    live_now = ipvs_exec.LiveIpvsState()
                last_live_poll = now
            with state.lock:
                if not state.desired_snapshot:
                    time.sleep(max(0.5, cfg.interval))
                    continue
                state.desired_snapshot["live_state"] = live_now
                snap = _runtime_snapshot_with_live(state.desired_snapshot, live_now)
                gen = int(state.desired_generation)
            _apply_runtime_disables(snap, cfg)
            for grp in snap.get("groups", []):
                for svc in grp.get("services", []):
                    checker.check_service(svc)
            _apply_health_weights(snap, state, cfg.cold_start_sec, cfg.stale_grace_sec)
            applier.submit(gen, snap)
            maybe_watch(cfg.watch if cfg.watch is not None else cfg.interval)
    finally:
        if cfg.clear_on_exit:
            try:
                snap = read_desired_snapshot(state)
                if snap:
                    ipvs_exec.clear_managed(ipvs_exec.ManagedScope(services=desired_services(snap)))
            except Exception as exc:
                log.error("clear-on-exit failed: %s", exc)
        checker.stop()
        applier.stop(shutdown_timeout)
        reloader.stop(shutdown_timeout)
        if api:
            api.stop()
        if metrics:
            metrics.stop()
        lock.release()

    return 0
