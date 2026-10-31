import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import TransferQuotes, { TransferQuoteResults } from './TransferQuotes';
import { londonPickup, transferPrice, transferRequest } from './transferQuoteData';
import type { TransferQuoteForm, TransferQuoteResult } from './transferQuoteData';

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
  it('preserves exact GBP values without per-passenger multiplication or EUR conversion', () => {
    expect(transferPrice('76')).toBe('£76.00');
    expect(transferPrice('76.1250')).toBe('£76.1250');
    expect(transferPrice('1234.50')).toBe('£1,234.50');
    const html = renderToStaticMarkup(<TransferQuoteResults result={result()} />);
    expect(html).toContain('£76.00');
    expect(html).toContain('GBP per vehicle');
    expect(html).not.toContain('£152');
    expect(html).not.toContain('€');
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
