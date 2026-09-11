# Passive monitor event stream

YWD-MMDVM-TNC can expose a live, read-only event stream alongside TCP KISS and
AGW raw mode. The monitor is intended for logging, dashboards, telemetry, and
other observers that need to see both received AX.25 frames and the product TX
lifecycle without becoming part of the transmit path.

The default endpoint is:

```text
127.0.0.1:8002
```

The protocol is newline-delimited JSON (NDJSON), schema version `1`. Each line
is one complete JSON object.

## Safety and behavior

The monitor plane is deliberately side-band:

- it has no command API and no transmit API;
- client input is ignored and never reaches KISS, AGW, the TX queue, or the
  modem owner;
- there is no history replay when a client connects or reconnects;
- each client gets a bounded queue;
- a slow or broken monitor client drops monitor events instead of blocking RX,
  TX, CSMA, KISS, AGW, or modem ownership;
- the normal default bind is loopback only;
- wildcard bind still requires explicit `allow_wildcard_bind = true`, and
  public IPv4 binds are rejected by the same listener policy used elsewhere in
  the product.

The physically-qualified modem/runtime closure under `src/ywd1278/` is not
modified by the monitor feature. Instrumentation is composed in the product
layer under `src/ywdtnc/` around the existing qualified boundaries.

## Configuration

New configurations include:

```toml
[monitor]
enabled = true
listen = "127.0.0.1"
port = 8002
allow_wildcard_bind = false
```

Installed configurations created before the monitor feature remain valid. If
`[monitor]` is absent, the runtime treats it as disabled and defaults its latent
endpoint to `127.0.0.1:8002`.

The guided installer offers to add the loopback-only block to an older config
without rewriting the existing radio, KISS, AGW, or packet settings. If an
older `ywd-packetlog.service` is already enabled or running, that migration
prompt defaults to yes so the logger can move from KISS sniffing to first-class
events.

## Event model

Every record contains:

```json
{"schema":1,"seq":42,"ts":"2026-09-10T15:00:00.000Z","event":"rx.frame"}
```

`seq` is a monotonically increasing sequence number for the current `ywd-tncd`
process. It is not persisted across daemon restarts. `ts` is UTC ISO-8601 with
millisecond precision.

The current event names are:

```text
rx.frame

tx.submitted
tx.queued
tx.channel_clear
tx.dispatched
tx.complete

tx.rejected
tx.timeout
tx.failed
```

For decoded AX.25 frames the event includes, when available:

```text
source
destination
path
frame_class
frame_type
control
poll_final
ns
nr
pid
info_text
info_hex
frame_bytes
frame_hex
```

A repeated digipeater in `path` is suffixed with `*`, matching conventional
packet-monitor notation.

### TX lifecycle meaning

The successful TX sequence is intentionally tied to existing product
boundaries:

```text
application DATA
  -> tx.submitted
  -> admission succeeds
  -> tx.queued
  -> qualified CSMA reaches READY
  -> tx.channel_clear
  -> selector burst accepted by the existing modem-facing TX broker
  -> tx.dispatched
  -> existing half-duplex lifecycle proves RF idle and restores RX
  -> tx.complete
```

`tx.channel_clear` therefore means the already-qualified channel-access policy
has reached READY; it is not inferred from application timing.

`tx.dispatched` means the existing downstream broker/modem submission returned
successfully. It does not replace the later RF-idle proof.

`tx.complete` is emitted only after the existing half-duplex lifecycle returns,
which means the TX path has completed its normal RF-idle wait and RX restoration
checks.

Terminal alternatives are explicit:

- `tx.rejected` — DATA was rejected before admission, for example because
  product TX is disabled or admission failed;
- `tx.timeout` — a queued request expired under the bounded channel-access
  lifetime;
- `tx.failed` — a request reached the downstream lifecycle but an exception
  occurred. The qualified path retains its existing no-automatic-retry
  semantics.

TX events that have entered the admission queue carry a `request_id` so stages
for one request can be correlated. They also carry the captured KISS parameter
generation and TXDELAY/PERSIST/SLOTTIME values. `tx.channel_clear` carries the
RSSI sample associated with the READY observation, and `tx.dispatched` includes
selector-burst receipt metadata when available.

## Watching the raw stream

On the TNC host:

```bash
nc 127.0.0.1 8002
```

