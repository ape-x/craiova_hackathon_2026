import { describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import Inspector, { ItineraryView } from './Inspector';
import { costPresentation, EstimatedCostNote, PriceEvidenceDetails } from './priceEvidence';
import { isAirportNode, locationKindLabel } from './locationNodes';
import { buildOverlays, cameraFor } from './maps/overlays';
import type { CostEvidence, Itinerary, Offer, Plan, PriceEvidence, Resource, World } from './types';

const price: PriceEvidence = {
  kind: 'apl_ecb_reference',
  original: { amount: '76', currency: 'GBP' },
  estimated_eur: { amount: '91.15', currency: 'EUR' },
  price_scope: 'per_vehicle',
  fx_reference: {
    source: 'European Central Bank',
    source_url: 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml',
    base_currency: 'EUR',
    rate_date: '2026-10-30',
    retrieved_at: '2026-10-31T20:00:00Z',
    as_of: '2026-10-31T20:00:00Z',
    age_calendar_days: 1,
    age_timezone: 'Europe/Berlin',
    max_age_days: 3,
    source_units_per_eur: '0.83380',
  },
  policy_id: 'ecb_reference_v1',
  settlement_guaranteed: false,
  revalidation_required: true,
};
const summary: CostEvidence = {
  kind: 'includes_ecb_reference',
  total_eur: '291.15',
  estimated_eur: '91.15',
  original_gbp: '76',
  policy_id: 'ecb_reference_v1',
  settlement_guaranteed: false,
  revalidation_required: true,
};
const transfer: Resource = {
  id: 'T1',
  kind: 'bus',
  name: 'Supplier saloon',
  origin: 'MP-1',
  destination: 'HP-1',
  departure: 3000,
  arrival: 3020,
  capacity: 2,
  accessible: false,
  available: true,
  cost_per_person: 0,
  room_capacity: 0,
  vehicle_id: null,
  status: 'quoted',
  source: 'Airport Pickups London',
  price_evidence: price,
  quote_total_cost: null,
  budget_total_cost: 91.15,
  capacity_scope: 'private_vehicle_quote',
  quote_freshness: {
    basis: 'app_receipt',
    received_at: '2026-10-31T20:00:00Z',
    max_age_seconds: 120,
  },
  requires_revalidation: true,
  valid_until: null,
};
const itinerary: Itinerary = {
  legs: [
    {
      resource_id: 'T1',
      kind: 'bus',
      origin: 'MP-1',
      destination: 'HP-1',
      departure: 3000,
      arrival: 3020,
      cost: 91.15,
      price_evidence: price,
    },
    {
      resource_id: 'H1',
      kind: 'hotel',
      origin: 'HP-1',
      destination: 'HP-1',
      departure: 3050,
      arrival: 4000,
      cost: 200,
    },
  ],
  arrival: 4000,
  total_cost: 291.15,
  cost_evidence: summary,
};
const offer: Offer = {
  id: 'O1',
  group_id: 'G1',
  itinerary,
  status: 'confirmed',
  expires_at: '2026-10-31T20:02:00Z',
  created_version: 1,
  confirmed_at: '2026-10-31T20:01:00Z',
  reason: null,
};
const world: World = {
  scenario_id: 'pricing-test',
  version: 1,
  epoch: '2026-10-31T00:00:00Z',
  sim_time: 1000,
  mode: 'live',
  currency: 'EUR',
  budget: 1000,
  airports: [],
  resources: [transfer],
  groups: [],
  offers: [offer],
  reservations: [],
  events: [],
  decisions: [],
  observations: [],
  stats: {},
  provider_status: {},
};

describe('typed APL price evidence presentation', () => {
  it('retains exact supplier decimal strings and the provided EUR cents without browser conversion', () => {
    const html = renderToStaticMarkup(
      <PriceEvidenceDetails
        evidence={{ ...price, original: { amount: '76.123456789012', currency: 'GBP' } }}
      />,
    );
    expect(html).toContain('£76.123456789012 per vehicle');
    expect(html).toContain('Approximately €91.15');
    expect(html).toContain('rate dated 2026-10-30');
    expect(html).toContain('1 EUR = 0.83380 GBP');
    expect(html).toContain('EUR settlement is not guaranteed');
  });

  it('marks a mixed itinerary and plan as estimates while retaining legacy displays', () => {
    expect(costPresentation(itinerary)).toEqual({ estimated: true, amount: '≈ €291.15' });
    const plan = {
      total_cost: 291.15,
      cost_evidence: summary,
      allocations: [{ group_id: 'G1', itinerary }],
    } as Plan;
    expect(costPresentation(plan)).toEqual({ estimated: true, amount: '≈ €291.15' });
    const note = renderToStaticMarkup(<EstimatedCostNote value={itinerary} />);
    expect(note).toContain('£76.00');
    expect(note).toContain('approximately €91.15');
    expect(note).toContain('not a guaranteed settlement amount');
    const legacy = {
      ...itinerary,
      legs: [itinerary.legs[1]],
      total_cost: 200,
      cost_evidence: null,
    };
    expect(costPresentation(legacy)).toEqual({ estimated: false, amount: '€200' });
    expect(renderToStaticMarkup(<EstimatedCostNote value={legacy} />)).toBe('');
  });

  it('does not turn missing or inconsistent FX summaries into firm-looking numeric totals', () => {
    for (const cost_evidence of [
      null,
      { ...summary, total_eur: '200.00' },
      { ...summary, total_eur: '291.1' },
      { ...summary, original_gbp: 'NaN' },
    ]) {
      expect(costPresentation({ ...itinerary, cost_evidence })).toEqual({
        estimated: true,
        amount: 'Estimated total unavailable',
      });
    }
  });

  it('shows GBP but suppresses untrusted or malformed EUR evidence', () => {
    for (const evidence of [
      { ...price, estimated_eur: { amount: 'NaN', currency: 'EUR' as const } },
      { ...price, fx_reference: { ...price.fx_reference, rate_date: '2026-02-30' } },
      { ...price, fx_reference: { ...price.fx_reference, source_units_per_eur: '0' } },
      { ...price, fx_reference: { ...price.fx_reference, source_url: 'javascript:alert(1)' } },
    ]) {
      const html = renderToStaticMarkup(<PriceEvidenceDetails evidence={evidence} />);
      expect(html).toContain('£76.00 per vehicle');
      expect(html).toContain('EUR reference estimate unavailable');
      expect(html).not.toContain('Approximately');
      expect(html).not.toContain('href="javascript:');
    }
  });

  it('labels the APL itinerary leg a private transfer and keeps original GBP visible after confirmation', () => {
    const html = renderToStaticMarkup(<ItineraryView offer={offer} world={world} />);
    expect(html).toContain('Private transfer');
    expect(html).toContain('£76.00 per vehicle');
    expect(html).toContain('Approximately €91.15');
    expect(html.toLowerCase()).not.toContain('coach');
  });

  it('shows receipt-based staleness separately from a recorded booking and retains its price evidence', () => {
    vi.useFakeTimers();
    vi.setSystemTime('2026-10-31T20:03:00Z');
    try {
      const html = renderToStaticMarkup(
        <Inspector
          world={world}
          selectedId="T1"
          onSelect={() => {}}
          onClose={() => {}}
          onReplan={() => {}}
          onScenario={() => {}}
          busy={false}
          config={{}}
          showLog={false}
        />,
      );
      expect(html).toContain('£76.00 per vehicle');
      expect(html).toContain('Approximately €91.15');
      expect(html).toContain('Booking recorded');
      expect(html).toContain('Quote stale');
      expect(html).toContain('Received by app');
      expect(html).toContain('31 Oct 2026, 20:00:00 UTC');
      expect(html).toContain('Supplier quote time and expiry are unknown');
      expect(html).not.toContain('Source quoted at');
      expect(html).not.toContain('Group quote total');
      expect(html).toContain('Recorded quoted group');
      expect(html).toContain('2 passengers · one vehicle quote');
      expect(html).not.toContain('Available / total');
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps operator meeting points outside airport and diversion semantics on the map', () => {
    const point = {
      id: 'MP-1',
      code: 'MP-1',
      kind: 'meeting_point' as const,
      name: 'Reported pickup',
      lat: 51.47,
      lon: -0.48,
      available: true,
      diversion_eligible: true,
    };
    expect(isAirportNode(point)).toBe(false);
    expect(locationKindLabel(point)).toBe('Reported meeting point');
    const operation = { ...world, airports: [point] };
    const marker = buildOverlays(operation, point.id, new Map()).markers[0];
    expect(marker.label).toContain('REPORTED MEETING POINT');
    expect(marker.title).toContain('not a verified terminal');
    expect(cameraFor(operation, 'region', point.id).center).toMatchObject({
      lat: 51.47,
      lng: -0.48,
    });
  });
});
