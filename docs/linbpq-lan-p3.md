# P3 — LinBPQ over LAN TCP KISS

P3 adds an explicitly authorized IPv4 wildcard KISS listener so LinBPQ can run
on another trusted LAN machine while YWD-MMDVM-TNC continues to own the HAT,
Bell-202 modem, channel access and RF path.

## Network safety

`0.0.0.0` is a bind address, not the address LinBPQ connects to. It causes the
TNC to listen on every IPv4 interface on the Raspberry Pi. The KISS protocol
has no authentication or encryption, so use this only on a trusted LAN and
restrict TCP/8001 with the host/network firewall if the Pi has any untrusted,
VPN, tunnel or public-facing interface.

Wildcard binding is fail-closed. It is rejected unless the same listener has:

```toml
allow_wildcard_bind = true
```

The shipped example remains loopback-only and TX-disabled.

## P3 staging configuration

The initial remote-LinBPQ gate used the already-qualified 145.050 MHz radio
profile and changed only the KISS listener while keeping persistent RF TX off:

```toml
[radio]
device = "/dev/ttyAMA0"
frequency_mhz = 145.050
tx_power = 200
tx_enabled = false

[kiss]
enabled = true
listen = "0.0.0.0"
port = 8001
allow_wildcard_bind = true
```

AGW may remain on `127.0.0.1:8000`.

## LinBPQ port

On the separate LinBPQ host, set `IPADDR` to the Raspberry Pi's actual LAN IPv4
address. Do not use `0.0.0.0` there.

```text
PORT
 ID=YWD-MMDVM-TNC 145.050
 TYPE=ASYNC
 PROTOCOL=KISS
 IPADDR=<YWD_TNC_PI_LAN_IP>
 TCPPORT=8001
 KISSOPTIONS=NOPARAMS
 FRACK=7000
 RESPTIME=1000
 RETRIES=10
 MAXFRAME=2
 PACLEN=128
 TXDELAY=300
 SLOTTIME=100
 PERSIST=63
 FULLDUP=0
ENDPORT
```

`NOPARAMS` is intentional for qualification: LinBPQ does not send KISS
TXDELAY/PERSIST/SLOTTIME updates, so the already-qualified YWD-MMDVM-TNC
configuration remains authoritative.

## Initial physical P3 LAN RX gate

The staging gate is retained at:

```bash
sudo ./scripts/p3-lan-kiss-physical.sh
```

It requires 145.050 MHz, KISS on `0.0.0.0:8001`, explicit wildcard
authorization, a non-loopback established TCP KISS connection from the remote
LinBPQ host, and one normal live RF packet observed in LinBPQ while persistent
TX remains disabled.

## Full LinBPQ interoperability qualification

On 2026-09-07, exact product commit
`6d11491f3a498cda5f79cb6a605d062c41f7b9df` was then exercised with real
LinBPQ traffic over the LAN TCP KISS interface and the physically-qualified
145.050 MHz / power-200 RF path.

The live interoperability test passed in both directions:

- remote LinBPQ received live RF through YWD-MMDVM-TNC over TCP KISS;
- LinBPQ-originated traffic transmitted successfully through the same KISS/RF
  path;
- a connected-mode session to `KJ6YWD-5` successfully transferred a large
  node list without problems;
- an outbound connected-mode session to `RDG` via `YWDNOD` connected and
  operated as expected.

This is intentionally an interoperability qualification rather than a claim
that connected-mode state moved into YWD-MMDVM-TNC. LinBPQ remains responsible
for AX.25 connection state, acknowledgements, retries, routing, node behavior,
FRACK/MAXFRAME/PACLEN and related application policy. `ywd-tncd` remains the
single-owner modem/TNC boundary: TCP KISS ingress/egress, bounded TX admission,
channel access, Bell-202 RX/TX and half-duplex RF lifecycle.

The machine-readable qualification record is:

```text
qualification/p3-linbpq-lan-kiss-interoperability-2026-09-07.json
```

P3 therefore qualifies the intended real product composition:

```text
LinBPQ on another LAN host
        |
        | TCP KISS :8001
        v
YWD-MMDVM-TNC / ywd-tncd
        |
        v
qualified AX25R4 HAT
        |
        v
145.050 MHz packet RF
```
