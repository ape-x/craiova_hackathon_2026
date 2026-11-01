import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import CoachFares, { CoachFareResults } from './CoachFares';
import { RecoveryJobDetails } from './RecoveryJobPanel';
import { classifyRecoveryJobGaps, recoveryStageLabel } from './recoveryJobs';
import { TransportError } from './api';
import {
  CoachFareSearch,
  coachEuroEstimate,
  coachMoney,
  coachRequest,
  coachSourceUrl,
  verifyCoachFareResult,
  verifyCoachFareStatus,
} from './coachFareData';
import type {
  CoachFareForm,
  CoachFareFx,
  CoachFareRequest,
  CoachFareResult,
  CoachFareStatus,
} from './coachFareData';
import type { RecoveryJob, World } from './types';

const status: CoachFareStatus = {
  provider: 'BILETERIA',
  configured: true,
  booking_supported: false,
  inventory_verified: false,
  adult_limit: 8,
  max_advance_days: 31,
  source_url: 'https://www.bileteria.ro/',
  supported_routes: [
    {
      origin_airport: 'CRA',
      origin_name: 'Craiova Airport',
      destination_airport: 'OTP',
      destination_name: 'Bucharest Henri Coanda Airport',
    },
  ],
};
const form: CoachFareForm = {
  origin_airport: 'CRA',
  destination_airport: 'OTP',
  travel_date: '2030-11-02',
  adults: '2',
};
const query = coachRequest(form, status, '2030-11-01');
const source = (request: CoachFareRequest) =>
  `https://www.bileteria.ro/Transport/AeroportCraiova-Otopeni?zi=${request.travel_date}&pas=${request.adults}-0-0-0-0`;
