# ipvsman

`ipvsman` manages Linux IPVS services from YAML drop-ins.

## Quick usage

Every invocation must either run the long-lived **service** (`--service`) or a **one-shot action** (for example `--test`, `--list-services`, `--status-detailed`).

```bash
# Validate config
ipvsman.py --test

# One-shot status
ipvsman.py --status-detailed --show-counters

# Daemon mode (e.g. under systemd)
ipvsman.py --service
```

## Common commands

```bash
# Lists
ipvsman.py --list-services
ipvsman.py --list-frontends
ipvsman.py --list-backends
ipvsman.py --list-healthchecks

# Manual checks
ipvsman.py --healthcheck-now
ipvsman.py --healthcheck-now-group dns-core
ipvsman.py --healthcheck-now-backend dns-core/10.0.0.10

# Filtered watch output
ipvsman.py --status-detailed --filter-group dns-core --watch 2
```

## Flags and defaults

### Core runtime

| Flag | Default | Notes |
| --- | --- | --- |
| `--service` | disabled | long-lived daemon; required for systemd unless using a one-shot action |
| `--config-dir` | `/usr/local/etc/ipvsman` | config root |
| `--interval` | `5.0` | main loop tick seconds |
| `--reload-interval` | `30.0` | config reload trigger seconds |
| `--stats-interval` | `15.0` | live IPVS poll seconds (0 to disable)|
| `--lock-file` | `/run/ipvsman/ipvsman.lock` | single-instance lock file |
| `--pid` | unset | daemon PID hint for `--reload` |
| `--check-workers` | `16` | healthcheck worker count |
| `--shutdown-timeout` | `10.0` | worker shutdown wait seconds |
| `--cold-start-sec` | `30.0` | startup grace for unknown health |
| `--stale-grace-sec` | `30.0` | keep weight when health is stale |

`--service` is mutually exclusive with one-shot actions (`--test`, `--list-*`, `--status`, `--status-detailed`, `--reset`, `--healthcheck-now`, …).

### API

| Flag | Default | Notes |
| --- | --- | --- |
| `--api-enable` | disabled | enable HTTP API listener |
| `--api-host` | `127.0.0.1` | API bind host |
| `--api-port` | `9111` | API bind port |
| `--api-token` | unset | falls back to `IPVSMAN_API_TOKEN` |
| `--api-enable-write` | disabled | allows `PUT /v1/config` |
| `--api-max-body-bytes` | `1048576` | max API write payload (1 MiB) |

### Metrics

| Flag | Default | Notes |
| --- | --- | --- |
| `--prometheus-metrics` | disabled | enable metrics endpoint |
| `--no-prometheus-metrics-stats` | disabled | do not export live `ipvsadm --stats` metrics |
| `--no-prometheus-metrics-healthcheks` | disabled | do not export healthcheck metrics |
| `--prometheus-metrics-stats-labels` | `configured` | stats label mode: `configured`, `route`, or `both` |
| `--prometheus-host` | `localhost` | standalone metrics bind host |
| `--prometheus-port` | `9110` | standalone metrics bind port |

**Naming:** backend resolve metrics mirror backend IP change — global `ipvsman_backend_resolve_error_events_total`, per-frontend `ipvsman_backend_resolve_errors_total` / `ipvsman_backend_resolve_errors_last_timestamp_seconds`. When live IPVS stats are enabled, scrape errors increment `ipvsman_ipvs_stats_scrape_failures_total` (counter).

Configured stats labels:
- service metrics: `group`, `frontend`
- real metrics: `group`, `frontend`, `address`, `backend_port` (`address` maps to configured backend address/hostname when available)

Healthcheck metrics:
- `ipvsman_healthcheck_state` (`1` healthy, `0` unhealthy, `-1` unknown)
- `ipvsman_healthcheck_ready` (`1` ready, `0` not ready)
- Labels follow the same configured/route/both mode as stats labels.

Example alert (Prometheus):

```yaml
groups:
  - name: ipvsman
    rules:
      - alert: IpvsmanIpvsStatsScrapeFailing
        expr: increase(ipvsman_ipvs_stats_scrape_failures_total[5m]) > 0
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: ipvsman cannot read live IPVS stats for metrics
```

### Output and filters

| Flag | Default | Notes |
| --- | --- | --- |
| `--output` | `table` | `json` or `table` |
| `--watch` | disabled | refresh output every N seconds |
| `--show-counters` | disabled | include counters in detailed output |
| `--show-rates <seconds>` | disabled | include rate deltas |
| `--only-active` | disabled | show active rows only |
| `--filter-group` | unset | filter by group name |
| `--filter-frontend` | unset | filter by `group/frontend` |
| `--filter-backend` | unset | filter by backend IP |
| `--no-color` | disabled | no ANSI colors |

### Operations and one-shot actions

