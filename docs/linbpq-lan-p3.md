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

## P3 RX-only TNC configuration

For the first LinBPQ integration gate, keep persistent RF TX disabled and change
only the KISS listener in `/etc/ywd-mmdvm-tnc/config.toml`:

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
ENDPORT
```

`NOPARAMS` is intentional for the qualification gate: LinBPQ does not send KISS
TXDELAY/PERSIST/SLOTTIME updates, so the already-qualified YWD-MMDVM-TNC
configuration remains authoritative.

## Physical P3 LAN RX gate

After installing the P3 checkpoint and editing the TNC config:

```bash
sudo ./scripts/p3-lan-kiss-physical.sh
```

The gate requires:

- 145.050 MHz;
- persistent TX disabled;
- KISS enabled on `0.0.0.0:8001`;
- explicit wildcard authorization;
- a non-loopback established TCP KISS connection from the remote LinBPQ host;
- one normal live RF packet observed in LinBPQ.

Success ends with:

```text
LINBPQ_REMOTE_TCP_ESTABLISHED=PASS
LINBPQ_LIVE_RX_CONFIRMED=YES
PERSISTENT_TX_ENABLED=NO
RF_DIRECTION=RX_ONLY
LINBPQ_KISS_TCP_LAN=PASS
YWD_TNC_P3_LAN_RX=PASS
```

P3 does not yet authorize persistent LinBPQ-originated RF TX. That becomes the
next physical gate after remote KISS RX is independently proven.
