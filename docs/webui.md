# YWD packet WebUI

The YWD WebUI is the read-only browser dashboard for YWD-MMDVM-TNC packet activity. It consumes the already-qualified passive monitor plane and historical packet logs without becoming part of the modem, TNC, channel-access, or RF path.

The WebUI is physically qualified on the Raspberry Pi as a passive observer. The physical gate observed a real schema-1 `rx.frame` event through browser-facing SSE while confirming that the gate generated no KISS frame, no RF transmission, and exposed no HTTP mutation API.

Physical checkpoint:

```text
checkpoint/webui-physical-qualified
96183be903ec18f0c852de3b1c0a2089bc8c3dc5
```

Machine-readable evidence is recorded in:

```text
qualification/webui-host-2026-09-10.json
qualification/webui-physical-2026-09-11.json
```

## Safety boundary

The WebUI reads only:

- live schema-1 NDJSON from `127.0.0.1:8002`;
- historical structured logs from `/var/log/ywd-packetlog/YYYY-MM-DD.jsonl`;
- historical human logs from `/var/log/ywd-packetlog/YYYY-MM-DD.log`.

It does **not** open TCP KISS, AGW, the modem UART, GPIO, firmware tooling, TX admission, channel-access, or RF objects. Its application HTTP API implements GET endpoints only. There is no WebUI transmit or modem-control endpoint.

The qualified monitor source remains the first backpressure boundary. A slow WebUI process can lose monitor events without blocking packet RX/TX. Inside the WebUI, each browser SSE client gets another bounded queue; a slow or suspended browser can likewise lose display events without blocking the monitor reader.

Stopping, restarting, crashing, or disconnecting the WebUI does not give it ownership of the HAT or alter the modem/RF lifecycle.

## Process layout

```text
                                  QUALIFIED MODEM / TNC PATH

packet app --> KISS :8001 --+
                            |
packet app --> AGW :8000 ---+--> ywd-tncd --> Bell-202 / AX.25 --> HAT --> RF
                            |
                            +--> passive monitor :8002
                                      |          \
                                      |           +--> ywd-packetlog
                                      |                  |
                                      |                  +--> daily .jsonl / .log
                                      |
                                      +--> ywd-webui --> HTTP/SSE :8088 --> browser
```

`ywd-webui.service` is separate from both `ywd-mmdvm-tnc.service` and `ywd-packetlog.service`.

## What the UI shows

The dependency-free browser interface uses a YWD retro/cyber terminal style and includes:

- live RX/TX event feed;
- monitor connection state, generation, last-event age, SSE clients, and WebUI drop count;
- animated RX/TX/error activity indicators;
- TX lifecycle visualization for queued, channel-clear, dispatched, complete, timeout, failed, and rejected states;
- frame inspector with source, destination, digipeater path, AX.25 control state, PID, payload, raw JSON, and frame hex;
- pause/resume for display updates without stopping monitor ingestion;
- historical structured-log browsing and filtering;
- daily RX/TX/error statistics;
- top-source and frame-type summaries;
- human-readable packet console view from the `.log` files;
- responsive mobile layout;
- `prefers-reduced-motion` support.

No CDN, remote font, analytics service, or JavaScript framework is required. Static assets are shipped with the package.

## HTTP API

The current read-only endpoints are:

```text
GET /api/v1/status
GET /api/v1/recent
GET /api/v1/stream        Server-Sent Events
GET /api/v1/dates
GET /api/v1/history
GET /api/v1/stats
GET /api/v1/human-log
GET /                     static WebUI
```

No POST/PUT/PATCH/DELETE handler exists.

Useful history queries include:

```text
/api/v1/history?date=2026-09-11&limit=250
/api/v1/history?date=2026-09-11&event=rx.frame
/api/v1/history?date=2026-09-11&event=tx.*
/api/v1/history?date=2026-09-11&station=KJ6YWD-11
/api/v1/history?date=2026-09-11&q=hello
```

History dates are strictly validated as `YYYY-MM-DD` before a path is constructed.

## Live event identity

Monitor `seq` is monotonic only for the current `ywd-tncd` process. The WebUI adds `_web_generation` to live browser copies and identifies SSE events as:

```text
<web-monitor-generation>:<monitor-seq>
```

The generation increments whenever the WebUI reconnects to the monitor source. It is display/session metadata only and is never written back to the monitor or logger.

## Installation

The low-level installer installs the WebUI package, service definition, and safe configuration without enabling or starting the service automatically:

```bash
sudo ./installer/install.sh
```

Installed WebUI configuration:

```text
/etc/ywd-mmdvm-tnc/webui.toml
```

Installed service:

```text
ywd-webui.service
```

Safe default browser endpoint:

```text
127.0.0.1:8088
```

The live monitor source remains:

```text
127.0.0.1:8002
```

## Enable the WebUI locally

For browser access on the same host:

```bash
sudo systemctl enable --now ywd-webui.service
sudo systemctl status ywd-webui.service --no-pager
```

Then open:

```text
http://127.0.0.1:8088/
```

Useful service commands:

```bash
sudo systemctl restart ywd-webui.service
sudo journalctl -u ywd-webui.service -f
curl -s http://127.0.0.1:8088/api/v1/status | python3 -m json.tool
```

## Trusted-LAN browser access

To view the dashboard from another device on a trusted LAN, explicitly change only the browser listener:

