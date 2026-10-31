import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { api, ApiError, downloadSession, TransportError } from './api';
import { CaseSummary, FlightFacts } from './RecoveryCases';
import RecoveryCaseDialog from './RecoveryCaseDialog';
import {
  flightLookupCurrent,
  groupBindingIssue,
  selectedFlight,
  utcTimestamp,
  validateFlightQuery,
} from './recoveryCase';
import type {
  FlightLookup,
  FlightSelection,
  IncidentInput,
  PassengerGroup,
  RecoveryIncident,
  World,
} from './types';

const now = Date.parse('2026-11-01T10:00:00Z');
const flight: FlightSelection = {
  selection_id: 'opaque-leg-a',
  fr24_id: 'fr24-123',
  flight_number: 'W43001',
  callsign: 'WMT3AB',
  registration: 'HA-ABC',
  aircraft_type: 'A320',
  origin: { iata: 'CRA', icao: 'LRCV', catalog_code: 'CRA' },
  intended_destination: { iata: 'FCO', icao: 'LIRF', catalog_code: 'FCO' },
  actual_destination: null,
  first_seen: '2026-11-01T07:00:00Z',
  last_seen: '2026-11-01T08:00:00Z',
  takeoff_at: null,
  landed_at: null,
  flight_ended: true,
  status: 'ended',
  freshness: 'historical',
};
const lookup: FlightLookup = {
  state: 'ready',
  lookup_id: 'lookup-a',
  source: 'Flightradar24',
  queried_at: '2026-11-01T09:59:00Z',
  expires_at: '2026-11-01T10:05:00Z',
  cached: false,
  message: null,
  matches: [flight, { ...flight, selection_id: 'opaque-leg-b', fr24_id: 'fr24-456' }],
};
const group = {
  id: 'G1',
  name: 'Planning party',
  size: 3,
  original_flight_id: 'W4 3001',
  source: 'synthetic',
  incident_id: null,
} as PassengerGroup;
const incident: RecoveryIncident = {
  id: 'CASE-1',
  created_at: '2026-11-01T10:00:00Z',
  source: 'operator_report',
  kind: 'destination_unavailable',
  description: 'Operator reports destination unavailable.',
  requested_flight_number: 'W43001',
  date_utc: '2026-11-01',
  flight_ref: { lookup_id: 'lookup-a', selection_id: 'opaque-leg-a' },
  unavailable_airport_code: 'FCO',
  group_ids: [],
  status: 'needs_passenger_data',
  flight,
  flight_context_state: 'available',
};
const world = {
  scenario_id: 'operation',
  version: 8,
  mode: 'live',
  groups: [group],
  incidents: [incident],
} as World;
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('flight lookup and incident boundaries', () => {
  it('omits transient provider facts and passenger tokens from the actual portable audit download', async () => {
    let blob: Blob | null = null;
    const anchor = { href: '', download: '', click: vi.fn() };
    vi.stubGlobal('document', { createElement: () => anchor });
    vi.spyOn(URL, 'createObjectURL').mockImplementation((value) => {
      blob = value as Blob;
      return 'blob:case-audit';
    });
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    downloadSession({
      ...world,
      groups: [{ ...group, token: 'private-passenger-token' }],
      observations: [{ id: 'raw-observation' }],
    });
    const saved = JSON.parse(await (blob! as Blob).text());
    expect(anchor.click).toHaveBeenCalledOnce();
    expect(saved.incidents[0]).toMatchObject({
      id: 'CASE-1',
      description: incident.description,
      flight_ref: incident.flight_ref,
      flight: null,
      flight_context_state: 'expired',
    });
    expect(saved.observations).toEqual([]);
    expect(JSON.stringify(saved)).not.toMatch(/HA-ABC|raw-observation|private-passenger-token/);
    expect(world.incidents?.[0].flight?.registration).toBe('HA-ABC');
  });
  it('requires an explicit exact-leg choice, a usable lookup and an unexpired source token', () => {
    expect(selectedFlight(lookup, '', now)).toBeNull();
    expect(selectedFlight(lookup, 'unknown', now)).toBeNull();
    expect(selectedFlight(lookup, 'opaque-leg-b', now)?.fr24_id).toBe('fr24-456');
    for (const changes of [
      { state: 'no_match' as const },
      { state: 'error' as const },
      { lookup_id: null },
      { expires_at: 'invalid' },
      { expires_at: '2026-11-01T10:00:00Z' },
    ])
      expect(selectedFlight({ ...lookup, ...changes }, 'opaque-leg-a', now)).toBeNull();
    expect(flightLookupCurrent({ ...lookup, matches: [] }, now)).toBe(true);
  });
  it('uses explicit UTC dates and rejects impossible calendar input without guessing a date', () => {
    expect(validateFlightQuery('w4 3001', '2026-11-01', '2026-11-01')).toBeNull();
    for (const date of ['', '2026-02-30', '11/01/2026'])
      expect(validateFlightQuery('W43001', date, '2026-11-01')).not.toBeNull();
    for (const date of ['2022-05-31', '2026-11-02'])
      expect(validateFlightQuery('W43001', date, '2026-11-01')).not.toBeNull();
    for (const number of ['123456', 'W4/3001', 'W4<script>'])
      expect(validateFlightQuery(number, '2026-11-01', '2026-11-01')).not.toBeNull();
    expect(validateFlightQuery('', '2026-11-01')).not.toBeNull();
    expect(utcTimestamp('2026-11-01T00:30:00+02:00')).toContain('31 Oct 2026');
    expect(utcTimestamp(null)).toBe('Unknown');
  });
  it('matches explicit flight identifiers rather than route similarity and rejects another case’s groups', () => {
    expect(groupBindingIssue(group, flight, 'W43001')).toBeNull();
    expect(groupBindingIssue({ ...group, status: 'confirmed' }, flight, 'W43001')).toContain(
      'supplier bookings',
    );
    expect(
      groupBindingIssue({ ...group, original_flight_id: 'WMT3AB' }, flight, 'W43001'),
    ).toBeNull();
    expect(
      groupBindingIssue({ ...group, original_flight_id: 'OTHER' }, flight, 'W43001'),
    ).toContain('does not match');
    expect(
      groupBindingIssue({ ...group, incident_id: 'CASE-2' }, flight, 'W43001', 'CASE-1'),
    ).toContain('another case');
    expect(
      groupBindingIssue({ ...group, incident_id: 'CASE-1' }, flight, 'W43001', 'CASE-1'),
    ).toBeNull();
  });
  it('does not translate ended tracking, historical data or missing destination into cancellation or capacity', () => {
    const html = renderToStaticMarkup(<FlightFacts flight={flight} />);
    expect(html).toContain('Tracking ended');
    expect(html).toContain('HISTORICAL');
    expect(html).toContain('Actual destination reported</dt><dd>Unknown');
    expect(html).not.toMatch(/cancelled|available seats|confirmed arrival/i);
  });
  it('keeps an operator’s case visible after flight context expires, without claiming available supply', () => {
    const html = renderToStaticMarkup(
      <CaseSummary
        world={world}
        incident={{ ...incident, flight: null, flight_context_state: 'expired' }}
      />,
    );
    expect(html).toContain('NEEDS PASSENGER DATA');
    expect(html).toContain('Operator-reported constraint');
    expect(html).toContain('Flight context has expired');
    expect(html).toContain('does not establish available seats');
    expect(html).not.toContain('HA-ABC');
  });
  it('renders an unselected new-case form and preserves passenger-data access on a saved empty case', () => {
    const props = {
      world: { ...world, groups: [] },
      onApplied: () => {},
      onRefresh: async () => {},
      onClose: () => {},
      onManageData: () => {},
    };
    const fresh = renderToStaticMarkup(<RecoveryCaseDialog {...props} incidentId={null} />);
    expect(fresh).toContain('First tracked date (UTC)');
    expect(fresh).toContain('Find flight');
    expect(fresh).not.toContain('Save recovery case');
    const saved = renderToStaticMarkup(<RecoveryCaseDialog {...props} incidentId="CASE-1" />);
    expect(saved).toContain('NEEDS PASSENGER DATA');
    expect(saved).toContain('Manage passenger requirements &amp; services');
    expect(saved).toContain('No passenger groups have been recorded');
  });
  it('preserves original incident identity and payload when retrying an unknown outcome', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response('interrupted', { status: 502 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: 'Source lookup expired', applied: false }), {
          status: 409,
        }),
      );
    vi.stubGlobal('fetch', fetch);
    const input: IncidentInput = {
      lookup_id: 'lookup-a',
      selection_id: 'opaque-leg-a',
      kind: 'flight_disruption',
      description: 'Operator report.',
      affected_group_ids: ['G1'],
    };
    let failure: unknown;
    try {
      await api.createIncident(world, input);
    } catch (cause) {
      failure = cause;
    }
    expect(failure).toBeInstanceOf(TransportError);
    input.selection_id = 'opaque-leg-b';
    input.affected_group_ids.push('G2');
    await expect((failure as TransportError).retry()).rejects.toBeInstanceOf(ApiError);
    expect(fetch.mock.calls[1][0]).toBe('/api/incidents');
    expect(fetch.mock.calls[1][1].body).toBe(fetch.mock.calls[0][1].body);
    expect(JSON.parse(fetch.mock.calls[1][1].body)).toMatchObject({
      expected_version: 8,
      selection_id: 'opaque-leg-a',
      affected_group_ids: ['G1'],
    });
  });
  it('keeps source lookup, explicit group binding and exact-leg refresh as separate requests', async () => {
    const fetch = vi
      .fn()
      .mockImplementation(
        async () => new Response(JSON.stringify({ world, incident_id: 'CASE-1' })),
      );
    vi.stubGlobal('fetch', fetch);
    await api.lookupFlight({ flight_number: 'W43001', date_utc: '2026-11-01' });
    await api.bindIncidentGroups(world, 'CASE/1', ['G1']);
    await api.refreshIncidentFlight(world, 'CASE/1', 'lookup-new', 'opaque-leg-a');
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
      flight_number: 'W43001',
      date_utc: '2026-11-01',
    });
    expect(fetch.mock.calls[1][0]).toBe('/api/incidents/CASE%2F1/groups');
    expect(JSON.parse(fetch.mock.calls[1][1].body)).toMatchObject({
      group_ids: ['G1'],
      expected_version: 8,
    });
    expect(fetch.mock.calls[2][0]).toBe('/api/incidents/CASE%2F1/refresh-flight');
    expect(JSON.parse(fetch.mock.calls[2][1].body)).toMatchObject({
      lookup_id: 'lookup-new',
      selection_id: 'opaque-leg-a',
      expected_version: 8,
    });
    expect(JSON.parse(fetch.mock.calls[1][1].body).idempotency_key).not.toBe(
      JSON.parse(fetch.mock.calls[2][1].body).idempotency_key,
    );
  });
});