function result(request = query): CoachFareResult {
  return {
    provider: 'BILETERIA',
    source_url: source(request),
    received_at: '2030-11-01T10:15:00Z',
    request,
    rejected_count: 1,
    notices: ['Displayed fares do not establish current sellability.'],
    booking_supported: false,
    options: [
      {
        id: 'BILETERIA-OBSERVATION-1',
        operator: 'Union Business & Travel',
        departure_date: request.travel_date,
        arrival_date: request.travel_date,
        departure_time: '00:15',
        arrival_time: '03:20',
        duration_text: '3h 05m',
        departure_stop: {
          name: 'Terminal Plecari/Sosiri',
          locality: 'Aeroport Craiova',
          source_url: 'https://bileteria.ro/AeroportCraiova/TerminalPlecari-Sosiri/Autogara35196',
        },
        arrival_stop: {
          name: 'Terminal PLECARI/ DEPARTURES',
          locality: 'Aeroport Otopeni',
          source_url: 'https://bileteria.ro/Otopeni/TerminalPLECARI/Autogara99',
        },
        price: { amount: '190', currency: 'RON' },
        price_scope: 'requested_party',
        adults: request.adults,
        seat_availability: null,
        supplier_valid_until: null,
        purchase_action_enabled: true,
        source_url: source(request),
        notices: ['Se poate călători doar cu rezervare anticipată.'],
        eur_reference_estimate: null,
      },
    ],
  };
}
function withFx(): CoachFareResult {
  const value = result();
  value.fx = {
    source_currency: 'RON',
    state: 'ready',
    source: 'European Central Bank',
    source_url: 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml',
    reference: {
      source: 'European Central Bank',
      source_url: 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml',
      base_currency: 'EUR',
      rate_date: '2030-10-31',
      retrieved_at: '2030-11-01T10:14:00Z',
      as_of: '2030-11-01T10:15:00Z',
      age_calendar_days: 1,
      age_timezone: 'Europe/Berlin',
      max_age_days: 3,
      source_units_per_eur: '5.0900',
    },
    cache: 'fresh',
    settlement_guaranteed: false,
    revalidation_required: true,
    message: 'Reference only; revalidate before booking.',
  };
  value.options[0].eur_reference_estimate = {
    amount: '37.33',
    currency: 'EUR',
    kind: 'ecb_reference_estimate',
    rounding: 'half_even_to_cent',
  };
  return value;
}
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('explicit coach date, party and source boundaries', () => {
  it('requires an explicit adult party and a supported route/date within the source horizon', () => {
    expect(query).toEqual({
      origin_airport: 'CRA',
      destination_airport: 'OTP',
      travel_date: '2030-11-02',
      adults: 2,
    });
    for (const override of [
      { adults: '' },
      { adults: '2.5' },
      { adults: '9' },
      { adults: '-1' },
      { adults: 'NaN' },
      { origin_airport: 'SBZ' },
      { destination_airport: 'CRA' },
      { travel_date: '2030-02-30' },
      { travel_date: '2030-10-31' },
      { travel_date: '2030-12-03' },
    ])
      expect(() => coachRequest({ ...form, ...override }, status, '2030-11-01')).toThrow();
    expect(() => coachRequest(form, { ...status, configured: false }, '2030-11-01')).toThrow(
      /unavailable/,
    );
    expect(
      coachRequest({ ...form, travel_date: '2030-12-02' }, status, '2030-11-01').travel_date,
    ).toBe('2030-12-02');
  });
  it('rejects source setup that could misstate capacity, horizon or supported routes', () => {
    expect(verifyCoachFareStatus(status)).toEqual(status);
    for (const override of [
      { configured: 'true' },
      { adult_limit: 0 },
      { adult_limit: 100 },
      { max_advance_days: Infinity },
      { max_advance_days: 32 },
      { booking_supported: true },
      { inventory_verified: true },
      { supported_routes: [] },
      { supported_routes: [null] },
    ])
      expect(() => verifyCoachFareStatus({ ...status, ...override })).toThrow();
  });
  it('never creates checkout, credential-bearing, foreign or mismatched dated links', () => {
    expect(coachSourceUrl(source(query), query)).toBe(source(query));
    for (const url of [
      'https://www.bileteria.ro/Checkout?pas=2-0-0-0-0',
      source(query).replace('https:', 'http:'),
      source(query).replace('www.bileteria.ro', 'www.bileteria.ro.evil.example'),
      source(query).replace('www.bileteria.ro', 'secret@www.bileteria.ro'),
      `${source(query)}&zi=2030-11-03`,
      `${source(query)}&redirect=https://example.com`,
      source({ ...query, adults: 1 }),
      source({ ...query, travel_date: '2030-11-03' }),
      `${source(query)}#checkout`,
    ])
      expect(coachSourceUrl(url, query)).toBeNull();
  });
  it('rejects mismatched date, party, currency, invented seats and duplicated evidence', () => {
    expect(verifyCoachFareResult(result(), query)).toEqual(result());
    for (const change of [
      (v: CoachFareResult) => {
        v.request = { ...query, adults: 3 };
      },
      (v: CoachFareResult) => {
        v.source_url = source({ ...query, travel_date: '2030-11-03' });
      },
      (v: CoachFareResult) => {
        v.options[0].adults = 3;
      },
      (v: CoachFareResult) => {
        v.options[0].price.currency = 'GBP' as 'RON';
      },
      (v: CoachFareResult) => {
        v.options[0].price.amount = '1e3';
      },
      (v: CoachFareResult) => {
        v.options[0].seat_availability = 2 as unknown as null;
      },
      (v: CoachFareResult) => {
        v.options[0].supplier_valid_until = '2030-11-02' as unknown as null;
      },
      (v: CoachFareResult) => {
        v.options[0].arrival_time = '00:00';
      },
      (v: CoachFareResult) => {
        v.options.push({ ...v.options[0] });
      },
      (v: CoachFareResult) => {
        v.received_at = '2030-11-01T10:15:00';
      },
    ]) {
      const value = result();
      change(value);
      expect(() => verifyCoachFareResult(value, query)).toThrow(/evidence/);
    }
  });
});

