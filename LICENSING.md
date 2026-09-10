# Licensing and source provenance

YWD-MMDVM-TNC host-side code is licensed under the **GNU General Public License version 2 or, at your option, any later version (GPL-2.0-or-later)**. The complete GNU GPL version 2 license text is provided in [`LICENSE`](LICENSE).

## Host runtime

The installed product runtime is carried directly in this repository:

- `src/ywdtnc/` contains the YWD-MMDVM-TNC product daemon, configuration, RF-profile enforcement, network adapters, and product composition;
- `src/ywd1278/` contains the frozen packet-modem support runtime used by the product, including AX.25 framing, Bell-202 RX/TX, modem/UART ownership, KISS framing/control/admission, CSMA/channel access, and sustained TNC runtime support.

The historical `vendor/ywd-1278` git submodule remains pinned to YWD-1278 commit:

`c28c46c3478d7931af611923c92cd8f692a00858`

(tree `9c06dea088a30782674404f43d964b8317c128a3`).

That submodule is retained as an exact **qualification and source-provenance anchor** and is verified by the installer/host contract. The installed runtime does not import its live implementation from the submodule.

The historical vendor repository also contains node, BBS, console, and other higher-level code because those components existed in the qualification source tree. YWD-MMDVM-TNC does not compose or expose those application layers.

## Firmware

The RF-critical firmware is derived from MMDVM_HS and remains subject to the applicable upstream GPL licensing, copyright notices, and attribution.

The repository carries the engineering inputs and deterministic build tooling needed to reproduce the accepted qualified firmware under `firmware/`, including the frozen MMDVM_HS-derived inputs in `firmware/vendor/ywd-mmdvm/`. The pinned historical YWD-1278 source remains part of the qualification/provenance record, but the current production firmware build is repository-owned and does not execute the historical YWD-1278 firmware builder.

Redistribution of firmware binaries must retain the corresponding source and notices required by the applicable upstream licenses. See the source files and retained upstream notices for component-specific copyright and licensing information.

## Qualified firmware identity

The accepted firmware intentionally retains its historical runtime identity:

`MMDVM_HS_Hat-YWD-1278-AX25R4-v0.1.0-alpha1 14.7456MHz ADF7021 FW based on CA6JAU GitID #7ff74ed`

Accepted qualified firmware SHA-256:

`b06fcbf0baa36e865198091cee27c66e1624ef08117ee685253a7a5613c7c616`

The historical identity string is intentionally preserved because it is part of the exact byte-level physical qualification evidence; changing it would create a different firmware binary.