| Flag | Default | Notes |
| --- | --- | --- |
| `--test` | disabled | validate config and exit |
| `--list-services` | disabled | list virtual services |
| `--list-frontends` | disabled | list frontends |
| `--list-backends` | disabled | list backends |
| `--list-healthchecks` | disabled | list healthcheck rows |
| `--status`, `-s` | disabled | one-shot status |
| `--stats`, `-S` | disabled | one-shot live IPVS stats (`ipvsadm -ln --stats`) |
| `--status-detailed` | disabled | detailed report |
| `--reset` | disabled | clear managed state |
| `--dump` | disabled | dump live IPVS config (`ipvsadm -Sn`) |
| `--reload` | disabled | validate config, then send `SIGHUP` to daemon |
| `--healthcheck-now` | disabled | run all checks once |
| `--healthcheck-now-group` | unset | run checks for one group |
| `--healthcheck-now-backend` | unset | run checks for one backend |
| `--clear-on-exit` | disabled | clear managed entries on shutdown |
| `--startup-full-replace` | disabled | clear first, then reconcile |
| `--disable-group` | unset | one-shot disable by group |
| `--disable-frontend` | unset | one-shot disable by `group/frontend` |
| `--disable-backend` | unset | one-shot weight=0 by `group/backend_ip` |

## Config layout

The directory passed to `--config-dir` (default `/usr/local/etc/ipvsman`) contains:
- `groups/*.yaml`
- `backends/*.yaml`
- `backend-maps/*.yaml`
- `check-refs/*.yaml`

Path rules for `backend_files`:
- only relative paths are allowed
- absolute paths are rejected
- path traversal outside that config directory is rejected

### Example group

```yaml
- group: dns-core
  vip: [192.0.2.1, 198.51.100.1]
  scheduler: wrr
  frontends:
    - name: dns-udp
      proto: udp
      port: domain
    - name: dns-tcp
      proto: tcp
      port: domain
    - name: doh
      proto: tcp
      port: https
      vip: [192.0.2.2]
      scheduler: rr
  backend_map_ref: dns_pool
  healthcheck:
    type: dns
    query_name: health.example.com
    query_type: A
    interval: 10
    timeout: 3
    rise: 2
    fall: 3
```

### Example backend map

```yaml
dns_pool:
  - ip: 10.0.0.10
    weight: 10
    port_map:
      "domain": 53
  - ip: 10.0.0.11
    weight: 10
    port_map:
      "*": 5353
    check_ref: registry-ns2
```

### Example check refs

```yaml
registry-ns2:
  ip: 192.168.1.100
  port: 9999
  type: http
  path: /status/ns2
```

## HTTP API

API is off by default. Enable with `--api-enable`.

```bash
# API enabled (daemon)
ipvsman.py --service --api-enable --api-host 127.0.0.1 --api-port 9111

# Token from env
export IPVSMAN_API_TOKEN='replace-with-secret'
ipvsman.py --service --api-enable
```

Token resolution order:
- `--api-token`
- `IPVSMAN_API_TOKEN`
- unset (no auth required)

Security note:
- if `--api-host` is not localhost (`127.0.0.1`, `localhost`, `::1`) and no token is set, startup logs an `ALERT`

Rate limiting:
- per-IP sliding window on all API routes
- default `300 requests/minute` per client IP
- overflow returns `429 {"error":"rate limit"}`

Read endpoints:
- `GET /v1/services`
- `GET /v1/frontends`
- `GET /v1/backends`
- `GET /v1/healthchecks`
- `GET /v1/status/detailed`
- `POST /v1/healthchecks/run`
- `GET /openapi.json`
- `GET /openapi.yaml`
- `GET /metrics` (only when API + metrics are both enabled)

Write endpoint:
- `PUT /v1/config` (requires `--api-enable-write`)
- writes to `groups/api-put.yaml`
- reloads desired snapshot atomically

Common response codes:
- `401` unauthorized
- `403` write disabled
- `404` not found
- `413` payload too large
- `422` validation failed
- `429` rate limit exceeded

## Metrics

Standalone metrics server:

```bash
ipvsman.py --service --prometheus-metrics --prometheus-host localhost --prometheus-port 9110
```

If API and metrics are both enabled, `/metrics` is served on API port (`9111` by default).

Format support:
- default: Prometheus text (`text/plain; version=0.0.4`)
- OpenMetrics: send `Accept: application/openmetrics-text`

## Technical notes

- **systemd**: the role ships a static unit file that runs `ipvsman.py --service`. `/etc/default/ipvsman` may provide optional `IPVSMAN_API_TOKEN` and optional `IPVSMAN_EXTRA_ARGS`.
- single-instance lock prevents duplicate daemon runs
- `SIGHUP` triggers reload (`systemctl reload ipvsman`)
- apply worker is single-threaded with queue coalescing
- config version is newest loaded file mtime
- live IPVS polling uses `--stats-interval`
- API `PUT` enforces `--api-max-body-bytes`

## Development tests

```bash
cd files
PYTHONPATH=. python3 -m unittest discover -s src/test -v
```

## How this project was built

This is an **AI-assisted** project: most implementation and routine edits were produced with **Cursor** agents/assistants, while **design and architecture decisions** remain **human-driven**.