describe('single explicit search and late-response isolation', () => {
  it('sends one same-origin authenticated search preserving exact inputs', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json(result()));
    vi.stubGlobal('fetch', fetcher);
    const session = new CoachFareSearch();
    expect(await session.search(query)).toEqual(result());
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe('/api/coaches/search');
    expect(fetcher.mock.calls[0][1]).toMatchObject({
      method: 'POST',
      credentials: 'same-origin',
      body: JSON.stringify(query),
    });
    expect(session.pending).toBe(false);
  });
  it('makes no automatic retry after network errors or rejected source responses', async () => {
    const fetcher = vi.fn().mockRejectedValue(new TypeError('Disconnected'));
    vi.stubGlobal('fetch', fetcher);
    const session = new CoachFareSearch();
    await expect(session.search(query)).rejects.toBeInstanceOf(TransportError);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(session.pending).toBe(false);
    fetcher.mockResolvedValue(
      Response.json({ detail: 'Source response changed' }, { status: 502 }),
    );
    await expect(session.search(query)).rejects.toThrow('Source response changed');
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it('refuses duplicate pending submissions and never publishes an invalidated response', async () => {
    let resolveFirst!: (value: Response) => void;
    const fetcher = vi.fn().mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          resolveFirst = resolve;
        }),
    );
    vi.stubGlobal('fetch', fetcher);
    const session = new CoachFareSearch();
    const first = session.search(query);
    await expect(session.search(query)).rejects.toThrow(/already running/);
    expect(fetcher).toHaveBeenCalledTimes(1);
    session.invalidate();
    expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
    const changed = { ...query, travel_date: '2030-11-03' };
    fetcher.mockResolvedValueOnce(Response.json(result(changed)));
    expect(await session.search(changed)).toEqual(result(changed));
    resolveFirst(Response.json(result()));
    expect(await first).toBeNull();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it('rejects a successful HTTP response for another date without showing old prices', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(Response.json(result({ ...query, travel_date: '2030-11-03' })));
    vi.stubGlobal('fetch', fetcher);
    await expect(new CoachFareSearch().search(query)).rejects.toThrow(/date, party/);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});