This is a receive-only observation socket. Sending bytes to it has no protocol
meaning and does not affect the TNC.

## Built-in packet logger

The product also ships `ywd-packetlog`, a reconnecting monitor client, plus a
systemd unit:

```text
ywd-packetlog.service
```

The service is installed but is not enabled automatically unless the operator
explicitly chooses packet logging during guided setup or an already-enabled
older service is being migrated.

Enable it manually with:

```bash
sudo systemctl enable --now ywd-packetlog.service
```

Useful status and live output commands are:

```bash
sudo systemctl status ywd-packetlog.service
sudo journalctl -u ywd-packetlog.service -f
```

The logger writes two daily files:

```text
/var/log/ywd-packetlog/YYYY-MM-DD.log
/var/log/ywd-packetlog/YYYY-MM-DD.jsonl
```

The `.log` file is human-readable. The `.jsonl` file preserves each schema-1
event object for later analysis, dashboards, statistics, or telemetry tools.

The human log uses compact packet-monitor notation for connected-mode control
fields, including examples such as `RR6-`, `SABM+`, and `I35+`:

```text
2026-09-10 08:24:31.517 RX fm KE6CHO-5 to KJ6YWD-11 ctl RR6- len=0
2026-09-10 08:24:42.101 TX QUEUED #17 fm KJ6YWD-11 to KJ6YWD-5 ctl SABM+ len=0
2026-09-10 08:24:42.304 TX CHANNEL-CLEAR #17 fm KJ6YWD-11 to KJ6YWD-5 ctl SABM+ len=0 raw_rssi=143
2026-09-10 08:24:42.612 TX DISPATCHED #17 fm KJ6YWD-11 to KJ6YWD-5 ctl SABM+ len=0 selectors=2840
2026-09-10 08:24:43.091 TX COMPLETE #17 fm KJ6YWD-11 to KJ6YWD-5 ctl SABM+ len=0
```

Exact frame/control fields depend on the AX.25 traffic observed.

## Host qualification

Host tests cover:

- monitor NDJSON framing and live-only behavior;
- ignored client input / absence of a monitor transmit API;
- bounded non-blocking subscriber fan-out;
- backward-compatible configuration loading;
- safe loopback defaults and listener bind validation;
- human plus lossless JSONL persistence;
- successful `queued -> channel_clear -> dispatched -> complete` ordering;
- terminal timeout and downstream-failure ordering;
- installer migration from the earlier KISS-sniffing packet logger;
- shell syntax and observation-only behavior of the physical gate.

The normal CI host checks remain hardware inert: they do not open the modem
UART, touch GPIO, write flash, or transmit RF. Runtime-equivalence CI also
continues to require the migrated `src/ywd1278/` closure and HAT support to be
byte-identical to the frozen qualified source.

## Physical qualification gate

The feature is not considered physically qualified until it is exercised on a
real Pi/HAT while a real packet application is using TCP KISS.

After installing the host-qualified monitor-events checkpoint, verify that
`ywd-mmdvm-tnc.service` and `ywd-packetlog.service` are running, then launch:

```bash
sudo bash /opt/ywd-mmdvm-tnc/source/scripts/monitor-events-physical.sh
```

The gate itself never sends a KISS frame and never requests RF transmission. It
only snapshots the logger and waits for the operator to generate one ordinary
packet exchange from the already-running application.

For the YWDXR/XRouter qualification, the requested stimulus is one direct AX.25
connection from the XRouter sysop console:

```text
C 1 !KJ6YWD-5
```

Wait for YWDNOD to answer and reach its prompt, disconnect cleanly, then return
to the physical-gate terminal and press Enter.

The analyzer considers only monitor records written after its baseline. A pass
requires at least one real `rx.frame` and one request with this ordered sequence:

```text
tx.submitted
  > tx.queued
  > tx.channel_clear
  > tx.dispatched
  > tx.complete
```

It additionally requires `raw_rssi` at channel-clear, a positive
`selector_count` at dispatch, valid schema-1 JSONL records, and no `tx.failed`
for the qualifying request. Success ends with markers including:

```text
MONITOR_EVENTS_PHYSICAL_GATE=PASS
LOGGER_JSONL_VALID=PASS
TX_AUTOMATIC_RETRY_ADDED=NO
```

Until those markers are obtained from the real station, the monitor event
feature remains host-qualified only.
