import { activeOffer } from './api';
import { groupRecoveryEntry } from './groupRecoveryEntry';
import { resolveRecoveryDataContext, type RecoveryDataContext } from './recoveryDataContext';
import type { Offer, PassengerGroup, RecoveryJob, World } from './types';

export interface RecoveryItineraryIntent {
  scenarioId: string;
  sourceVersion: number;
  incidentId: string;
  jobId: string;
  appliedVersion: number;
  offerId: string;
  groupId: string;
}
export type RecoveryItineraryReview =
  | { kind: 'ready'; intent: RecoveryItineraryIntent; offer: Offer; group: PassengerGroup }
  | { kind: 'unavailable'; reason: string };

export function preservesItineraryReviewOnClose(modal: string | null): boolean {
  return !['recovery-case', 'live-data', 'recovery-picker'].includes(modal ?? '');
}

export function recoveryItineraryMapSelection(
  world: World,
  selectedId: string | null,
  intent?: RecoveryItineraryIntent,
  now = Date.now(),
): string | null {
  return intent &&
    selectedId === intent.groupId &&
    resolveRecoveryItinerary(world, intent, now).kind !== 'ready'
    ? null
    : selectedId;
}

/** Resolve the exact recorded proposal again; never substitute the group's newest offer. */
export function resolveRecoveryItinerary(
  world: World,
  intent: RecoveryItineraryIntent,
  now = Date.now(),
): RecoveryItineraryReview {
  const unavailable = (reason: string): RecoveryItineraryReview => ({
    kind: 'unavailable',
    reason,
  });
  if (
    world.mode !== 'live' ||
    world.scenario_id !== intent.scenarioId ||
    !Number.isSafeInteger(intent.sourceVersion) ||
    world.version < intent.sourceVersion ||
    !Number.isSafeInteger(intent.appliedVersion) ||
    intent.appliedVersion < 1 ||
    intent.appliedVersion > intent.sourceVersion
  )
    return unavailable('This proposal belongs to a different or unavailable operation revision.');
  const entry = groupRecoveryEntry(world, intent.groupId);
  if (!entry || entry.intent.kind === 'setup' || entry.intent.incidentId !== intent.incidentId)
    return unavailable('The original passenger group is no longer uniquely linked to this case.');
  const offers = world.offers.filter((offer) => offer.id === intent.offerId);
  if (offers.length !== 1)
    return unavailable('This recorded proposal is no longer available in the current operation.');
  const offer = offers[0];
  if (offer.group_id !== intent.groupId || offer.created_version !== intent.appliedVersion)
    return unavailable('The recorded proposal no longer matches this recovery result.');
  if (!['held', 'proposed', 'accepted', 'confirmed'].includes(offer.status))
    return unavailable(`This recorded proposal is ${offer.status}; no replacement was selected.`);
  if (
    offer.status !== 'confirmed' &&
    (!Number.isFinite(Date.parse(offer.expires_at)) || Date.parse(offer.expires_at) <= now)
  )
    return unavailable(
      'This recorded proposal has expired. Review the case for current alternatives.',
    );
  if (activeOffer(world, intent.groupId)?.id !== offer.id)
    return unavailable(
      'Another proposal is now selected for this group. Review the case before opening it.',
    );
  return {
    kind: 'ready',
    intent,
    offer,
    group: world.groups.find((group) => group.id === intent.groupId)!,
  };
}

/** Only a successful attempt can introduce an offer-review navigation intent. */
export function recoveryItineraryTarget(
  world: World,
  incidentId: string,
  job: RecoveryJob,
  offerId: string,
  now = Date.now(),
): RecoveryItineraryReview {
  const offer = world.offers.find((item) => item.id === offerId);
  if (
    job.status !== 'completed' ||
    job.incident_id !== incidentId ||
    job.result?.offer_ids.filter((id) => id === offerId).length !== 1 ||
    !offer ||
    !job.group_ids.includes(offer.group_id) ||
    job.applied_version === null
  )
    return {
      kind: 'unavailable',
      reason: 'The exact completed proposal is not available for review in this case.',
    };
  return resolveRecoveryItinerary(
    world,
    {
      scenarioId: world.scenario_id,
      sourceVersion: world.version,
      incidentId,
      jobId: job.id,
      appliedVersion: job.applied_version,
      offerId,
      groupId: offer.group_id,
    },
    now,
  );
}

/** Inspection and the unsent search selection are independent scopes. */
export function prepareRecoveryItineraryNavigation(
  world: World,
  intent: RecoveryItineraryIntent,
  context: RecoveryDataContext,
  pending: boolean,
  now = Date.now(),
):
  | (Extract<RecoveryItineraryReview, { kind: 'ready' }> & { context: RecoveryDataContext })
  | Extract<RecoveryItineraryReview, { kind: 'unavailable' }> {
  if (pending)
    return {
      kind: 'unavailable',
      reason: 'Finish or recover the pending submission before reviewing the itinerary.',
    };
  const target = resolveRecoveryItinerary(world, intent, now);
  if (target.kind !== 'ready') return target;
  const scope = resolveRecoveryDataContext(world, context);
  if (
    scope.kind !== 'ready' ||
    context.scenarioId !== intent.scenarioId ||
    context.incidentId !== intent.incidentId
  )
    return {
      kind: 'unavailable',
      reason: 'The original search draft scope changed. Review the case before leaving it.',
    };
  if (
    context.searchDraft &&
    (context.searchDraft.groupIds.length !== context.groupIds.length ||
      context.searchDraft.groupIds.some((id, index) => id !== context.groupIds[index]))
  )
    return {
      kind: 'unavailable',
      reason:
        'The saved search selection does not match its draft. Review the case before leaving it.',
    };
  return { ...target, context: structuredClone(context) };
}