describe('fare evidence presentation without allocatable seats', () => {
  it('renders the exact party fare, original stops, observation time and availability limits', () => {
    const html = renderToStaticMarkup(<CoachFareResults result={result()} />);
    expect(html).toContain('190.00');
    expect(html).toContain('RON');
    expect(html).toContain('Total for 2 adults');
    expect(html).not.toContain('380');
    expect(html).not.toContain('€');
    expect(html).not.toContain('/Checkout');
    expect(html).toContain('Terminal Plecari/Sosiri');
    expect(html).toContain('Terminal PLECARI/ DEPARTURES');
    expect(html).toContain('UTC offset');
    expect(html).toContain('Seat availability is unknown');
    expect(html).toContain('no transport is allocated, held or booked');
    expect(html).toContain('<dt>Supplier expiry</dt><dd>Unknown</dd>');
    expect(html).toContain('3h 05m');
    expect(html).toContain('Se poate călători doar cu rezervare anticipată.');
    expect(html).not.toContain('CAPACITY FITS');
    expect(html).not.toContain('Add planning leg');
    expect(html).toContain('2030');
    expect(html).toContain('UTC');
  });
  it('keeps one clear fare boundary and both prices visible while disclosing repeated source details once', () => {
    const value = withFx();
    value.options.push({
      ...value.options[0],
      id: 'SECOND-OPTION',
      notices: ['Second option notice.'],
    });
    const html = renderToStaticMarkup(<CoachFareResults result={value} historical />);
    const [visibleBeforeDetails, disclosure] = html.split('<details class="coach-source-details">');
    expect(disclosure).toBeDefined();
    expect(html).not.toContain('class="coach-source-details" open');
    expect(html.match(/class="coach-boundary"/g)).toHaveLength(1);
    expect(visibleBeforeDetails).toContain('Fare references only. Seat availability is unknown');
    expect(visibleBeforeDetails).toContain('no transport is allocated, held or booked');
    expect(visibleBeforeDetails).toContain('Historical observation');
    expect(visibleBeforeDetails).toContain('1 Nov 2030, 10:15:00 UTC');
    expect(visibleBeforeDetails).toContain('Source local times · UTC offset not supplied');
    expect(visibleBeforeDetails).toContain('190.00');
    expect(visibleBeforeDetails).toContain('Approximately €37.33');
    expect(visibleBeforeDetails).toContain('Se poate călători doar cu rezervare anticipată.');
    expect(visibleBeforeDetails).toContain('Second option notice.');
    expect(visibleBeforeDetails).not.toContain('Supplier expiry');
    expect(visibleBeforeDetails).not.toContain('EUR settlement is not guaranteed');
    expect(disclosure).toContain('<dt>Supplier expiry</dt><dd>Unknown</dd>');
    expect(disclosure).toContain('Accessibility, luggage and boarding access</dt><dd>Unverified');
    expect(disclosure).toContain('1 EUR = 5.0900 RON');
    expect(disclosure).toContain('rate dated 2030-10-31');
    expect(disclosure).toContain('Assessed 1 Nov 2030, 10:15:00 UTC');
    expect(disclosure).toContain('EUR settlement is not guaranteed');
    expect(disclosure).toContain(value.notices[0]);
    expect(html.match(/Accessibility, luggage and boarding access/g)).toHaveLength(1);
    expect(html).not.toContain('Add planning leg');
    expect(html).not.toContain('/Checkout');
  });
  it('preserves overnight arrival dates and disables no-availability purchase inferences', () => {
    const value = result();
    value.options[0].departure_time = '23:50';
    value.options[0].arrival_time = '02:55';
    value.options[0].arrival_date = '2030-11-03';
    value.options[0].purchase_action_enabled = false;
    const html = renderToStaticMarkup(<CoachFareResults result={value} historical />);
    expect(html).toContain('arrives 2030-11-03');
    expect(html).toContain('Historical observation');
    expect(html).toContain('did not show an enabled purchase action');
    expect(html).not.toContain('sold out');
    expect(html).not.toContain('disabled=""');
  });
  it('displays a source-backed EUR estimate while retaining RON as the authoritative amount', () => {
    const value = withFx();
    const html = renderToStaticMarkup(<CoachFareResults result={value} />);
    expect(html).toContain('Approximately €37.33');
    expect(html).toContain('1 EUR = 5.0900 RON');
    expect(html).toContain('rate dated 2030-10-31');
    expect(html).toContain('EUR settlement is not guaranteed');
    expect(html.indexOf('190.00')).toBeLessThan(html.indexOf('€37.33'));
    value.options[0].price.amount = '190.1250';
    expect(coachMoney('190.1250')).toBe('190.1250');
    expect(coachEuroEstimate(value.options[0], value.fx)).toBe('€37.33');
  });
  it.each(['unavailable', 'missing_rate', 'stale_rate', 'not_requested'] as const)(
    'never displays EUR for %s references',
    (state) => {
      const value = withFx();
      value.fx!.state = state;
      const html = renderToStaticMarkup(<CoachFareResults result={value} />);
      expect(html).toContain('190.00');
      expect(html).not.toContain('€37.33');
      expect(html).toContain('EUR estimate unavailable');
    },
  );
  it('hides EUR for a missing or contradictory RON marker and malformed conversion metadata', () => {
    for (const override of [
      { source_currency: undefined },
      { source_currency: 'GBP' },
      { settlement_guaranteed: true },
      { revalidation_required: false },
      { source: 'Unverified source' },
    ]) {
      const value = withFx();
      value.fx = { ...value.fx, ...override } as CoachFareFx;
      expect(coachEuroEstimate(value.options[0], value.fx)).toBeNull();
      expect(renderToStaticMarkup(<CoachFareResults result={value} />)).not.toContain('€37.33');
    }
    const value = withFx();
    value.fx!.reference!.age_calendar_days = 4;
    expect(coachEuroEstimate(value.options[0], value.fx)).toBeNull();
    value.fx!.reference!.age_calendar_days = 1;
    value.options[0].eur_reference_estimate!.currency = 'RON' as 'EUR';
    expect(coachEuroEstimate(value.options[0], value.fx)).toBeNull();
  });
  it('shows an honest empty or malformed-source outcome without inventing transport', () => {
    const empty = result();
    empty.options = [];
    expect(renderToStaticMarkup(<CoachFareResults result={empty} />)).toContain(
      'No supported dated fares',
    );
    empty.provider = 'Unknown' as 'BILETERIA';
    const html = renderToStaticMarkup(<CoachFareResults result={empty} />);
    expect(html).toContain('role="alert"');
    expect(html).toContain('incomplete or inconsistent');
    expect(html).not.toContain('<a');
  });
  it('does not fetch prices during rendering or prefill a presumed passenger count', () => {
    const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    const html = renderToStaticMarkup(<CoachFares />);
    expect(fetcher).not.toHaveBeenCalled();
    expect(html).toContain('Adult passengers');
    expect(html).toContain('value=""');
    expect(html).toContain('Find coach fares');
    expect(html).not.toContain('190.00');
  });
  it('renders blocked-job references outside the applied result without claiming recovered passengers', () => {
    const job: RecoveryJob = {
      id: 'JOB-1',
      incident_id: 'CASE-1',
      group_ids: ['G1'],
      modes: ['bus'],
      objective: 'balanced',
      max_provider_calls: 3,
      calls_dispatched: 2,
      status: 'blocked',
      stage: 'finished',
      message: 'Seat confirmation is required.',
      created_at: '2030-11-01T10:14:00Z',
      updated_at: '2030-11-01T10:15:00Z',
      source_version: 10,
      applied_version: null,
      cancel_requested: false,
      steps: [],
      gaps: [
        {
          code: 'ground_availability_unverified',
          message: 'Seats have not been checked by the supplier.',
          group_ids: ['G1'],
        },
      ],
      result: null,
      ground_references: [{ group_ids: ['G1'], result: result() }],
    };
    const world = {
      mode: 'live',
      offers: [],
      groups: [{ id: 'G1', name: 'Airport group' }],
      airports: [],
    } as unknown as World;
    const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    const html = renderToStaticMarkup(<RecoveryJobDetails job={job} world={world} />);
    expect(fetcher).not.toHaveBeenCalled();
    expect(html).toContain('Recorded ground fare references');
    expect(html).toContain('Airport group');
    expect(html).toContain('190.00');
    expect(html).toContain('Historical observation');
    expect(html).toContain('Requirements still unresolved');
    expect(html).not.toContain('Recorded proposal result');
    expect(html).not.toContain('passengers proposed');
    job.status = 'completed';
    job.applied_version = 11;
    job.result = {
      decision_id: 'D1',
      plan_id: 'P1',
      recovered_passengers: 2,
      exception_passengers: 0,
      offer_ids: [],
      resource_ids: [],
      source_counts: { 'Flight supplier': 1 },
    };
    const completed = renderToStaticMarkup(<RecoveryJobDetails job={job} world={world} />);
    expect(completed).toContain('Recorded proposal result');
    expect(completed).toContain('Source exclusions and search outcomes');
    expect(completed).not.toContain('Requirements still unresolved');
    job.result.exception_passengers = 2;
    expect(renderToStaticMarkup(<RecoveryJobDetails job={job} world={world} />)).toContain(
      'Requirements still unresolved',
    );
  });
  it('keeps unfamiliar ground requirements visible even beside a successful flight proposal', () => {
    const known = {
      code: 'ground_availability_unverified',
      message: 'Coach seats unverified',
      group_ids: ['G1'],
    };
    const unknown = {
      code: 'ground_new_requirement',
      message: 'Unknown ground requirement',
      group_ids: ['G1'],
    };
    expect(classifyRecoveryJobGaps([known, unknown], true)).toEqual({
      requirements: [unknown],
      sourceExclusions: [known],
      waitingNotes: [],
    });
    expect(classifyRecoveryJobGaps([known, unknown], false).requirements).toEqual([known, unknown]);
    expect(recoveryStageLabel.searching_ground).toBe('Searching dated coach fares');
  });
});
