/*
 * SPDX-License-Identifier: GPL-2.0-only
 *
 * YWD-MMDVM AX25-2 receive-feasibility raw slicer capture.
 */

#include "AX25AFSKRX.h"

CAX25AFSKRX::CAX25AFSKRX() :
m_buffer(),
m_head(0U),
m_tail(0U),
m_partial(0U),
m_partialBits(0U),
m_active(false),
m_samples(0U),
m_droppedBytes(0U)
{
}

void CAX25AFSKRX::reset()
{
  m_head = 0U;
  m_tail = 0U;
  m_partial = 0U;
  m_partialBits = 0U;
  m_samples = 0U;
  m_droppedBytes = 0U;
}

void CAX25AFSKRX::start()
{
  m_active = true;
}

void CAX25AFSKRX::stop()
{
  m_active = false;
}

bool CAX25AFSKRX::active() const
{
  return m_active;
}

void CAX25AFSKRX::sample(uint8_t bit)
{
  if (!m_active)
    return;

  m_samples++;

  if ((bit & 0x01U) != 0U)
    m_partial |= uint8_t(0x80U >> m_partialBits);

  m_partialBits++;
  if (m_partialBits < 8U)
    return;

  uint16_t next = uint16_t(m_head + 1U);
  if (next >= BUFFER_SIZE)
    next = 0U;

  if (next == m_tail) {
    if (m_droppedBytes < 65535U)
      m_droppedBytes++;
  } else {
    m_buffer[m_head] = m_partial;
    m_head = next;
  }

  m_partial = 0U;
  m_partialBits = 0U;
}

uint16_t CAX25AFSKRX::available() const
{
  const uint16_t head = m_head;
  const uint16_t tail = m_tail;
  if (head >= tail)
    return uint16_t(head - tail);
  return uint16_t(BUFFER_SIZE - tail + head);
}

uint8_t CAX25AFSKRX::read(uint8_t* out, uint8_t maxLength)
{
  if (out == 0 || maxLength == 0U)
    return 0U;

  uint8_t count = 0U;
  while (count < maxLength) {
    const uint16_t head = m_head;
    uint16_t tail = m_tail;
    if (tail == head)
      break;

    out[count++] = m_buffer[tail];
    tail++;
    if (tail >= BUFFER_SIZE)
      tail = 0U;
    m_tail = tail;
  }

  return count;
}

uint32_t CAX25AFSKRX::samples() const
{
  return m_samples;
}

uint16_t CAX25AFSKRX::droppedBytes() const
{
  return m_droppedBytes;
}
