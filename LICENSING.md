# Licensing and source provenance

YWD-MMDVM-TNC host-side code is licensed under GPL-2.0-or-later.

The initial modem/RF core is consumed from the `vendor/ywd-1278` git submodule, pinned to YWD-1278 commit `c28c46c3478d7931af611923c92cd8f692a00858` (tree `9c06dea088a30782674404f43d964b8317c128a3`). Runtime code in `src/ywdtnc` deliberately imports only the packet-modem portions of that qualified lineage: AX.25 framing, Bell-202 RX/TX, MMDVM/YWD host protocol and UART ownership, KISS framing/control/admission, CSMA/channel access, and the sustained TNC runtime.

The vendor repository also contains node/BBS/console code because it is the historical qualification source. YWD-MMDVM-TNC does not compose or expose those layers.

The MMDVM_HS-derived firmware remains subject to its upstream GPL licensing, copyright notices, and attribution. The pinned vendor source and its `LICENSING.md` are authoritative for the exact firmware derivation and notices. Do not redistribute a firmware binary without retaining the corresponding source and required notices.

The initial qualified firmware runtime identity is:

`MMDVM_HS_Hat-YWD-1278-AX25R4-v0.1.0-alpha1 14.7456MHz ADF7021 FW based on CA6JAU GitID #7ff74ed`

Initial qualified firmware SHA-256:

`b06fcbf0baa36e865198091cee27c66e1624ef08117ee685253a7a5613c7c616`
