import { describe, expect, it } from 'vitest';
import {
  MANIFEST_TEMPLATE,
  inspectManifest,
  supplierStationRoute,
  utcInputToIso,
} from './liveData';
import { formatOperationTime } from './api';

describe('live input boundaries', () => {
  it('keeps supplier station names distinct from airport geography', () => {
    expect(
      supplierStationRoute({
        departure_station: { name: 'Gara de Nord' },
        arrival_station: { name: 'Sibiu Central' },
      }),
    ).toBe('Gara de Nord → Sibiu Central');
    expect(supplierStationRoute({ departure_station: { name: '  Main coach stop  ' } })).toBe(
      'Departure station: Main coach stop',
    );
    expect(supplierStationRoute({ origin: 'OTP', destination: 'SBZ' })).toBeNull();
    expect(supplierStationRoute(null)).toBeNull();
  });
  it('provides a header-only template and rejects missing fields and empty manifests', () => {
    expect(MANIFEST_TEMPLATE.trim().split('\n')).toHaveLength(1);
    expect(inspectManifest(MANIFEST_TEMPLATE).error).toContain('Add passenger');
    expect(inspectManifest('name,origin\nAda,CRA').error).toContain('Missing CSV columns');
    expect(inspectManifest(MANIFEST_TEMPLATE + 'record\n').rowCount).toBe(1);
  });
  it('turns explicitly UTC form dates into zoned values and rejects invalid dates', () => {
    expect(utcInputToIso('2026-10-31T12:30')).toBe('2026-10-31T12:30:00.000Z');
    for (const value of ['', '2026-02-30T12:30', '2026-10-31', '2026-10-31T25:15'])
      expect(() => utcInputToIso(value)).toThrow();
  });
  it('displays the actual Bucharest date across the operation day boundary', () => {
    expect(formatOperationTime('2026-10-31T00:00:00+02:00', 1440)).toContain('01 Nov');
    expect(formatOperationTime('invalid', 60)).toBe('Time unavailable');
  });
});
