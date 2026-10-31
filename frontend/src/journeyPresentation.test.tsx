import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { ItineraryView } from './Inspector';
import { flightSegments, sourcedArrival } from './journeyPresentation';
import type { Offer, Resource } from './types';

const segments = [
  {
    mode: 'flight',
    origin: 'LHR',
    destination: 'ZRH',
    carrier: 'Example Air',
    flight_number: '101',
    departure_at: '2026-11-01T09:00:00Z',
    departure_local: '2026-11-01T09:00:00',
    arrival_at: '2026-11-01T10:55:00Z',
    arrival_local: '2026-11-01T11:55:00',
  },
  {
    mode: 'flight',
    origin: 'ZRH',
    destination: 'OTP',
    carrier: 'Example Air',
    flight_number: '202',
    departure_at: '2026-11-01T20:55:00Z',
    departure_local: '2026-11-01T21:55:00',
    arrival_at: '2026-11-01T23:15:00Z',
    arrival_local: '2026-11-02T01:15:00',
  },
];
const resource: Resource = {
  id: 'Q1',
  kind: 'flight',
  name: 'Example Air',
  origin: 'LHR',
  destination: 'OTP',
  departure: 1980,
  arrival: 2835,
  capacity: 2,
  accessible: false,
  available: true,
  cost_per_person: 0,
  room_capacity: 0,
  vehicle_id: null,
  status: 'quoted',
  source: 'Nuitee',
  provenance: { segments },
};
const offer = {
  id: 'O1',
  itinerary: {
    legs: [
      {
        resource_id: 'Q1',
        kind: 'flight',
        origin: 'LHR',
        destination: 'OTP',
        departure: 1980,
        arrival: 2835,
      },
    ],
    arrival: 2835,
  },
} as Offer;

describe('sourced passenger journey presentation', () => {
  it('shows both actual flight legs, the ten-hour connection and endpoint-local dates', () => {
    const html = renderToStaticMarkup(
      <ItineraryView
        offer={offer}
        world={{ mode: 'live', epoch: '2026-10-31T00:00:00+02:00', resources: [resource] }}
      />,
    );
    expect(html).toContain('LHR');
    expect(html).toContain('ZRH');
    expect(html).toContain('OTP');
    expect(html).toContain('10 h');
    expect(html).toContain('connection at');
    expect(html).toContain('01 Nov 2026, 09:00 · local at LHR (UTC+00:00)');
    expect(html).toContain('01 Nov 2026, 11:55 · local at ZRH (UTC+01:00)');
    expect(sourcedArrival(offer, [resource])).toBe('02 Nov 2026, 01:15 · local at OTP (UTC+02:00)');
    expect(html).not.toContain('Europe/Bucharest operations time');
  });
  it('labels UTC explicitly when the supplier has no local clock, without assuming Bucharest', () => {
    const value = {
      ...resource,
      provenance: {
        segments: segments.map(
          ({ departure_local: _departure, arrival_local: _arrival, ...segment }) => segment,
        ),
      },
    };
    expect(flightSegments(value)[0].departure.label).toBe('01 Nov 2026, 09:00 UTC · LHR');
  });
  it('rejects broken geographic or temporal connections instead of displaying a complete itinerary', () => {
    expect(
      flightSegments({
        ...resource,
        provenance: { segments: [segments[0], { ...segments[1], origin: 'CDG' }] },
      }),
    ).toEqual([]);
    expect(
      flightSegments({
        ...resource,
        provenance: {
          segments: [segments[0], { ...segments[1], departure_at: '2026-11-01T10:00:00Z' }],
        },
      }),
    ).toEqual([]);
    expect(flightSegments({ ...resource, provenance: { segments: [segments[0]] } })).toEqual([]);
  });
  it('does not treat malformed dates or impossible offsets as a verified local time', () => {
    const malformed = {
      ...resource,
      provenance: {
        segments: [{ ...segments[0], departure_local: '2026-02-30T09:00:00' }, segments[1]],
      },
    };
    expect(flightSegments(malformed)[0].departure.label).toContain('09:00 UTC');
    const wrongDay = {
      ...resource,
      provenance: {
        segments: [{ ...segments[0], departure_local: '2026-11-02T09:00:00' }, segments[1]],
      },
    };
    expect(flightSegments(wrongDay)[0].departure.label).toContain('09:00 UTC');
  });
  it('falls back to explicitly named operations time for operator-supplied summaries without flight details', () => {
    const html = renderToStaticMarkup(
      <ItineraryView
        offer={offer}
        world={{ mode: 'live', epoch: '2026-10-31T00:00:00+02:00', resources: [] }}
      />,
    );
    expect(html).toContain('Europe/Bucharest operations time');
    expect(html).not.toContain('connection at');
  });
});
