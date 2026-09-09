"""YWD-MMDVM-TNC modem-only product layer."""

__version__ = "0.1.0a15"

PRODUCT_TARGET = "mmdvm-hs-hat-stm32f103-simplex-14.7456-adf7021"
QUALIFIED_FIRMWARE_IDENTITY = (
    "MMDVM_HS_Hat-YWD-1278-AX25R4-v0.1.0-alpha1 14.7456MHz "
    "ADF7021 FW based on CA6JAU GitID #7ff74ed"
)

# Historical physical TX qualification. Keep these constants immutable so the
# evidence for the 145.050 MHz / power-200 qualification remains unambiguous.
QUALIFIED_TX_FREQUENCY_HZ = 145_050_000
QUALIFIED_TX_POWER = 200

# Additional product-level operational profile. This is explicitly permitted
# for APRS application interoperability, but is not represented as historical
# physical qualification evidence.
APRS_TX_FREQUENCY_HZ = 144_390_000
APRS_TX_POWER = 200

PERMITTED_TX_PROFILES = frozenset(
    {
        (QUALIFIED_TX_FREQUENCY_HZ, QUALIFIED_TX_POWER),
        (APRS_TX_FREQUENCY_HZ, APRS_TX_POWER),
    }
)

QUALIFIED_CORE_COMMIT = "c28c46c3478d7931af611923c92cd8f692a00858"
QUALIFIED_CORE_TREE = "9c06dea088a30782674404f43d964b8317c128a3"
