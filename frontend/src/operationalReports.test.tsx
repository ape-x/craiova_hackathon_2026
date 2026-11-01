import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { api, ApiError, TransportError } from './api';
import type {
  MutationResult,
  Offer,
  PassengerGroup,
  RecoveryIncident,
  Resource,
  World,
} from './types';
import {
  affectedPendingGroups,
  arrivalReportInput,
  arrivalReportIssue,
  latestOperatorReport,
  reportDescription,
  unavailabilityIssue,
  type ArrivalReportForm,
} from './operationalReports';
import {
  ArrivalReportPanel,
  OperatorReportNote,
  ResourceUnavailabilityDialog,
} from './OperationalReportForms';
import { deriveResourceImpact } from './Inspector';

const incident = { id: 'CASE-1', group_ids: ['G1'], kind: 'flight_disruption' } as RecoveryIncident;
const group = {
  id: 'G1',
  name: 'Linked family',
  incident_id: incident.id,
  size: 2,
  origin: 'CRA',
  destination: 'FCO',
  ready_at: 500,
  deadline: 600,
  status: 'offered',
  provenance: {},
} as PassengerGroup;
const resource = {
  id: 'TRANSFER-1',
  name: 'Sourced private transfer',
  kind: 'bus',
  origin: 'CRA',
  destination: 'OTP',
  available: true,
  source: 'operator_confirmed',
  provenance: {},
} as Resource;
const offer = {
  id: 'O1',
  group_id: group.id,
  status: 'proposed',
  expires_at: '2099-01-01T00:00:00Z',
  itinerary: { legs: [{ resource_id: resource.id }] },
} as Offer;
function operation(): World {
  return {
    mode: 'live',
    scenario_id: 'ops',
    version: 9,
    epoch: '2026-11-01T00:00:00Z',
    groups: [structuredClone(group)],
    resources: [structuredClone(resource)],
    incidents: [structuredClone(incident)],
    offers: [structuredClone(offer)],
    reservations: [],
    airports: [{ id: 'CRA', code: 'CRA', name: 'Craiova', kind: 'airport' }],
    events: [],
  } as unknown as World;
}
const form: ArrivalReportForm = {
  groupIds: ['G1'],
  airportCode: 'otp',
  readyAt: '2026-11-01T11:00',
  description: 'Passengers reported ready at the diverted airport.',
};
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('operator reports and booking boundaries', () => {
  it('reports exact selected positions without guessing deadlines, destination or source flight facts', () => {
    const world = operation();
    const before = structuredClone(world);
    expect(arrivalReportInput(world, incident, form)).toEqual({
      group_ids: ['G1'],
      airport_code: 'OTP',
      ready_at: '2026-11-01T11:00:00.000Z',
      description: form.description,
    });
    expect(world).toEqual(before);
    // The new readiness is after the old deadline: reporting reality must not extend it.
    expect(world.groups[0].deadline).toBe(600);
    expect(world.airports.some((airport) => airport.code === 'OTP')).toBe(false);
  });
  it('requires reciprocal case membership and protects any recorded booking or confirmed reservation', () => {
    const world = operation();
    expect(() =>
      arrivalReportInput(world, incident, { ...form, groupIds: ['G1', 'other'] }),
    ).toThrow('no longer present');
    world.groups[0].incident_id = 'OTHER-CASE';
    expect(arrivalReportIssue(world, incident, world.groups[0])).toMatch(/not linked/);
    world.groups[0].incident_id = incident.id;
    world.offers[0].external_bookings = [
      { resource_id: resource.id, confirmation_reference: 'fixture' },
    ];
    expect(arrivalReportIssue(world, incident, world.groups[0])).toMatch(/booking is recorded/);
    expect(unavailabilityIssue(world, resource)).toMatch(/booking is recorded/);
    world.offers[0].external_bookings = [];
    world.reservations = [
      { id: 'R1', group_id: group.id, resource_id: resource.id, status: 'confirmed' },
    ] as World['reservations'];
    expect(arrivalReportIssue(world, incident, world.groups[0])).toMatch(/booking is recorded/);
    expect(unavailabilityIssue(world, resource)).toMatch(/booking is recorded/);
  });
  it.each(['2026-02-30T11:00', '2026-11-01T11:00:01', '2026-11-01T11:00Z', ''])(
    'rejects invalid or non-minute UTC input %s',
    (readyAt) => {
      expect(() => arrivalReportInput(operation(), incident, { ...form, readyAt })).toThrow();
    },
  );
  it('rejects duplicated scope and control characters without silently rewriting an incident', () => {
    expect(() =>
      arrivalReportInput(operation(), incident, { ...form, groupIds: ['G1', 'G1'] }),
    ).toThrow();
    expect(() => reportDescription('Report\nwith hidden control')).toThrow();
    expect(() => reportDescription('ab')).toThrow();
    expect(reportDescription('  Reported breakdown  ')).toBe('Reported breakdown');
  });
  it('counts dependent pending parties once, excluding historical or unrelated proposals', () => {
    const world = operation();
    world.groups.push({ ...group, id: 'G2' });
    world.offers.push(
      { ...offer, id: 'O2', status: 'accepted' },
      { ...offer, id: 'expired', group_id: 'G2', status: 'expired' },
      {
        ...offer,
        id: 'other',
        group_id: 'G2',
        itinerary: {
          ...offer.itinerary,
          legs: [{ ...offer.itinerary.legs[0], resource_id: 'OTHER' }],
        },
      },
    );
    expect(affectedPendingGroups(world, resource.id).map((item) => item.id)).toEqual(['G1']);
  });
  it('renders explicit source, scope and UTC controls; booked parties cannot be selected', () => {
    const world = operation();
    world.groups[0].status = 'confirmed';
    const html = renderToStaticMarkup(
      <ArrivalReportPanel
        world={world}
        incident={incident}
        onApplied={() => {}}
        onRefresh={async () => {}}
        onMutationPending={() => {}}
      />,
    );
    expect(html).toContain('Passengers ready · UTC');
    expect(html).toContain('not a Flightradar24 landing confirmation');
    expect(html).toContain('type="checkbox" disabled=""');
    expect(html).toContain(
      'Report-only changes passenger location/readiness without starting a search',
    );
    expect(html).toContain('Record arrival only');
    expect(html).toContain('Record arrival and recalculate');
    const outage = renderToStaticMarkup(
      <ResourceUnavailabilityDialog
        world={world}
        resourceId={resource.id}
        onApplied={() => {}}
        onRefresh={async () => {}}
        onClose={() => {}}
      />,
    );
    expect(outage).toContain('2 passengers in 1 groups');
    expect(outage).toContain('does not cancel a supplier booking or request a replacement');
  });
  it('retains a dated operator note without interpreting malformed provenance as evidence', () => {
    expect(latestOperatorReport({ arrival_reports: 'malformed' }, 'arrival_reports')).toBeNull();
    expect(
      latestOperatorReport({ arrival_reports: [{ source: 'Flightradar24' }] }, 'arrival_reports'),
    ).toBeNull();
    const html = renderToStaticMarkup(
      <OperatorReportNote
        field="unavailability_reports"
        provenance={{
          unavailability_reports: [
            {
              source: 'operator_report',
              received_at: '2026-11-01T11:15:00Z',
              description: 'Vehicle reported unavailable.',
            },
          ],
        }}
      />,
    );
    expect(html).toContain('Operator report');
    expect(html).toContain('UTC');
    expect(html).toContain('Vehicle reported unavailable.');
  });
  it('uses recorded outage dependency IDs for historic impact instead of current allocations', () => {
    const world = operation();
    world.resources[0].available = false;
    world.offers[0].status = 'invalidated';
    world.events = [
      {
        id: 'E1',
        type: 'operator_resource_unavailability',
        timestamp: '2026-11-01T11:00:00Z',
        description: 'Operator reported this service unavailable.',
        target_id: resource.id,
        version: 9,
        data: { affected_offer_ids: ['O1', 'missing'], affected_group_ids: ['G1', 'not-affected'] },
      },
    ] as World['events'];
    const impact = deriveResourceImpact(world, world.resources[0]);
    expect(impact).not.toBeNull();
    expect(impact?.affected).toBe(2);
  });
});

