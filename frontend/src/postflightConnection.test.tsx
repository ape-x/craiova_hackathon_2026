import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import PostflightConnectionDetails, {
  postflightConnectionEvidence,
} from './PostflightConnectionDetails';
import AirportTransferQuoteObservations from './AirportTransferQuoteObservations';
import { ItineraryView } from './Inspector';
import type {
  AirportTransferQuoteSummary,
  Offer,
  PostflightConnection,
  Resource,
  World,
} from './types';

const evidence: PostflightConnection = {
  source: 'operator_declared_processing_window',
  arrival_basis: 'supplier_schedule_not_observed',
  pickup_rule: 'max_window_start_ceil_arrival_plus_processing',
  incoming_resource_id: 'INBOUND1',
  incoming_resource_fingerprint: 'a'.repeat(64),
  source_arrival_at: '2030-11-02T09:00:30Z',
  airport_processing_minutes: 60,
  pickup_window_start: '2030-11-02T10:00:00Z',
  pickup_window_end: '2030-11-02T12:00:00Z',
  derived_pickup_at: '2030-11-02T10:01:00Z',
};
const render = (value: unknown, incomingResourceId?: string | null, pickupAt?: number) =>
  renderToStaticMarkup(
    <PostflightConnectionDetails
      value={value}
      incomingResourceId={incomingResourceId}
      pickupAt={pickupAt}
    />,
  );
