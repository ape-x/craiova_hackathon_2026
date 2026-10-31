import { describe, expect, it } from 'vitest';
import { diversionChoice } from './scenarioSelection';
import type { Airport } from './types';

const airport = (id: string, available = true, eligible = true): Airport => ({
  id,
  code: id,
  name: id,
  lat: 0,
  lon: 0,
  available,
  diversion_eligible: eligible,
});

describe('live diversion choices', () => {
  it('excludes hotels and coach stations even if a malformed node advertises diversion eligibility', () => {
    const choice = diversionChoice(
      [
        { ...airport('HOTEL'), kind: 'hotel' },
        { ...airport('STOP'), kind: 'station' },
        airport('OTP'),
      ],
      'HOTEL',
    );
    expect(choice.airports.map((node) => node.id)).toEqual(['OTP']);
    expect(choice.selectedId).toBe('OTP');
  });
  it('keeps the operator selection while that airport is eligible', () => {
    const choice = diversionChoice([airport('OTP'), airport('SBZ')], 'SBZ');
    expect(choice.selectedId).toBe('SBZ');
  });

  it('replaces a closed destination with the same eligible value displayed by the selector', () => {
    const airports = [airport('OTP'), airport('SBZ')];
    expect(diversionChoice(airports, 'OTP').selectedId).toBe('OTP');
    const updated = airports.map((item) =>
      item.id === 'OTP' ? { ...item, available: false } : item,
    );
    const choice = diversionChoice(updated, 'OTP');
    expect(choice.airports.map((item) => item.id)).toEqual(['SBZ']);
    expect(choice.selectedId).toBe(choice.airports[0].id);
    expect(airports[0].available).toBe(true);
  });

  it('provides no submission destination when remaining airports are unavailable or ineligible', () => {
    const choice = diversionChoice([airport('OTP', false), airport('SBZ', true, false)], 'OTP');
    expect(choice.selectedId).toBe('');
    expect(choice.airports).toEqual([]);
  });
});
