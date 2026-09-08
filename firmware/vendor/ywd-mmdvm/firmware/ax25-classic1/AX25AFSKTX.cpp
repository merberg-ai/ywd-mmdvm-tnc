/*
 * SPDX-License-Identifier: GPL-2.0-only
 *
 * YWD-MMDVM experimental Bell-202/AX.25 transmit waveform engine.
 *
 * The Pi supplies a packed sequence of Bell-202 tone selectors at 1200 baud.
 * This class expands each selector to sixteen 1-bit samples at 19.2 ksample/s.
 * The ADF7021 runs in a dedicated 2FSK mode and converts that one-bit waveform
 * into FM deviation. A normal FM receiver should therefore recover an audio
 * waveform whose fundamental follows the 1200/2200 Hz Bell-202 tones.
 */

#include "Config.h"
#include "Globals.h"
#include "AX25AFSKTX.h"

#include <cstring>

CAX25AFSKTX::CAX25AFSKTX() :
m_data(),
m_bitCount(0U),
m_symbol(0U),
m_sample(0U),
m_phase(0U),
m_active(false),
m_keyups(0U),
m_samplesQueued(0U)
{
}

uint8_t CAX25AFSKTX::writeSelectors(const uint8_t* packed, uint8_t length, uint16_t bitCount)
{
  if (packed == NULL || length == 0U || bitCount == 0U || bitCount > MAX_SELECTORS)
    return 4U;

  const uint16_t expected = uint16_t((bitCount + 7U) / 8U);
  if (expected != length || length > MAX_PACKED)
    return 4U;

  if (m_active || m_tx)
    return 5U;

  ::memcpy(m_data, packed, length);
  m_bitCount = bitCount;
  m_symbol = 0U;
  m_sample = 0U;
  m_phase = 0U;
  m_keyups = 0U;
  m_samplesQueued = 0U;
  m_active = true;
  return 0U;
}

void CAX25AFSKTX::process()
{
  if (!m_active)
    return;

  // The stock CIO TX ring is 1024 bits and represents full vs empty with
  // head==tail plus a separate m_full flag.  The ISR clears m_full just before
  // advancing tail, which creates a tiny race where getSpace() can momentarily
  // report 1024 free slots for a nearly-full ring.  Filling the ring completely
  // makes that race easy to hit and CIO::write() does not report rejected puts.
  //
  // Classic-1 therefore never intentionally drives the ring to full.  Keep a
  // 256-bit reserve and refill toward 768-bit occupancy.  While TX is active the
  // ISR can only consume bits, so free space can increase after this snapshot;
  // it cannot invalidate the conservative refill budget below.
  static const uint16_t CIO_FIFO_RESERVE = 256U;
  static const uint16_t CIO_REFILL_MAX = 768U;

  uint16_t space = io.getSpace();
  if (space <= CIO_FIFO_RESERVE)
    return;

  uint16_t budget = uint16_t(space - CIO_FIFO_RESERVE);
  if (budget > CIO_REFILL_MAX)
    budget = CIO_REFILL_MAX;

  uint8_t samples[CIO_REFILL_MAX];
  uint16_t count = 0U;

  while (count < budget && m_active) {
    const uint32_t increment = selector(m_symbol) ? SPACE_INC : MARK_INC;
    samples[count++] = (m_phase & 0x80000000UL) != 0U ? 1U : 0U;
    m_phase += increment;

    m_sample++;
    if (m_sample >= SAMPLES_PER_SYMBOL) {
      m_sample = 0U;
      m_symbol++;
      if (m_symbol >= m_bitCount)
        m_active = false;
    }
  }

  if (count == 0U)
    return;

  const bool wasTX = m_tx;
  io.write(samples, count);
  if (!wasTX && m_tx && m_keyups < 255U)
    m_keyups++;

  const uint32_t queued = uint32_t(m_samplesQueued) + count;
  m_samplesQueued = queued > 65535U ? 65535U : uint16_t(queued);
}

void CAX25AFSKTX::abort()
{
  // Already queued CIO bits remain bounded and will drain; no new waveform
  // samples are generated after this point. Diagnostic counters remain intact
  // until the next accepted burst so the host can inspect the completed run.
  m_bitCount = 0U;
  m_symbol = 0U;
  m_sample = 0U;
  m_active = false;
}

bool CAX25AFSKTX::busy() const
{
  return m_active;
}

uint16_t CAX25AFSKTX::remaining() const
{
  return m_symbol < m_bitCount ? uint16_t(m_bitCount - m_symbol) : 0U;
}

uint8_t CAX25AFSKTX::keyups() const
{
  return m_keyups;
}

uint16_t CAX25AFSKTX::samplesQueued() const
{
  return m_samplesQueued;
}

bool CAX25AFSKTX::selector(uint16_t index) const
{
  return (m_data[index >> 3] & (0x80U >> (index & 7U))) != 0U;
}
