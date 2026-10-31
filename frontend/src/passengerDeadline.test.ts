import { afterEach, describe, expect, it } from 'vitest';
import {
  LIVE_PASSENGER_HORIZON_MINUTES,
  parsePassengerDeadlineUtc,
  passengerDeadlineUtcInput,
} from './passengerDeadline';

const originalTimezone = process.env.TZ;
afterEach(() => {
  if (originalTimezone === undefined) delete process.env.TZ;
  else process.env.TZ = originalTimezone;
});

describe('live passenger UTC deadlines', () => {
  const epoch = '2026-10-31T00:00:00+02:00';

  it('edits an actual calendar date after the operation has run for two weeks', () => {
    const now = 14 * 1440 + 600;
    const deadline = now + 2 * 1440 + 37;
    expect(passengerDeadlineUtcInput(epoch, deadline)).toBe('2026-11-16T08:37');
    expect(parsePassengerDeadlineUtc('2026-11-16T08:37', epoch, now)).toBe(deadline);
  });

  it('accepts the current server minute and exactly seven days ahead, but rejects outside', () => {
    const now = 20 * 1440 + 120;
    for (const minute of [now, now + LIVE_PASSENGER_HORIZON_MINUTES]) {
      expect(parsePassengerDeadlineUtc(passengerDeadlineUtcInput(epoch, minute), epoch, now)).toBe(
        minute,
      );
    }
    expect(() =>
      parsePassengerDeadlineUtc(passengerDeadlineUtcInput(epoch, now - 1), epoch, now),
    ).toThrow('current server time');
    expect(() =>
      parsePassengerDeadlineUtc(
        passengerDeadlineUtcInput(epoch, now + LIVE_PASSENGER_HORIZON_MINUTES + 1),
        epoch,
        now,
      ),
    ).toThrow('seven days');
  });

  it('preserves an old deadline for review instead of silently shifting it into range', () => {
    expect(passengerDeadlineUtcInput(epoch, 60)).toBe('2026-10-30T23:00');
    expect(() => parsePassengerDeadlineUtc('2026-10-30T23:00', epoch, 8 * 1440)).toThrow(
      'current server time',
    );
  });

  it('allows an early first-day deadline without an inherited 10am minimum', () => {
    expect(parsePassengerDeadlineUtc('2026-10-30T22:00', epoch, 0)).toBe(0);
    expect(parsePassengerDeadlineUtc('2026-10-31T01:15', epoch, 60)).toBe(195);
  });

  it.each(['Europe/Bucharest', 'America/New_York', 'Asia/Tokyo'])(
    'uses exact UTC instants across a DST transition regardless of browser zone %s',
    (timezone) => {
      process.env.TZ = timezone;
      const transitionEpoch = '2026-10-24T00:00:00+03:00';
      const text = passengerDeadlineUtcInput(transitionEpoch, 1740);
      expect(text).toBe('2026-10-25T02:00');
      expect(parsePassengerDeadlineUtc(text, transitionEpoch, 1440)).toBe(1740);
    },
  );

  it('crosses month and year boundaries without operation-day arithmetic', () => {
    const yearEpoch = '2026-12-20T00:00:00+02:00';
    const now = 11 * 1440;
    expect(passengerDeadlineUtcInput(yearEpoch, 12 * 1440 + 150)).toBe('2027-01-01T00:30');
    expect(parsePassengerDeadlineUtc('2027-01-01T00:30', yearEpoch, now)).toBe(17430);
  });

  it.each([
    '',
    '2026-11-01',
    '2026-02-30T12:30',
    '2026-11-01T24:00',
    '2026-11-01T12:60',
    '2026-11-01T12:30:01',
    '2026-11-01T12:30Z',
    '2026-11-01T12:30+02:00',
  ])('rejects invalid or non-minute form text %j before it can become a deadline', (value) => {
    expect(() => parsePassengerDeadlineUtc(value, epoch, 0)).toThrow();
  });

  it('does not guess an unzoned epoch or round away sub-minute source precision', () => {
    expect(() => parsePassengerDeadlineUtc('2026-11-01T12:30', '2026-10-31T00:00:00', 0)).toThrow(
      'operation time',
    );
    expect(() => parsePassengerDeadlineUtc('2026-11-01T12:30', '2026-10-31T00:00:30Z', 0)).toThrow(
      'exact operation minute',
    );
  });
});
