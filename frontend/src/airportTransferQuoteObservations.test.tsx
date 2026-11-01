import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import AirportTransferQuoteObservations from './AirportTransferQuoteObservations';
import { RecoveryJobDetails } from './RecoveryJobPanel';
import type { AirportTransferQuoteSummary, RecoveryJob, World } from './types';

const world = {
  groups: [{ id: 'G1', name: 'Existing group' }],
  offers: [],
  resources: [],
} as unknown as World;
function observation(): AirportTransferQuoteSummary {
  return {
    group_id: 'G1',
    quote_id: 'CAB-Q1',
    quote_sequence: 2,
    pickup_at: '2030-11-02T14:00:00Z',
    usable_until: '2030-11-01T12:02:00Z',
    state: 'ready',
    arrival_estimated: true,
    supplier_booked: false,
    http_request_count: 2,
    blockers: [],
    options: [
      {
        id: 'CAR1',
        car_type: 'Sedan',
        price: { amount: '191.25', currency: 'EUR' },
        max_passengers: 3,
        max_suitcases: 2,
        max_handbags: 3,
        fits_request: true,
        importable: true,
      },
      {
        id: 'CAR2',
        car_type: 'Compact',
        price: { amount: '180.00', currency: 'EUR' },
        max_passengers: 2,
        max_suitcases: 1,
        max_handbags: 1,
        fits_request: false,
        importable: false,
      },
    ],
  };
}
describe('historical airport-transfer fare observations', () => {
  it('shows original native EUR cents and only eligibility at receipt, without action controls', () => {
    const html = renderToStaticMarkup(
      <AirportTransferQuoteObservations quotes={[observation()]} world={world} />,
    );
    for (const text of [
      'Existing group · OTP',
      'Requested pickup 02 Nov 2030, 14:00:00 UTC',
      '01 Nov 2030, 12:02:00 UTC',
      'historical application eligibility',
      'Sedan · €191.25 per vehicle',
      'Compact · €180.00 per vehicle',
      'Party and bag fit at receipt: Yes',
      'Planning eligibility at receipt: No',
      'Fleet availability is unknown',
      'these quotes made no reservation',
      'do not establish a complete journey',
      'Source state at receipt: ready',
      'category limits, not remaining fleet',
    ])
      expect(html).toContain(text);
    expect(html).not.toMatch(/<button|<a |Within freshness|Currently available|ECB|GBP|£/);
  });
  it('retains returned fare categories on a blocked recovery without claiming recovered passengers', () => {
    const recorded = observation();
    recorded.state = 'blocked';
    recorded.options.forEach((option) => {
      option.importable = false;
    });
    recorded.blockers = [
      { code: 'deadline', message: 'No complete onward itinerary fits the deadline.' },
    ];
    const job = {
      id: 'J1',
      incident_id: 'CASE1',
      group_ids: ['G1'],
      objective: 'balanced',
      modes: ['bus'],
      max_provider_calls: 5,
      status: 'blocked',
      stage: 'finished',
      calls_dispatched: 2,
      message: 'Recovery blocked',
      created_at: '2030-11-01T12:00:00Z',
      updated_at: '2030-11-01T12:01:00Z',
      airport_transfer_quotes: [recorded],
      result: null,
      gaps: [],
      steps: [],
    } as unknown as RecoveryJob;
    const html = renderToStaticMarkup(<RecoveryJobDetails job={job} world={world} />);
    expect(html).toContain('Recorded Bucharest.CAB fare observations');
    expect(html).toContain('€191.25 per vehicle');
    expect(html).toContain('No complete onward itinerary fits the deadline.');
    expect(html).toContain('Source state at receipt: blocked');
    expect(html).not.toContain('Passengers recovered');
  });
  it('does not invent native EUR pricing for malformed original money or currency', () => {
    const recorded = observation();
    recorded.options[0].price.amount = '191.255';
    (recorded.options[1].price as { currency: string }).currency = 'RON';
    const html = renderToStaticMarkup(
      <AirportTransferQuoteObservations quotes={[recorded]} world={world} />,
    );
    expect(html.match(/Native EUR fare unavailable/g)).toHaveLength(2);
    expect(html).not.toContain('€191.255');
    expect(html).not.toContain('€180.00');
  });
  it('renders missing groups by exact ID and reports an empty source result', () => {
    const recorded = observation();
    recorded.group_id = 'REMOVED-GROUP';
    recorded.options = [];
    recorded.state = 'blocked';
    const html = renderToStaticMarkup(
      <AirportTransferQuoteObservations quotes={[recorded]} world={world} />,
    );
    expect(html).toContain('REMOVED-GROUP');
    expect(html).toContain('No vehicle categories recorded');
    expect(
      renderToStaticMarkup(<AirportTransferQuoteObservations quotes={[]} world={world} />),
    ).toBe('');
  });
});