describe('versioned report submission', () => {
  it.each(['arrival', 'unavailable'] as const)(
    'recovers an uncertain %s with the original ID, payload and scope',
    async (kind) => {
      const world = operation();
      const fetch = vi
        .fn()
        .mockResolvedValueOnce(new Response('connection lost', { status: 502 }))
        .mockResolvedValueOnce(
          new Response(JSON.stringify({ world, applied: true, replayed: true })),
        );
      vi.stubGlobal('fetch', fetch);
      const input = arrivalReportInput(world, incident, form);
      let failure: unknown;
      try {
        if (kind === 'arrival') await api.reportArrival(world, incident.id, input);
        else await api.reportUnavailability(world, resource.id, { description: 'Vehicle failed' });
      } catch (cause) {
        failure = cause;
      }
      expect(failure).toBeInstanceOf(TransportError);
      world.version = 99;
      input.group_ids.push('another-party');
      const result = await (failure as TransportError).retry<MutationResult>();
      expect(result.replayed).toBe(true);
      expect(fetch.mock.calls[1][1].body).toBe(fetch.mock.calls[0][1].body);
      expect(JSON.parse(fetch.mock.calls[1][1].body)).toMatchObject({
        expected_version: 9,
        idempotency_key: expect.any(String),
      });
      expect(fetch.mock.calls[0][0]).toBe(
        kind === 'arrival'
          ? '/api/incidents/CASE-1/arrival-report'
          : '/api/live/resources/TRANSFER-1/unavailability',
      );
      if (kind === 'arrival')
        expect(JSON.parse(fetch.mock.calls[1][1].body).group_ids).toEqual(['G1']);
    },
  );
  it('surfaces a definitive booking/version conflict from a same-ID recovery', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response('unconfirmed', { status: 502 }))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ detail: 'Booking recorded; report rejected', applied: false }),
          { status: 409 },
        ),
      );
    vi.stubGlobal('fetch', fetch);
    try {
      await api.reportUnavailability(operation(), resource.id, { description: 'Failed transfer' });
    } catch (cause) {
      expect(cause).toBeInstanceOf(TransportError);
      await expect((cause as TransportError).retry()).rejects.toBeInstanceOf(ApiError);
    }
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(fetch.mock.calls[1][1].body).toBe(fetch.mock.calls[0][1].body);
  });
});
