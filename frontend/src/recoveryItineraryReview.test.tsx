import type { ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import Inspector from './Inspector';
import { ReviewRecoveryItinerary, RecoveryJobDetails } from './RecoveryJobPanel';
import { emptyHotelRecoveryForm } from './hotelRecovery';
import { restoreRecoveryFormDraft } from './recoveryFormDraft';
import {
  prepareRecoveryItineraryNavigation,
  preservesItineraryReviewOnClose,
  recoveryItineraryMapSelection,
  recoveryItineraryTarget,
  resolveRecoveryItinerary,
} from './recoveryItineraryReview';
import type { RecoveryItineraryIntent } from './recoveryItineraryReview';
import type { RecoveryDataContext } from './recoveryDataContext';
import type { Offer, RecoveryJob, World } from './types';

const NOW = Date.parse('2030-11-01T12:00:00Z');
function fixture() {
  const offer: Offer = {
    id: 'O1',
    group_id: 'G1',
    status: 'proposed',
    created_version: 4,
    expires_at: '2030-11-01T12:02:00Z',
    confirmed_at: null,
    reason: null,
    itinerary: {
      legs: [
        {
          resource_id: 'EXACT-LEG',
          kind: 'flight',
          origin: 'LHR',
          destination: 'CRA',
          departure: 800,
          arrival: 1000,
          cost: 200,
        },
      ],
      arrival: 1000,
      total_cost: 200,
    },
  };
  const world = {
    mode: 'live',
    scenario_id: 'OP1',
    version: 4,
    epoch: '2030-11-01T00:00:00Z',
    sim_time: 720,
    currency: 'EUR',
    budget: 1000,
    groups: ['G1', 'G2'].map((id) => ({
      id,
      name: `Party ${id}`,
      incident_id: 'CASE1',
      size: 2,
      members: [],
      adult_count: 2,
      origin: 'LHR',
      destination: 'CRA',
      ready_at: 720,
      deadline: 2880,
      accessible: false,
      status: 'waiting',
      budget: 1000,
      original_flight_id: 'W43001',
      token: 'token',
      priority: 1,
    })),
    incidents: [{ id: 'CASE1', group_ids: ['G1', 'G2'], flight_context_state: 'expired' }],
    offers: [offer],
    airports: [],
    resources: [],
    reservations: [],
    events: [],
    decisions: [],
    observations: [],
    stats: {},
    provider_status: {},
  } as unknown as World;
  const job: RecoveryJob = {
    id: 'JOB1',
    incident_id: 'CASE1',
    status: 'completed',
    stage: 'finished',
    group_ids: ['G1'],
    objective: 'balanced',
    modes: ['flight'],
    max_provider_calls: 8,
    calls_dispatched: 3,
    source_version: 3,
    applied_version: 4,
    created_at: '2030-11-01T11:59:00Z',
    updated_at: '2030-11-01T12:00:00Z',
    message: 'Proposal recorded.',
    cancel_requested: false,
    steps: [],
    gaps: [],
    result: {
      decision_id: 'D1',
      plan_id: 'P1',
      recovered_passengers: 2,
      exception_passengers: 0,
      offer_ids: ['O1'],
      resource_ids: ['EXACT-LEG'],
      source_counts: { Supplier: 1 },
    },
  };
  return { world, job };
}
function intent(world: World, job: RecoveryJob): RecoveryItineraryIntent {
  const target = recoveryItineraryTarget(world, 'CASE1', job, 'O1', NOW);
  if (target.kind !== 'ready') throw new Error(target.reason);
  return target.intent;
}
function context(): RecoveryDataContext {
  return {
    scenarioId: 'OP1',
    incidentId: 'CASE1',
    groupIds: ['G2'],
    searchDraft: {
      groupIds: ['G2'],
      objective: 'lowest_cost',
      modes: ['bus', 'flight'],
      limit: '03',
      instructions: '  Keep my original draft.\nNo automatic edits.  ',
      hotelMode: false,
      hotelFields: { ...emptyHotelRecoveryForm(), rooms: '02', checkout_at: 'unfinished' },
      airportTransfers: [
        {
          group_id: 'G2',
          scope: 'original',
          pickup_at: 'old pickup',
          suitcases: '',
          handbags: '02',
        },
      ],
      afterFlightTransfer: {
        group_id: 'G2',
        scope: 'original after-flight',
        pickup_window_start: 'old start',
        pickup_window_end: '',
        airport_processing_minutes: '025',
        suitcases: '0',
        handbags: '',
        max_inbound_options: '1',
      },
    },
  };
}
const renderInspector = (world: World, reviewed: RecoveryItineraryIntent) =>
  renderToStaticMarkup(
    <Inspector
      world={world}
      selectedId="G1"
      onSelect={() => {}}
      onClose={() => {}}
      onReplan={() => {}}
      onGroupRecovery={() => {}}
      onScenario={() => {}}
      busy={false}
      config={{ public_base_url: 'https://example.invalid' }}
      showLog={false}
      itineraryReview={reviewed}
      onReturnToRecoveryCase={() => {}}
    />,
  );
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('exact completed recovery itinerary navigation', () => {
  it('opens the exact current proposal and geographic group focus without sourcing anything', () => {
    vi.spyOn(Date, 'now').mockReturnValue(NOW);
    const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    const { world, job } = fixture();
    const original = structuredClone(world);
    const selected = intent(world, job);
    expect(resolveRecoveryItinerary(world, selected, NOW)).toMatchObject({
      kind: 'ready',
      offer: { id: 'O1' },
      group: { id: 'G1' },
    });
    expect(recoveryItineraryMapSelection(world, 'G1', selected, NOW)).toBe('G1');
    const onReview = vi.fn();
    const button = ReviewRecoveryItinerary({
      world,
      incidentId: 'CASE1',
      job,
      offerId: 'O1',
      onReview,
    }) as ReactElement<{ type: string; onClick: () => void }>;
    expect(button.type).toBe('button');
    expect(button.props.type).toBe('button');
    button.props.onClick();
    expect(onReview).toHaveBeenCalledExactlyOnceWith(selected);
    const html = renderToStaticMarkup(
      <RecoveryJobDetails
        world={world}
        job={job}
        incidentId="CASE1"
        onReviewItinerary={onReview}
      />,
    );
    expect(html).toContain('Review itinerary · Party G1 · O1');
    expect(renderInspector(world, selected)).toContain('EXACT-LEG');
    expect(world).toEqual(original);
    expect(fetcher).not.toHaveBeenCalled();
  });
  it.each([
    'missing',
    'superseded',
    'expired',
    'invalid_expiry',
    'foreign_group',
    'foreign_case',
    'revision',
    'duplicate',
  ] as const)('does not expose a current action when the recorded offer is %s', (change) => {
    vi.spyOn(Date, 'now').mockReturnValue(NOW);
    const { world, job } = fixture();
    if (change === 'missing') world.offers = [];
    if (change === 'superseded') world.offers[0].status = 'superseded';
    if (change === 'expired') world.offers[0].expires_at = new Date(NOW).toISOString();
    if (change === 'invalid_expiry') world.offers[0].expires_at = 'not a date';
    if (change === 'foreign_group') world.offers[0].group_id = 'G2';
    if (change === 'foreign_case') world.groups[0].incident_id = 'OTHER';
    if (change === 'revision') world.offers[0].created_version = 3;
    if (change === 'duplicate') world.offers.push(structuredClone(world.offers[0]));
    expect(recoveryItineraryTarget(world, 'CASE1', job, 'O1', NOW).kind).toBe('unavailable');
    const html = renderToStaticMarkup(
      <ReviewRecoveryItinerary
        world={world}
        incidentId="CASE1"
        job={job}
        offerId="O1"
        onReview={() => {}}
      />,
    );
    expect(html).toContain('O1');
    expect(html).not.toContain('<button');
  });
  it('requires the completed job, exact result reference and matching case before creating an intent', () => {
    const { world, job } = fixture();
    expect(recoveryItineraryTarget(world, 'OTHER', job, 'O1', NOW).kind).toBe('unavailable');
    expect(
      recoveryItineraryTarget(world, 'CASE1', { ...job, status: 'blocked' }, 'O1', NOW).kind,
    ).toBe('unavailable');
    expect(
      recoveryItineraryTarget(
        world,
        'CASE1',
        { ...job, result: { ...job.result!, offer_ids: ['OTHER'] } },
        'O1',
        NOW,
      ).kind,
    ).toBe('unavailable');
    expect(
      recoveryItineraryTarget(world, 'CASE1', { ...job, applied_version: null }, 'O1', NOW).kind,
    ).toBe('unavailable');
  });
  it('keeps a replaced proposal pinned as stale across Inspector rerenders instead of showing a newer itinerary', () => {
    vi.spyOn(Date, 'now').mockReturnValue(NOW);
    const { world, job } = fixture();
    const selected = intent(world, job);
    expect(renderInspector(world, selected)).toContain('EXACT-LEG');
    const newer = structuredClone(world.offers[0]);
    newer.id = 'NEW-OFFER';
    newer.created_version = 5;
    newer.itinerary.legs[0].resource_id = 'NEWER-LEG';
    world.version = 5;
    world.offers.push(newer);
    const html = renderInspector(world, selected);
    expect(html).toContain('Another proposal is now selected');
    expect(html).toContain('Return to recovery case');
    expect(html).not.toContain('NEWER-LEG');
    expect(html).not.toContain('EXACT-LEG');
    expect(recoveryItineraryMapSelection(world, 'G1', selected, NOW)).toBeNull();
    expect(recoveryItineraryMapSelection(world, 'G2', selected, NOW)).toBe('G2');
    expect(resolveRecoveryItinerary({ ...world, scenario_id: 'OTHER' }, selected, NOW).kind).toBe(
      'unavailable',
    );
  });
  it('checks the clock again on click and respects pending/unknown mutation locks', () => {
    const clock = vi.spyOn(Date, 'now').mockReturnValue(NOW);
    const { world, job } = fixture();
    const onReview = vi.fn();
    const disabled = ReviewRecoveryItinerary({
      world,
      incidentId: 'CASE1',
      job,
      offerId: 'O1',
      onReview,
      disabled: true,
    }) as ReactElement<{ disabled: boolean; onClick: () => void }>;
    expect(disabled.props.disabled).toBe(true);
    disabled.props.onClick();
    const button = ReviewRecoveryItinerary({
      world,
      incidentId: 'CASE1',
      job,
      offerId: 'O1',
      onReview,
    }) as ReactElement<{ onClick: () => void }>;
    clock.mockReturnValue(NOW + 120_000);
    button.props.onClick();
    expect(onReview).not.toHaveBeenCalled();
    expect(
      prepareRecoveryItineraryNavigation(world, intent(world, job), context(), true, NOW).kind,
    ).toBe('unavailable');
  });
  it('keeps supplier-confirmed journeys reviewable after their earlier proposal expiry', () => {
    const { world, job } = fixture();
    world.offers[0].status = 'confirmed';
    expect(recoveryItineraryTarget(world, 'CASE1', job, 'O1', NOW + 120_001).kind).toBe('ready');
  });
  it('retains the separate unsent form, exact call cap and incomplete inputs when reviewing another group', () => {
    const { world, job } = fixture();
    const saved = context();
    const prepared = prepareRecoveryItineraryNavigation(
      world,
      intent(world, job),
      saved,
      false,
      NOW,
    );
    if (prepared.kind !== 'ready') throw new Error(prepared.reason);
    expect(prepared.group.id).toBe('G1');
    expect(prepared.context.groupIds).toEqual(['G2']);
    expect(prepared.context).toEqual(saved);
    expect(prepared.context).not.toBe(saved);
    saved.searchDraft!.limit = '26';
    saved.searchDraft!.afterFlightTransfer!.suitcases = '7';
    expect(prepared.context.searchDraft!.limit).toBe('03');
    const restored = restoreRecoveryFormDraft(world, world.incidents![0], prepared.context);
    expect(restored.issue).toBeNull();
    expect(restored.draft).toEqual(context().searchDraft);
    expect(
      prepareRecoveryItineraryNavigation(
        world,
        intent(world, job),
        { ...context(), scenarioId: 'OTHER' },
        false,
        NOW,
      ).kind,
    ).toBe('unavailable');
  });
  it('retains itinerary/draft review across auxiliary modal closure and clears only explicit workflow exits', () => {
    for (const modal of ['search', 'settings', 'help', 'voice-review', null])
      expect(preservesItineraryReviewOnClose(modal)).toBe(true);
    for (const modal of ['recovery-case', 'live-data', 'recovery-picker'])
      expect(preservesItineraryReviewOnClose(modal)).toBe(false);
  });
});
