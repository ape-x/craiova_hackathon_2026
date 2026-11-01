import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import TransferoScheduleDetails from './TransferoScheduleDetails';
import HotelResourcePrice from './HotelResourcePrice';
import { transferoEvidence } from './transferoData';
import { resourceFreshness } from './resourceFreshness';
import { transferReview } from './resourceLabels';
import { groundPath } from './maps/groundPaths';
import { GeographicLegend } from './maps/GoogleMaps3D';
import {
  costPresentation,
  EstimatedCostNote,
  PriceEvidenceDetails,
  privateTransfer,
} from './priceEvidence';
import type { CostEvidence, Itinerary, Resource, TransferoPriceEvidence, World } from './types';

const now = Date.parse('2026-11-01T12:00:00Z');
const price: TransferoPriceEvidence = {
  kind: 'transfero_ecb_reference',
  original: { amount: '260.00', currency: 'RON' },
  estimated_eur: { amount: '52.00', currency: 'EUR' },
  price_scope: 'requested_party',
  policy_id: 'ecb_reference_v1',
  settlement_guaranteed: false,
  revalidation_required: true,
  fx_reference: {
    source: 'European Central Bank',
    source_url: 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml',
    base_currency: 'EUR',
    rate_date: '2026-10-30',
    retrieved_at: '2026-11-01T12:00:00Z',
    as_of: '2026-11-01T12:00:00Z',
    age_calendar_days: 2,
    age_timezone: 'Europe/Berlin',
    max_age_days: 3,
    source_units_per_eur: '5.00',
  },
};
const resource = (departure = '2026-11-02T11:00:00Z'): Resource => ({
  id: 'TR1',
  name: 'Direct Aeroport van',
  kind: 'bus',
  source: 'Transfero',
  origin: 'OTP',
  destination: 'HP1',
  departure: 2100,
  arrival: 2280,
  capacity: 13,
  capacity_scope: 'source_reported_seats',
  available: true,
  status: 'quoted',
  accessible: false,
  cost_per_person: 0,
  room_capacity: 0,
  vehicle_id: null,
  requires_revalidation: true,
  booking_status: 'unbooked',
  eligible_group_ids: ['G1'],
  price_evidence: price,
  budget_total_cost: 52,
  quote_total_cost: null,
  quote_freshness: {
    basis: 'app_receipt',
    received_at: '2026-11-01T12:00:00Z',
    max_age_seconds: 120,
  },
  provenance: {
    transfero_option_id: 'OPT1',
    transfero_quote: {
      provider: 'Transfero',
      source_url: 'https://www.transfero.ro/cauta-bilete/',
      booking_supported: false,
      received_at: '2026-11-01T12:00:00Z',
      options: [
        {
          id: 'OPT1',
          operator: 'Direct Aeroport',
          vehicle_type: 'van',
          departure_at: departure,
          arrival_at: new Date(Date.parse(departure) + 3 * 3600000).toISOString(),
          timezone: 'Europe/Bucharest',
          timezone_basis: 'app_interpretation_of_source_local_clock',
          availability_status: 'quoted_not_reserved',
          available_seats: 13,
          supplier_valid_until: null,
          price: { amount: '260.00', currency: 'RON' },
          price_scope: 'requested_party',
          departure_stop: {
            name: 'Terminal SOSIRI / ARRIVALS',
            city: 'Aeroport Otopeni',
            source_url: 'https://direct-aeroport.ro/contact/',
          },
          arrival_stop: {
            name: 'Hotel Aro Palace',
            city: 'Brasov',
            source_url: 'https://direct-aeroport.ro/contact/',
          },
          policy: {
            required_advance_hours: 12,
            boarding_minutes: 20,
            airport_processing_minutes: 180,
            timing_basis: 'app_conservative_maximum_of_operator_and_attached_terms',
            schedule_basis: 'source_estimated_local_timetable',
            boarding_location_basis: 'operator_named_facility_exact_bay_confirmed_after_booking',
            source_url: 'https://direct-aeroport.ro/termeni-conditii-calatorie/',
          },
        },
      ],
    },
  },
});
const world = (item = resource()) =>
  ({
    mode: 'live',
    epoch: '2026-11-01T00:00:00Z',
    resources: [item],
    offers: [],
    reservations: [],
    airports: [
      { id: 'OTP', code: 'OTP', kind: 'airport', lat: 44.57, lon: 26.07 },
      { id: 'HP1', kind: 'hotel', name: 'Hotel Aro Palace', lat: 45.645, lon: 25.591 },
    ],
  }) as unknown as World;
const summary: CostEvidence = {
  kind: 'includes_ecb_reference',
  total_eur: '168.34',
  estimated_eur: '52.00',
  original_gbp: '0',
  original_ron: '260.00',
  policy_id: 'ecb_reference_v1',
  settlement_guaranteed: false,
  revalidation_required: true,
};
const itinerary = (evidence = price): Itinerary => ({
  legs: [
    {
      resource_id: 'TR1',
      kind: 'bus',
      origin: 'OTP',
      destination: 'HP1',
      departure: 2100,
      arrival: 2280,
      cost: 52,
      price_evidence: evidence,
    },
  ],
  arrival: 2280,
  total_cost: 168.34,
  cost_evidence: summary,
});

