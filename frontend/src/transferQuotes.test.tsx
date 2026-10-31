import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import TransferQuotes, { TransferQuoteResults } from './TransferQuotes';
import {
  londonPickup,
  transferEuroEstimate,
  transferPrice,
  transferRequest,
} from './transferQuoteData';
import type { TransferFx, TransferQuoteForm, TransferQuoteResult } from './transferQuoteData';

const form: TransferQuoteForm = {
  origin: 'Heathrow Terminal 5',
  destination: 'W1K 1LN',
  date: '2030-11-02',
  time: '14:00',
  passengers: '2',
  suitcases: '0',
};

function result(): TransferQuoteResult {
  return {
    provider: 'Airport Pickups London',
    source_url: 'https://mcp.airport-pickups-london.com/docs',
    received_at: '2030-11-01T10:15:00Z',
    source_quoted_at: null,
    expires_at: null,
    request: {
      ...transferRequest(form, 0),
      transfer_date: '2030-11-02',
      transfer_time: '14:00',
      timezone: 'Europe/London',
    },
    resolved_route: {
      pickup: 'LHR Airport',
      dropoff: 'W1K 1LN',
      hub: null,
      direction: null,
      resolved_address: null,
      pickup_terminal: null,
    },
    options: [
      {
        id: 'local-quote',
        car_type: 'Saloon',
        price: { amount: '76', currency: 'GBP' },
        price_scope: 'per_vehicle',
        max_passengers: 3,
        max_suitcases: 3,
        fits_request: true,
        duration_seconds: null,
        arrival_at: null,
        availability_status: 'quoted_not_reserved',
        accessibility_confirmed: false,
      },
    ],
    provider_duration_text: null,
    message: null,
  };
}

function fxResult(): TransferQuoteResult {
  const quote = result();
  quote.fx = {
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
      source_units_per_eur: '0.83380',
    },
    cache: 'cached',
    settlement_guaranteed: false,
    revalidation_required: true,
    message: 'ECB reference estimate; not a supplier EUR price.',
  };
  quote.options[0].eur_reference_estimate = {
    amount: '91.15',
    currency: 'EUR',
    kind: 'ecb_reference_estimate',
    rounding: 'half_even_to_cent',
  };
  return quote;
}

describe('London pickup and explicit query boundaries', () => {
  it('converts summer and winter London time without relying on browser timezone', () => {
    expect(londonPickup('2030-07-02', '14:00')).toBe('2030-07-02T13:00:00.000Z');
    expect(londonPickup('2030-11-02', '14:00')).toBe('2030-11-02T14:00:00.000Z');
    expect(londonPickup('2030-07-02', '00:15')).toBe('2030-07-01T23:15:00.000Z');
  });

  it('rejects both missing spring times and repeated autumn times instead of guessing', () => {
    expect(() => londonPickup('2030-03-31', '01:30')).toThrow(/does not exist/);
    expect(() => londonPickup('2030-10-27', '01:30')).toThrow(/occurs twice/);
    expect(londonPickup('2030-10-27', '02:30')).toBe('2030-10-27T02:30:00.000Z');
    expect(() => londonPickup('2030-02-30', '14:00')).toThrow(/valid pickup date/);
  });

  it('requires party and bags while preserving an explicit zero bag count', () => {
    expect(transferRequest(form, 0)).toEqual({
      origin: 'Heathrow Terminal 5',
      destination: 'W1K 1LN',
      passengers: 2,
      suitcases: 0,
      pickup_at: '2030-11-02T14:00:00.000Z',
    });
    for (const override of [
      { passengers: '' },
      { suitcases: '' },
      { passengers: '2.5' },
      { suitcases: '-1' },
      { destination: form.origin },
      { origin: 'T5\naddress' },
    ])
      expect(() => transferRequest({ ...form, ...override }, 0)).toThrow();
    expect(() => transferRequest(form, Date.parse('2030-11-02T14:00:00Z'))).toThrow(/future/);
  });
});

