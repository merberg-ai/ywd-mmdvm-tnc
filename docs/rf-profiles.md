# RF operating profiles

YWD-MMDVM-TNC keeps RF selection deliberately bounded while allowing the modem
to be reused underneath different KISS applications.

## Named profiles

| Profile | Frequency | TX power | TX | Qualification status |
| --- | ---: | ---: | --- | --- |
| `packet` | 145.050 MHz | 200/255 | enabled | physically qualified historical packet profile |
| `aprs` | 144.390 MHz | 200/255 | enabled | explicitly permitted APRS operational profile; not separately physically qualified |
| `rx-only` | current frequency | current power value | disabled | receive-only |

Arbitrary transmit frequencies and power values remain rejected by the product
configuration validator and the product-level SET_FREQ request builder.

## Switching profiles

After installing a build that contains the profile tool:

```bash
sudo ywd-tnc-profile packet
sudo ywd-tnc-profile aprs
sudo ywd-tnc-profile rx-only
ywd-tnc-profile status
```

`packet` selects 145.050 MHz, power 200, and enables TX. `aprs` selects 144.390
MHz, power 200, and enables TX. `rx-only` leaves the current frequency and power
value in place and disables KISS/AGW data transmission at the modem ingress.

A profile change:

1. reads the existing `/etc/ywd-mmdvm-tnc/config.toml`;
2. changes only `radio.frequency_mhz`, `radio.tx_power`, and `radio.tx_enabled`;
3. validates the complete candidate configuration before installing it;
4. saves the previous file as `/etc/ywd-mmdvm-tnc/config.toml.bak`;
5. atomically replaces the configuration;
6. restarts `ywd-mmdvm-tnc.service`;
7. restores the previous configuration and attempts to restart it if the new
   service start fails.

For host-only testing or maintenance, `--no-restart` suppresses the systemd
restart. `--config` and `--service` can point the tool at alternate paths/names.

## Application handoff

Only one application should normally be given authority to originate packets
through a particular KISS TNC at a time. Stop or disconnect the old KISS client,
switch the RF profile if necessary, then start the next application. The TNC
service itself remains the single owner of the HAT UART.

The frozen YWD-1278 submodule is not modified by this feature. Its historical
145.050 MHz qualification helper remains intact; the additional bounded APRS
SET_FREQ operation exists only in the YWD-MMDVM-TNC product layer.
