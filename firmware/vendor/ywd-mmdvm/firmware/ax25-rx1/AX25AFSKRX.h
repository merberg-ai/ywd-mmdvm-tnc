/*
 * SPDX-License-Identifier: GPL-2.0-only
 *
 * YWD-MMDVM AX25-2 receive-feasibility raw slicer capture.
 */

#if !defined(AX25AFSKRX_H)
#define AX25AFSKRX_H

#include <cstdint>

class CAX25AFSKRX {
public:
  CAX25AFSKRX();

  void     reset();
  void     start();
  void     stop();
  bool     active() const;

  // Called from the ADF7021 receive-clock ISR. Keep this path tiny.
  void     sample(uint8_t bit);

  uint16_t available() const;
  uint8_t  read(uint8_t* out, uint8_t maxLength);
  uint32_t samples() const;
  uint16_t droppedBytes() const;

private:
  enum { BUFFER_SIZE = 512U };

  volatile uint8_t  m_buffer[BUFFER_SIZE];
  volatile uint16_t m_head;
  volatile uint16_t m_tail;
  volatile uint8_t  m_partial;
  volatile uint8_t  m_partialBits;
  volatile bool     m_active;
  volatile uint32_t m_samples;
  volatile uint16_t m_droppedBytes;
};

#endif
