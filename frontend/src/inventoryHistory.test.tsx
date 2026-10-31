import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import HistoricalSource, { SourceRecordDetails } from './HistoricalSource';
import PassengerHistory from './PassengerHistory';
import { archiveReasonLabel, loadInventoryRecord, verifyInventoryRecord } from './inventoryHistory';
import type { InventoryResourceRecord, Offer, PriceEvidence, Resource } from './types';

const epoch = '2026-11-01T00:00:00Z';
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
const resource: Resource = {
  id: 'APL/OLD-1',
  kind: 'bus',
  name: 'Original supplier saloon',
  origin: 'MP-1',
  destination: 'HP-1',
  departure: 100,
  arrival: 140,
  capacity: 2,
  accessible: false,
  available: false,
  cost_per_person: 0,
  room_capacity: 2,
  vehicle_id: null,
  status: 'quoted',
  source: 'Airport Pickups London',
  external_offer_id: 'supplier-old',
  transport_mode: 'private_transfer',
  booking_status: 'unbooked',
  price_evidence: price,
  quote_freshness: {
    basis: 'app_receipt',
    received_at: '2026-10-31T20:00:00Z',
    max_age_seconds: 120,
  },
  provenance: { private_diagnostic: 'MUST_NOT_RENDER' },
};
const record: InventoryResourceRecord = {
  scenario_id: 'OP/OLD',
  resource,
  locations: [
    {
      id: 'MP-1',
      code: 'MP-1',
      name: 'Declared airport pickup',
      kind: 'meeting_point',
      lat: 51.4,
      lon: -0.4,
      available: false,
      diversion_eligible: false,
    },
    {
      id: 'HP-1',
      code: 'HP-1',
      name: 'Original supplier hotel',
      kind: 'hotel',
      lat: 51.5,
      lon: -0.3,
      available: false,
      diversion_eligible: false,
    },
  ],
  archived: true,
  archived_at: '2026-11-01T12:00:00Z',
  archived_version: 42,
  archive_reason: 'Expired supplier quote',
};
const offer: Offer = {
  id: 'OLD-OFFER',
  group_id: 'G-1',
  status: 'expired',
  created_version: 4,
  expires_at: '2026-10-31T20:02:00Z',
  confirmed_at: null,
  reason: 'The recorded proposal expired.',
  itinerary: {
    legs: [
      {
        resource_id: resource.id,
        kind: 'bus',
        origin: 'MP-1',
        destination: 'HP-1',
        departure: 100,
        arrival: 140,
        cost: 91.15,
        transport_mode: 'private_transfer',
        price_evidence: price,
      },
    ],
    arrival: 140,
    total_cost: 91.15,
    cost_evidence: {
      kind: 'includes_ecb_reference',
      total_eur: '91.15',
      estimated_eur: '91.15',
      original_gbp: '76',
      policy_id: 'ecb_reference_v1',
      settlement_guaranteed: false,
      revalidation_required: true,
    },
  },
};
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('saved inventory read boundary', () => {
  it('does not request inventory just because historical service references render', () => {
    const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    const html = renderToStaticMarkup(
      <HistoricalSource scenarioId={record.scenario_id} resourceId={resource.id} epoch={epoch} />,
    );
    expect(fetcher).not.toHaveBeenCalled();
    expect(html).toContain('View source');
    expect(html).toContain('aria-expanded="false"');
    expect(html).not.toContain('Original supplier saloon');
  });
  it('reads a specific encoded operation and service without a mutation body', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json(record));
    vi.stubGlobal('fetch', fetcher);
    const result = await loadInventoryRecord(
      record.scenario_id,
      resource.id,
      new AbortController().signal,
    );
    expect(result).toEqual(record);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe(
      '/api/inventory/resources/APL%2FOLD-1?scenario_id=OP%2FOLD',
    );
    expect(fetcher.mock.calls[0][1]).toMatchObject({
      method: 'GET',
      body: undefined,
      credentials: 'same-origin',
    });
  });
  it.each([
    { ...record, scenario_id: 'OTHER' },
    { ...record, resource: { ...resource, id: 'OTHER' } },
    { ...record, locations: null },
    { ...record, locations: [{ id: 'HP-1', name: 'Unknown coordinates' }] },
    { ...record, resource: { ...resource, departure: 'yesterday' } },
    { ...record, archived: undefined },
  ])('rejects a malformed or differently scoped record', (value) => {
    expect(() => verifyInventoryRecord(value, record.scenario_id, resource.id)).toThrow(
      'could not be verified',
    );
  });
  it('ignores a response that finishes after its panel closes or its scope changes', async () => {
    let resolve!: (response: Response) => void;
    const fetcher = vi.fn(
      () =>
        new Promise<Response>((done) => {
          resolve = done;
        }),
    );
    vi.stubGlobal('fetch', fetcher);
    const controller = new AbortController();
    const pending = loadInventoryRecord(record.scenario_id, resource.id, controller.signal);
    controller.abort();
    resolve(Response.json(record));
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('does not dispatch an already cancelled read', async () => {
    const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    const controller = new AbortController();
    controller.abort();
    await expect(
      loadInventoryRecord(record.scenario_id, resource.id, controller.signal),
    ).rejects.toMatchObject({ name: 'AbortError' });
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('does not automatically retry a missing historical source', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(Response.json({ detail: 'Record not found' }, { status: 404 }));
    vi.stubGlobal('fetch', fetcher);
    await expect(
      loadInventoryRecord(record.scenario_id, resource.id, new AbortController().signal),
    ).rejects.toMatchObject({ status: 404 });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});

describe('historical evidence presentation', () => {
  it.each([
    ['capacity_pressure', 'make room for current inventory'],
    ['superseded_quote', 'A newer supplier quote replaced'],
    ['hotel_quotes_replaced', 'fresh hotel search replaced'],
    ['removed_from_active', 'removed from active inventory'],
    ['resource_revision', 'earlier version of the service'],
    ['location_revision', 'earlier version of the location'],
    ['unrecognized_internal_reason', 'further reason details are unavailable'],
    ['constructor', 'further reason details are unavailable'],
  ])('explains the %s archive reason without internal codes', (reason, explanation) => {
    const html = renderToStaticMarkup(
      <SourceRecordDetails record={{ ...record, archive_reason: reason }} epoch={epoch} />,
    );
    expect(html).toContain(explanation);
    expect(html).not.toContain(reason);
    expect(archiveReasonLabel(reason)).not.toContain('_');
  });
  it('preserves actual endpoint names, original GBP and ECB qualification without exposing arbitrary provenance', () => {
    const html = renderToStaticMarkup(<SourceRecordDetails record={record} epoch={epoch} />);
    expect(html).toContain('Historical evidence only');
    expect(html).toContain('cannot be used for a new plan');
    expect(html).toContain('Declared airport pickup');
    expect(html).toContain('Original supplier hotel');
    expect(html).toContain('£76.00 per vehicle');
    expect(html).toContain('Approximately €91.15');
    expect(html).toContain('rate dated 2026-10-30');
    expect(html).toContain('EUR settlement is not guaranteed');
    expect(html).toContain('Source quoted at</dt><dd>Not supplied');
    expect(html).toContain('Supplier validity</dt><dd>Not supplied');
    expect(html).not.toContain('UTC UTC');
    expect(html).not.toContain('MUST_NOT_RENDER');
    expect(html).not.toContain('<button');
  });
  it('does not present a current-inventory record as proof of current stock', () => {
    const html = renderToStaticMarkup(
      <SourceRecordDetails
        record={{
          ...record,
          archived: false,
          archived_at: null,
          archived_version: null,
          archive_reason: null,
        }}
        epoch={epoch}
      />,
    );
    expect(html).toContain('does not check current supplier availability');
    expect(html).not.toContain('Historical evidence only');
  });
  it('keeps the passenger historical itinerary read-only and excludes other service records', () => {
    const html = renderToStaticMarkup(
      <PassengerHistory
        offer={offer}
        epoch={epoch}
        resources={[resource, { ...resource, id: 'OTHER', name: 'Another family source' }]}
        airports={record.locations}
        archivedResourceIds={[resource.id, resource.id, 'OTHER']}
      />,
    );
    expect(html).toContain('Previous itinerary · historical record');
    expect(html).toContain('1 service record preserved');
    expect(html).toContain('Original supplier hotel');
    expect(html).toContain('Original supplier saloon');
    expect(html).toContain('£76.00 per vehicle');
    expect(html).not.toContain('Another family source');
    expect(html).not.toContain('<button');
  });
  it.each(['held', 'proposed', 'accepted', 'confirmed'] as const)(
    'does not mislabel a %s offer as archived history',
    (status) => {
      expect(
        renderToStaticMarkup(
          <PassengerHistory offer={{ ...offer, status }} epoch={epoch} resources={[resource]} />,
        ),
      ).toBe('');
    },
  );
  it('supports older passenger responses without inventing saved supplier details', () => {
    const html = renderToStaticMarkup(<PassengerHistory offer={offer} epoch={epoch} />);
    expect(html).toContain('historical record');
    expect(html).not.toContain('Original supplier saloon');
    expect(html).not.toContain('preserved in the source archive');
    expect(html).toContain('Source evidence is unavailable for 1 recorded service');
    expect(html).toContain('missing supplier details have not been reconstructed');
  });
  it('marks only missing source records while retaining the full saved itinerary', () => {
    const partial: Offer = {
      ...offer,
      itinerary: {
        ...offer.itinerary,
        legs: [
          ...offer.itinerary.legs,
          {
            resource_id: 'MISSING-HOTEL',
            kind: 'hotel',
            origin: 'HP-1',
            destination: 'HP-1',
            departure: 145,
            arrival: 200,
            cost: 50,
          },
        ],
      },
    };
    const html = renderToStaticMarkup(
      <PassengerHistory
        offer={partial}
        epoch={epoch}
        resources={[resource]}
        airports={record.locations}
      />,
    );
    expect(html).toContain('Source evidence is unavailable for 1 recorded service');
    expect(html).toContain('MISSING-HOTEL');
    expect(html).toContain('Original supplier saloon');
    expect(html).not.toContain('<button');
  });
});