describe('quote presentation', () => {
  it('preserves exact GBP values and does not invent EUR when an older response omits FX', () => {
    expect(transferPrice('76')).toBe('£76.00');
    expect(transferPrice('76.1250')).toBe('£76.1250');
    expect(transferPrice('1234.50')).toBe('£1,234.50');
    const html = renderToStaticMarkup(<TransferQuoteResults result={result()} />);
    expect(html).toContain('£76.00');
    expect(html).toContain('GBP per vehicle');
    expect(html).not.toContain('£152');
    expect(html).not.toContain('€');
    expect(html).toContain('EUR estimate unavailable');
  });

  it('keeps exact GBP prominent and labels the supplied EUR amount with source, rate date and limits', () => {
    const html = renderToStaticMarkup(<TransferQuoteResults result={fxResult()} />);
    expect(html).toContain('<strong>£76.00</strong>');
    expect(html).toContain('Approximately €91.15');
    expect(html.indexOf('£76.00')).toBeLessThan(html.indexOf('€91.15'));
    expect(html).toContain('EUR reference estimate per vehicle');
    expect(html).toContain('European Central Bank');
    expect(html).toContain('Rate date: 2030-10-31');
    expect(html).toContain('1 EUR = 0.83380 GBP');
    expect(html).toContain('at most 3 calendar days');
    expect(html).toContain('Europe/Berlin');
    expect(html).toContain('cached reference');
    expect(html).toContain('not an APL EUR price');
    expect(html).toContain('Revalidate before booking');
    expect(html).toContain('EUR settlement is not guaranteed');
    expect(html).toContain('are not added to an itinerary or the operation’s EUR budget');
  });

  it('preserves the server Decimal estimate without binary-float conversion or browser recalculation', () => {
    const quote = fxResult();
    quote.options[0].eur_reference_estimate!.amount = '12345678901234567890.12';
    expect(transferEuroEstimate(quote.options[0], quote.fx)).toBe('€12,345,678,901,234,567,890.12');
    expect(transferPrice('76.1250')).toBe('£76.1250');
  });

  it('preserves GBP and hides any EUR amount for unavailable, stale, missing or unrequested rates', () => {
    const states: [TransferFx['state'], string][] = [
      ['unavailable', 'EUR estimate unavailable'],
      ['stale_rate', 'ECB rate is stale'],
      ['missing_rate', 'GBP reference rate unavailable'],
      ['not_requested', 'EUR estimate not requested'],
    ];
    for (const [state, label] of states) {
      const quote = fxResult();
      quote.fx!.state = state;
      quote.fx!.message = 'No usable GBP reference for this response.';
      if (state === 'unavailable' || state === 'not_requested') quote.fx!.reference = null;
      if (state === 'missing_rate') quote.fx!.reference!.source_units_per_eur = null;
      const html = renderToStaticMarkup(<TransferQuoteResults result={quote} />);
      expect(html).toContain(label);
      expect(html).toContain('£76.00');
      expect(html).toContain('GBP source prices remain unchanged');
      expect(html).not.toContain('€');
    }
  });

  it('does not show a conversion from malformed or inconsistent ready-reference metadata', () => {
    const reference = fxResult().fx!.reference!;
    for (const invalid of [
      null,
      { ...reference, source_units_per_eur: '0' },
      { ...reference, source_units_per_eur: 'NaN' },
      { ...reference, rate_date: '2030-02-30' },
      { ...reference, age_calendar_days: 4 },
      { ...reference, source_url: 'https://unrelated.example/rate' },
    ]) {
      const quote = fxResult();
      quote.fx!.reference = invalid;
      const html = renderToStaticMarkup(<TransferQuoteResults result={quote} />);
      expect(html).toContain('£76.00');
      expect(html).toContain('reference is incomplete or inconsistent');
      expect(html).not.toContain('€');
    }
  });

  it('leaves a missing or malformed per-option estimate unavailable instead of substituting zero', () => {
    for (const amount of ['', 'NaN', '-1.00', '91.155', '91']) {
      const quote = fxResult();
      quote.options[0].eur_reference_estimate!.amount = amount;
      const html = renderToStaticMarkup(<TransferQuoteResults result={quote} />);
      expect(html).toContain('EUR reference estimate unavailable');
      expect(html).toContain('£76.00');
      expect(html).not.toContain('€');
    }
    const quote = fxResult();
    quote.options[0].eur_reference_estimate = null;
    expect(transferEuroEstimate(quote.options[0], quote.fx)).toBeNull();
  });

  it('clearly separates capacity from availability and keeps expiry and journey timing unknown', () => {
    const html = renderToStaticMarkup(<TransferQuoteResults result={result()} />);
    expect(html).toContain('CAPACITY FITS');
    expect(html).toContain('Up to 3 passengers');
    expect(html).toContain('Supplier expiry was not provided');
    expect(html).toContain('Price snapshot only');
    expect(html).toContain('Journey duration and arrival time are not verified');
    expect(html).toContain('No booking, driver dispatch or supplier hold was made');
    expect(html).not.toMatch(/Book now|Reserve vehicle|seats available|fare saved/i);
  });

  it('does not present an oversized party as fitting or invent options after an empty result', () => {
    const quote = result();
    quote.options[0].fits_request = false;
    expect(renderToStaticMarkup(<TransferQuoteResults result={quote} />)).toContain('DOES NOT FIT');
    quote.options = [];
    const html = renderToStaticMarkup(<TransferQuoteResults result={quote} />);
    expect(html).toContain('no vehicle quotes');
    expect(html).not.toContain('Saloon');
  });

  it('starts with empty explicit counts and disabled submission until server configuration is known', () => {
    const html = renderToStaticMarkup(<TransferQuotes />);
    expect(html).toContain('Europe/London');
    expect(html).toContain('CHECKING SETUP');
    expect(html).toMatch(/type="number"[^>]*value=""/);
    expect(html).toMatch(/disabled=""[^>]*type="submit"/);
    expect(html).not.toContain('APL CONFIGURED');
  });
});
