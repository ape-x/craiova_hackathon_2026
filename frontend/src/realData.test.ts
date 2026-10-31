import { describe, expect, it } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import HotelOfferPrice from './HotelOfferPrice';
import {
  addCalendarDays,
  bucharestDate,
  formatOfferPrice,
  hotelSearchDetails,
  liveHotelOffers,
  MOMONDO_HOTELS_URL,
  safeExternalUrl,
  validateRealDataQuery,
} from './realData';
import type { RealDataQuery, RealDataSection, RealHotelOffer } from './realData';
const query: RealDataQuery = {
  airport_code: 'CRA',
  destination_code: 'OTP',
  checkin: '2026-10-31',
  checkout: '2026-11-01',
  adults: 2,
  rooms: 1,
};
describe('hotel price disclosure', () => {
  const offer: RealHotelOffer = {
    id: 'hotel-quote',
    name: 'Hotel',
    total_price: '100.00',
    currency: 'EUR',
    url: null,
    pricing: {
      booking_total: { amount: '100.00', currency: 'EUR' },
      excluded_fee_totals: [{ amount: '12.00', currency: 'EUR' }],
      mandatory_total: { amount: '112.00', currency: 'EUR' },
      status: 'complete',
      unresolved_reasons: [],
      payment_timing: 'unconfirmed',
    },
  };
  it('shows mandatory total and supplier quote separately without claiming a payment amount', () => {
    const html = renderToStaticMarkup(createElement(HotelOfferPrice, { offer }));
    expect(html).toContain('112.00');
    expect(html).toContain('Total including mandatory fees');
    expect(html).toContain('Quoted stay:');
    expect(html).toContain('Additional mandatory fees:');
    expect(html).toContain('Payment timing is unconfirmed');
    expect(html).not.toContain('Due now');
  });
  it('does not combine currencies or present an incomplete quote as a verified total', () => {
    const incomplete = {
      ...offer,
      pricing: {
        ...offer.pricing!,
        status: 'incomplete' as const,
        mandatory_total: null,
        excluded_fee_totals: [{ amount: '30.00', currency: 'PLN' }],
        unresolved_reasons: ['unsupported_budget_currency'],
      },
    };
    const html = renderToStaticMarkup(createElement(HotelOfferPrice, { offer: incomplete }));
    expect(html).toContain('Supplier quote');
    expect(html).toContain('PLN');
    expect(html).toContain('Mandatory total requires verification');
    expect(html).toContain('A sourced currency conversion is required');
    expect(html).not.toContain('unsupported_budget_currency');
    expect(html).not.toContain('Total including mandatory fees');
    expect(html).not.toContain('130.00');
  });
  it('keeps legacy quotes visibly unverified for mandatory fees', () => {
    const html = renderToStaticMarkup(
      createElement(HotelOfferPrice, { offer: { ...offer, pricing: null } }),
    );
    expect(html).toContain('Supplier quote');
    expect(html).toContain('Mandatory total requires verification');
    expect(html).not.toContain('Total including mandatory fees');
  });
});
describe('real-data request boundaries', () => {
  it('uses the Bucharest day and advances over month/year boundaries', () => {
    expect(bucharestDate(new Date('2026-10-30T22:30:00Z'))).toBe('2026-10-31');
    expect(addCalendarDays('2026-12-31', 1)).toBe('2027-01-01');
    expect(addCalendarDays('2026-10-25', 1)).toBe('2026-10-26');
    expect(addCalendarDays('', 1)).toBe('');
    expect(addCalendarDays('2026-02-30', 1)).toBe('');
  });
  it('rejects expired, impossible and contradictory search criteria', () => {
    expect(validateRealDataQuery(query, '2026-10-31')).toBeNull();
    for (const changes of [
      { checkin: '2026-10-30' },
      { checkin: '2026-02-30' },
      { checkout: '2026-10-31' },
      { checkout: '2026-12-01' },
      { destination_code: 'CRA' },
      { rooms: 3 },
      { adults: 2.5 },
      { checkin: '2028-10-01', checkout: '2028-10-02' },
    ])
      expect(validateRealDataQuery({ ...query, ...changes }, '2026-10-31')).not.toBeNull();
  });
  it('only allows non-credentialed web source links', () => {
    expect(safeExternalUrl('https://www.openstreetmap.org/node/42')).toBe(
      'https://www.openstreetmap.org/node/42',
    );
    expect(safeExternalUrl('http://hotel.example/')).toBe('http://hotel.example/');
    for (const value of [
      'javascript:alert(1)',
      'data:text/html,test',
      '//evil.example',
      'https://user:secret@example.com',
      null,
      'invalid',
    ])
      expect(safeExternalUrl(value)).toBeUndefined();
  });
  it('formats only usable provider prices', () => {
    expect(formatOfferPrice('123.45', 'EUR')).toContain('123.45');
    expect(formatOfferPrice('-10', 'EUR')).toBe('Price unavailable');
    expect(formatOfferPrice('not-price', 'EUR')).toBe('Price unavailable');
    expect(formatOfferPrice('', 'EUR')).toBe('Price unavailable');
    expect(formatOfferPrice('42', '<script>')).toBe('Price unavailable');
  });
  it('copies current hotel search details without inventing a Momondo prefill link', () => {
    expect(MOMONDO_HOTELS_URL).toBe('https://www.momondo.com/hotels');
    expect(hotelSearchDetails({ ...query, adults: 1, rooms: 1 }, 'Craiova International')).toBe(
      'Hotels near Craiova International (CRA)\nCheck-in: 2026-10-31\nCheck-out: 2026-11-01\nGuests: 1 adult\nRooms: 1',
    );
    expect(
      hotelSearchDetails({ ...query, adults: 4, rooms: 2 }, 'Craiova International'),
    ).toContain('Guests: 4 adults\nRooms: 2');
  });
  it('never exposes sandbox or failed-supplier prices as live offers', () => {
    const section: RealDataSection<RealHotelOffer> = {
      state: 'ready',
      mode: 'live',
      source: 'Hotel supplier',
      fetched_at: null,
      cached: false,
      message: null,
      items: [{ id: '1', name: 'Hotel', total_price: '120.00', currency: 'EUR', url: null }],
    };
    expect(liveHotelOffers(section)).toHaveLength(1);
    expect(liveHotelOffers({ ...section, mode: 'sandbox' })).toEqual([]);
    expect(liveHotelOffers({ ...section, state: 'sandbox' })).toEqual([]);
    expect(liveHotelOffers({ ...section, state: 'error' })).toEqual([]);
    expect(liveHotelOffers({ ...section, state: 'not_configured' })).toEqual([]);
  });
});