```toml
[web]
listen = "0.0.0.0"
port = 8088
allow_wildcard_bind = true

[monitor]
host = "127.0.0.1"
port = 8002
reconnect_seconds = 2.0
```

Then restart the WebUI:

```bash
sudo systemctl restart ywd-webui.service
```

Open the Raspberry Pi's actual LAN address from the browser, for example:

```text
http://192.168.1.50:8088/
```

`0.0.0.0` is a bind address, not a browser destination.

The WebUI bind validator accepts IPv4 loopback/private addresses and explicitly authorized `0.0.0.0`; direct public-address binds are rejected. The monitor source itself is required to remain loopback-only.

The WebUI does not provide authentication or TLS. Keep port `8088` on a trusted LAN or place an appropriate authenticated reverse proxy in front of it if stronger access control is required.

## Historical data

The history, stats, and console panels use `ywd-packetlog` output:

```text
/var/log/ywd-packetlog/YYYY-MM-DD.jsonl
/var/log/ywd-packetlog/YYYY-MM-DD.log
```

Enable packet logging separately when desired:

```bash
sudo systemctl enable --now ywd-packetlog.service
```

The WebUI can still display live traffic without historical logs. History/stats/console simply remain empty until packet-log data exists.

## Service hardening

`ywd-webui.service` is intentionally more restricted than the modem service. Its systemd sandbox includes controls such as:

```text
DynamicUser=true
NoNewPrivileges=true
PrivateDevices=true
ProtectHome=true
ProtectSystem=strict
CapabilityBoundingSet=
```

The service needs network access for its loopback monitor connection and browser listener, plus read access to the installed static assets and packet-log history. It does not need device access or modem capabilities.

## Host qualification

Run:

```bash
bash scripts/check-webui.sh
```

The host gate verifies:

- Python compilation and WebUI unit tests;
- safe config defaults and explicit wildcard opt-in;
- loopback-only monitor source;
- bounded SSE fan-out and slow-browser drop behavior;
- GET-only HTTP/SSE behavior;
- absence of modem/KISS/TX imports;
- absence of monitor socket write APIs;
- systemd hardening;
- framework-self-test hardware/RF inertness;
- no changes to the qualified modem/monitor closure relative to the original WebUI base.

The qualified host gate reports, among other markers:

```text
WEBUI_HOST_GATE=PASS
QUALIFIED_MODEM_MONITOR_PATH_CHANGED=NO
HTTP_MUTATION_API_PRESENT=NO
MONITOR_SOCKET_WRITE_API_PRESENT=NO
KISS_OR_MODEM_IMPORT_PRESENT=NO
MODEM_UART_OPENED=NO
RF_TRANSMITTED=NO
```

## Physical qualification

The observation-only physical gate is:

```bash
sudo bash /opt/ywd-mmdvm-tnc/source/scripts/webui-physical.sh
```

It does not create a KISS frame or request RF transmission. It checks the already-running WebUI and waits for normal station/network activity to produce a live monitor event.

The 2026-09-11 physical pass observed:

```text
WEBUI_STATUS_READ_ONLY=PASS
WEBUI_MONITOR_STATE=connected
WEBUI_HTTP_MUTATION_API=ABSENT
WEBUI_SSE_CONNECTED=PASS
WEBUI_LIVE_EVENT=rx.frame
WEBUI_LIVE_SEQ=2881
WEBUI_LIVE_GENERATION=1
WEBUI_LIVE_SCHEMA1_SSE=PASS
WEBUI_PHYSICAL_GATE=PASS
RF_GENERATED_BY_GATE=NO
KISS_FRAME_GENERATED_BY_GATE=NO
QUALIFIED_MODEM_PATH_CONTROLLED_BY_WEBUI=NO
```

Mobile-browser verification also showed live counters, zero WebUI drops at the observation point, populated history, daily stats, top-source/frame summaries, and the human-readable console rendering correctly.

## Troubleshooting

Check service status and logs first:

```bash
sudo systemctl status ywd-webui.service --no-pager -l
sudo journalctl -u ywd-webui.service -n 100 --no-pager
```

Check the local API:

```bash
curl -s http://127.0.0.1:8088/api/v1/status | python3 -m json.tool
```

Check the passive monitor listener:

```bash
sudo ss -ltnp | grep ':8002'
nc 127.0.0.1 8002
```

If the dashboard is live but history is empty, check the packet logger:

```bash
sudo systemctl status ywd-packetlog.service --no-pager -l
ls -lh /var/log/ywd-packetlog/
```

If a LAN browser cannot connect, verify the `[web]` block in `/etc/ywd-mmdvm-tnc/webui.toml`, confirm port `8088` is allowed from the trusted LAN, and connect to the Pi's real LAN IP rather than `0.0.0.0`.

## Related documentation

- [`monitor-events.md`](monitor-events.md) — schema-1 monitor protocol and lifecycle semantics
- [`rf-profiles.md`](rf-profiles.md) — bounded RF profiles
- [`linbpq-lan-p3.md`](linbpq-lan-p3.md) — LinBPQ TCP KISS integration
- [`../qualification/webui-host-2026-09-10.json`](../qualification/webui-host-2026-09-10.json) — host qualification evidence
- [`../qualification/webui-physical-2026-09-11.json`](../qualification/webui-physical-2026-09-11.json) — physical qualification evidence
