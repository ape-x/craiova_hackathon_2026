import type {
  AirportTransferProfile,
  AirportTransferProfiles,
  RecoveryIncident,
  World,
} from './types';
import {
  airportTransferGroupIssue,
  airportTransferScope,
  type AirportTransferDraft,
} from './airportTransferRecovery';
import { sourceInstant } from './resourceFreshness';

export function airportTransferProfileReason(reason: string): string {
  switch (reason) {
    case 'missing':
      return 'No saved transfer inputs for this group. Enter pickup and bag totals in the form above.';
    case 'stale':
      return 'Saved inputs belong to a different party, route or case. Enter fresh transfer inputs for this group.';
    case 'invalid':
      return 'Saved transfer inputs could not be verified. Enter them again in the form above.';
    case 'scope':
      return 'This group is not consistently linked to this case. Review its case linkage before loading saved inputs.';
    default:
      return 'Saved transfer inputs are unavailable. Review this group and enter pickup and bag totals in the form above.';
  }
}

export function airportTransferProfileScope(
  world: World,
  incident: RecoveryIncident,
  groupIds: string[],
) {
  return JSON.stringify([
    world.version,
    incident.id,
    [...incident.group_ids].sort(),
    airportTransferScope(world, groupIds),
  ]);
}

export function verifyAirportTransferProfiles(value: AirportTransferProfiles) {
  if (
    !value ||
    !Number.isSafeInteger(value.world_version) ||
    value.world_version < 0 ||
    !Array.isArray(value.profiles) ||
    value.profiles.length > 500 ||
    new Set(value.profiles.map((profile) => profile?.group_id)).size !== value.profiles.length
  )
    throw new Error('Saved transfer inputs are incomplete or inconsistent.');
  for (const profile of value.profiles) {
    if (!profile || typeof profile.group_id !== 'string' || !profile.group_id.trim())
      throw new Error('Saved transfer inputs do not identify their group.');
    if (profile.configured === false && typeof profile.reason === 'string') continue;
    if (profile.configured !== true)
      throw new Error('Saved transfer configuration state is unavailable.');
    const input = profile.inputs;
    const pickup = sourceInstant(input?.pickup_at);
    if (
      !input ||
      input.group_id !== profile.group_id ||
      input.destination_airport !== 'CRA' ||
      pickup === null ||
      pickup % 60_000 !== 0 ||
      !Number.isInteger(input.suitcases) ||
      input.suitcases < 0 ||
      input.suitcases > 7 ||
      !Number.isInteger(input.handbags) ||
      input.handbags < 0 ||
      input.handbags > 7 ||
      typeof profile.source_job_id !== 'string' ||
      !profile.source_job_id.trim() ||
      sourceInstant(profile.saved_at) === null
    )
      throw new Error('Saved transfer inputs are incomplete or inconsistent.');
  }
  return value;
}

export function loadSavedAirportTransferInput({
  profile,
  responseVersion,
  reviewedScope,
  world,
  incident,
  groupIds,
  drafts,
}: {
  profile: AirportTransferProfile;
  responseVersion: number;
  reviewedScope: string;
  world: World;
  incident: RecoveryIncident;
  groupIds: string[];
  drafts: AirportTransferDraft[];
}): AirportTransferDraft[] {
  if (
    responseVersion !== world.version ||
    reviewedScope !== airportTransferProfileScope(world, incident, groupIds)
  )
    throw new Error('The operation or selection changed. Review saved inputs again.');
  verifyAirportTransferProfiles({ world_version: responseVersion, profiles: [profile] });
  if (!profile.configured) throw new Error(airportTransferProfileReason(profile.reason));
  const group = world.groups.find((item) => item.id === profile.group_id);
  if (
    !groupIds.includes(profile.group_id) ||
    !incident.group_ids.includes(profile.group_id) ||
    group?.incident_id !== incident.id
  )
    throw new Error('Saved inputs can only fill an already-selected group in this case.');
  const issue = airportTransferGroupIssue(world, profile.group_id);
  if (issue) throw new Error(issue);
  const input = profile.inputs;
  const draft: AirportTransferDraft = {
    group_id: profile.group_id,
    pickup_at: new Date(input.pickup_at).toISOString().slice(0, 16),
    suitcases: String(input.suitcases),
    handbags: String(input.handbags),
    scope: airportTransferScope(world, groupIds),
  };
  if (drafts.some((item) => item.group_id === profile.group_id))
    return drafts.map((item) => (item.group_id === profile.group_id ? draft : item));
  if (drafts.length >= 12) throw new Error('Choose at most 12 airport-transfer groups.');
  return [...drafts, draft];
}

/** Read-only responses cannot populate a later case, selection, or world version. */
export class AirportTransferProfileReadGate {
  private sequence = 0;
  begin() {
    return ++this.sequence;
  }
  invalidate() {
    this.sequence++;
  }
  accepts(token: number, startedScope: string, currentScope: string) {
    return token === this.sequence && startedScope === currentScope;
  }
}