describe('exact planned inbound connection evidence', () => {
  it('preserves source seconds and rounds only derived pickup up without claiming an observed landing', () => {
    expect(postflightConnectionEvidence(evidence)).toEqual(evidence);
    const html = render(evidence, 'INBOUND1', Date.parse(evidence.derived_pickup_at));
    expect(html).toContain('flight INBOUND1');
    expect(html).toContain('Supplier-scheduled arrival');
    expect(html).toContain('not an observed landing');
    expect(html).toContain('Operator arrival processing: 60 minutes');
    expect(html).toContain('Derived pickup');
    expect(html).toContain('Terminal, baggage collection and connection protection are unverified');
    expect(html).toContain('Vehicle unreserved; road arrival estimated');
  });
  it('allows an explicitly later window start but not arbitrary rescheduling for source policy', () => {
    const later = {
      ...evidence,
      pickup_window_start: '2030-11-02T11:00:00Z',
      derived_pickup_at: '2030-11-02T11:00:00Z',
    };
    expect(postflightConnectionEvidence(later)).toEqual(later);
    expect(
      postflightConnectionEvidence({ ...later, derived_pickup_at: '2030-11-02T11:01:00Z' }),
    ).toBeNull();
  });
  it.each([
    { arrival_basis: 'observed' },
    { incoming_resource_fingerprint: 'unknown' },
    { source_arrival_at: '2030-11-02T09:00:30' },
    { airport_processing_minutes: 0 },
    { airport_processing_minutes: 721 },
    { derived_pickup_at: '2030-11-02T10:00:00Z' },
    { derived_pickup_at: '2030-11-02T10:02:00Z' },
    { pickup_window_end: '2030-11-02T10:00:00Z' },
  ])('does not present inconsistent evidence as a connection %j', (change) => {
    const html = render({ ...evidence, ...change });
    expect(html).toContain('incomplete or inconsistent');
    expect(html).not.toContain('Planned connection at OTP');
  });
  it('rejects a different preceding flight and mismatched transfer departure', () => {
    expect(render(evidence, 'OTHER')).toContain('incomplete or inconsistent');
    expect(render(evidence, null)).toContain('incomplete or inconsistent');
    expect(render(evidence, 'INBOUND1', Date.parse(evidence.derived_pickup_at) + 60_000)).toContain(
      'incomplete or inconsistent',
    );
    expect(render(null)).toBe('');
  });
  it('checks conservative departure minutes relative to a non-minute epoch without inventing a different source pickup', () => {
    const epoch = '2030-11-01T00:00:30Z',
      departure = 2041;
    expect(Date.parse(epoch) + departure * 60_000).not.toBe(Date.parse(evidence.derived_pickup_at));
    const html = renderToStaticMarkup(
      <PostflightConnectionDetails
        value={evidence}
        departureSchedule={{ epoch, departure }}
        pickupAt={Date.parse(evidence.derived_pickup_at)}
      />,
    );
    expect(html).toContain('Planned connection at OTP');
    expect(html).not.toContain('incomplete or inconsistent');
    for (const minute of [2040, 2042]) {
      expect(
        renderToStaticMarkup(
          <PostflightConnectionDetails
            value={evidence}
            departureSchedule={{ epoch, departure: minute }}
          />,
        ),
      ).toContain('incomplete or inconsistent');
    }
    // A quote still needs its exact source pickup, not the app's later rounded instant.
    expect(
      renderToStaticMarkup(
        <PostflightConnectionDetails
          value={evidence}
          departureSchedule={{ epoch, departure }}
          pickupAt={Date.parse(epoch) + departure * 60_000}
        />,
      ),
    ).toContain('incomplete or inconsistent');
  });
  it('shows historical quoted bindings independently of whether a candidate became a proposal', () => {
    const quote = {
      group_id: 'G1',
      quote_id: 'Q1',
      quote_sequence: 1,
      pickup_at: evidence.derived_pickup_at,
      usable_until: '2030-11-01T12:02:00Z',
      state: 'ready',
      options: [],
      blockers: [],
      http_request_count: 2,
      arrival_estimated: true,
      supplier_booked: false,
      postflight_connection: evidence,
    } as AirportTransferQuoteSummary;
    const html = renderToStaticMarkup(
      <AirportTransferQuoteObservations
        quotes={[quote]}
        world={{ groups: [{ id: 'G1', name: 'Party' }] } as World}
      />,
    );
    expect(html).toContain('Historical native EUR prices');
    expect(html).toContain('flight INBOUND1');
    expect(html).toContain('not supplier expiry or current availability');
    expect(html).not.toContain('chosen');
    expect(html).not.toContain('<button');
  });
  it('renders the connection within the shared operator/passenger itinerary only after the exact inbound leg', () => {
    const resource = {
      id: 'CAB1',
      kind: 'bus',
      source: 'Bucharest.CAB',
      transport_mode: 'private_transfer',
      origin: 'OTP',
      destination: 'CRA',
      departure: 2041,
      arrival: 2241,
      provenance: { direction: 'after_flight_airport_to_airport', postflight_connection: evidence },
    } as unknown as Resource;
    const offer = {
      itinerary: {
        legs: [
          {
            resource_id: 'INBOUND1',
            kind: 'flight',
            origin: 'LHR',
            destination: 'OTP',
            departure: 1800,
            arrival: 1981,
            cost: 100,
          },
          {
            resource_id: 'CAB1',
            kind: 'bus',
            transport_mode: 'private_transfer',
            origin: 'OTP',
            destination: 'CRA',
            departure: 2041,
            arrival: 2241,
            cost: 200,
            postflight_connection: evidence,
          },
        ],
        total_cost: 300,
        arrival: 2241,
      },
    } as Offer;
    const world = {
      mode: 'live',
      epoch: '2030-11-01T00:00:00Z',
      resources: [resource],
      airports: [],
    } as unknown as World;
    expect(renderToStaticMarkup(<ItineraryView offer={offer} world={world} />)).toContain(
      'Planned connection at OTP',
    );
    world.epoch = '2030-11-01T00:00:30Z';
    const rounded = renderToStaticMarkup(<ItineraryView offer={offer} world={world} />);
    expect(rounded).toContain('Planned connection at OTP');
    expect(rounded).not.toContain('incomplete or inconsistent');
    offer.itinerary.legs[1].departure = 2042;
    expect(renderToStaticMarkup(<ItineraryView offer={offer} world={world} />)).toContain(
      'incomplete or inconsistent',
    );
    offer.itinerary.legs[1].departure = 2041;
    offer.itinerary.legs[0].resource_id = 'OTHER';
    expect(renderToStaticMarkup(<ItineraryView offer={offer} world={world} />)).toContain(
      'incomplete or inconsistent',
    );
  });
});
