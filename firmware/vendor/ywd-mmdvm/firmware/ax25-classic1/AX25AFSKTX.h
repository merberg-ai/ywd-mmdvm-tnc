/*
 * SPDX-License-Identifier: GPL-2.0-only
 *
 * YWD-MMDVM experimental Bell-202/AX.25 transmit waveform engine.
 */

#if !defined(AX25AFSKTX_H)
#define AX25AFSKTX_H

#include <stdint.h>

class CAX25AFSKTX {
public:
  CAX25AFSKTX();

  uint8_t writeSelectors(const uint8_t* packed, uint8_t length, uint16_t bitCount);
  void process();
  void abort();

  bool busy() const;
  uint16_t remaining() const;
  uint8_t keyups() const;
  uint16_t samplesQueued() const;

private:
  static const uint16_t MAX_SELECTORS = 1920U;
  static const uint8_t MAX_PACKED = 240U;
  static const uint8_t SAMPLES_PER_SYMBOL = 16U;
  static const uint32_t MARK_INC = 0x10000000UL;  // 1200 Hz at 19.2 ksample/s
  static const uint32_t SPACE_INC = 0x1D555555UL; // 2200 Hz at 19.2 ksample/s

  uint8_t m_data[MAX_PACKED];
  uint16_t m_bitCount;
  uint16_t m_symbol;
  uint8_t m_sample;
  uint32_t m_phase;
  bool m_active;
  uint8_t m_keyups;
  uint16_t m_samplesQueued;

  bool selector(uint16_t index) const;
};

#endif