describe('Transfero scheduled transport evidence', () => {
  it('preserves native RON party fare and the server EUR estimate, without GBP/private-vehicle labels', () => {
    expect(privateTransfer(resource())).toBe(false);
    const html = renderToStaticMarkup(<PriceEvidenceDetails evidence={price} />);
    expect(html).toContain('260.00 RON for the requested party');
    expect(html).toContain('Approximately €52.00');
    expect(html).toContain('1 EUR = 5.00 RON');
    expect(html).not.toMatch(/GBP|per vehicle|Private transfer/);
    expect(costPresentation(itinerary()).amount).toBe('≈ €168.34');
    const note = renderToStaticMarkup(<EstimatedCostNote value={itinerary()} />);
    expect(note).toContain('260.00 RON');
    expect(note).not.toContain('£0.00');
    expect(note).toContain('not a guaranteed settlement');
    const missing = { ...summary };
    delete missing.original_ron;
    expect(costPresentation({ ...itinerary(), cost_evidence: missing }).amount).toBe(
      'Estimated total unavailable',
    );
    const mixed = renderToStaticMarkup(
      <EstimatedCostNote
        value={{ ...itinerary(), cost_evidence: { ...summary, original_gbp: '76.00' } }}
      />,
    );
    expect(mixed).toContain('£76.00');
    expect(mixed).toContain('260.00 RON');
  });
  it('fails closed on wrong original currency, FX metadata or price scope', () => {
    for (const evidence of [
      { ...price, original: { amount: '260.00', currency: 'GBP' } },
      { ...price, price_scope: 'per_vehicle' },
      { ...price, fx_reference: { ...price.fx_reference, source_units_per_eur: '0' } },
    ]) {
      const html = renderToStaticMarkup(
        <PriceEvidenceDetails evidence={evidence as TransferoPriceEvidence} />,
      );
      expect(html).toContain('EUR reference estimate unavailable');
      expect(html).not.toContain('Approximately');
    }
  });
  it('uses the earlier local receipt/advance cutoff with exact boundaries; missing source evidence never looks current', () => {
    expect(resourceFreshness(resource(), now + 120000).state).toBe('current');
    expect(resourceFreshness(resource(), now + 120001).state).toBe('stale');
    const near = resource('2026-11-02T00:01:00Z');
    expect(resourceFreshness(near, now + 60000).state).toBe('current');
    expect(resourceFreshness(near, now + 60001).label).toBe('Advance-notice window ended');
    for (const invalid of [
      { ...resource(), capacity: 12 },
      { ...resource(), provenance: {} },
      { ...resource(), valid_until: '2026-11-02T00:00:00Z' },
      {
        ...resource(),
        quote_freshness: {
          basis: 'app_receipt' as const,
          received_at: '2026-11-01T12:01:00Z',
          max_age_seconds: 120 as const,
        },
      },
    ])
      expect(resourceFreshness(invalid, now).state).toBe('unknown');
  });
  it('labels source vehicle type, reported seats and scheduled arrival, with only a reference connection on the map', () => {
    const item = resource();
    item.provenance = {
      ...item.provenance,
      google_route: { source: 'Google Routes', encoded_polyline: 'invented' },
    };
    expect(transferoEvidence(item)?.vehicleType).toBe('van');
    expect(groundPath(item, world(item))?.geometry).toBe('connection_only');
    const html = renderToStaticMarkup(<TransferoScheduleDetails resource={item} />);
    expect(html).toContain('Direct Aeroport · van · scheduled service');
    expect(html).toContain('13 seats reported at receipt');
    expect(html).toContain('scheduled arrival');
    expect(html).toContain('No seat');
    expect(html).not.toContain('per vehicle');
    const legend = renderToStaticMarkup(
      <GeographicLegend
        mode="live"
        selectedReview={transferReview(world(item), item, now)}
        selectedGeometry="connection_only"
        roadGeometry={{ sourced: true, connectionOnly: true }}
        hasObservations={false}
      />,
    );
    expect(legend).toContain('Source timetable');
    expect(legend).toContain('Connection only');
    expect(legend).not.toMatch(/Google Routes|Dated prediction|predicted road/);
  });
  it('renders the entire native EUR hotel stay including mandatory fees, without multiplying rooms or hiding incomplete pricing', () => {
    const hotel = {
      ...resource(),
      id: 'HQ1',
      source: 'Nuitee / LiteAPI',
      kind: 'hotel',
      quote_rooms: 2,
      quote_total_cost: 114.48,
      budget_total_cost: 116.34,
      provenance: {
        pricing: {
          status: 'complete',
          booking_total: { amount: '114.48', currency: 'EUR' },
          mandatory_total: { amount: '116.34', currency: 'EUR' },
          excluded_fee_totals: [{ amount: '1.86', currency: 'EUR' }],
          unresolved_reasons: [],
          payment_timing: 'unconfirmed',
        },
      },
    } as Resource;
    const html = renderToStaticMarkup(<HotelResourcePrice resource={hotel} />);
    expect(html).toContain('€116.34');
    expect(html).toContain('€114.48');
    expect(html).toContain('€1.86');
    expect(html).toContain('Entire requested stay and party');
    expect(html).toContain('2 requested rooms');
    expect(html).not.toContain('€232.68');
    const bad = renderToStaticMarkup(
      <HotelResourcePrice resource={{ ...hotel, budget_total_cost: 114.48 }} />,
    );
    expect(bad).toContain('incomplete or inconsistent');
    expect(bad).not.toContain('€114.48');
  });
});
