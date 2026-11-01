import type {
  AfterFlightTransferProfile,
  AfterFlightTransferProfiles,
  RecoveryIncident,
  ResourceKind,
  World,
} from './types';
import {
  afterFlightTransferGroupIssue,
  type AfterFlightTransferDraft,
} from './afterFlightTransfer';
import { airportTransferScope } from './airportTransferRecovery';
import { sourceInstant } from './sourceInstant';

export function afterFlightProfileReason(reason: string): string {
  switch (reason) {
    case 'missing':
      return 'No saved flight + transfer setup for this group. Enter a pickup window, processing allowance and bag totals above.';
    case 'stale':
      return 'Saved flight + transfer inputs belong to a different party, route or case. Enter fresh inputs for this group.';
    case 'invalid':
      return 'Saved flight + transfer inputs could not be verified. Enter them again above.';
    case 'scope':
      return 'This group is not consistently linked to this case. Review its case linkage before loading saved inputs.';
    default:
      return 'Saved flight + transfer inputs are unavailable. Review this group and enter its window, processing allowance and bags above.';
  }
}

export function afterFlightProfileScope(
  world: World,
  incident: RecoveryIncident,
  groupIds: string[],
  modes: ResourceKind[],
  incompatibleSetup: boolean,
) {
  return JSON.stringify([
    world.version,
    incident.id,
    [...incident.group_ids].sort(),
    airportTransferScope(world, groupIds),
    [...modes].sort(),
    incompatibleSetup,
  ]);
}

export function verifyAfterFlightProfiles(value: AfterFlightTransferProfiles) {
  if (
    !value ||
    !Number.isSafeInteger(value.world_version) ||
    value.world_version < 0 ||
    !Array.isArray(value.profiles) ||
    value.profiles.length > 500 ||
    new Set(value.profiles.map((profile) => profile?.group_id)).size !== value.profiles.length
  )
    throw new Error('Saved flight + transfer inputs are incomplete or inconsistent.');
  const integer = (value: unknown, min: number, max: number) =>
    typeof value === 'number' && Number.isInteger(value) && value >= min && value <= max;
  for (const profile of value.profiles) {
    if (!profile || typeof profile.group_id !== 'string' || !profile.group_id.trim())
      throw new Error('Saved flight + transfer inputs do not identify their group.');
    if (profile.configured === false && typeof profile.reason === 'string') continue;
    if (profile.configured !== true)
      throw new Error('Saved flight + transfer configuration state is unavailable.');
    const input = profile.inputs,
      start = sourceInstant(input?.pickup_window_start),
      end = sourceInstant(input?.pickup_window_end);
    if (
      !input ||
      input.group_id !== profile.group_id ||
      input.via_airport !== 'OTP' ||
      input.destination_airport !== 'CRA' ||
      start === null ||
      end === null ||
      start % 60_000 !== 0 ||
      end % 60_000 !== 0 ||
      end < start ||
      !integer(input.airport_processing_minutes, 1, 720) ||
      !integer(input.suitcases, 0, 7) ||
      !integer(input.handbags, 0, 7) ||
      ![1, 2].includes(input.max_inbound_options) ||
      typeof profile.source_job_id !== 'string' ||
      !profile.source_job_id.trim() ||
      sourceInstant(profile.saved_at) === null
    )
      throw new Error('Saved flight + transfer inputs are incomplete or inconsistent.');
  }
  return value;
}

export interface AfterFlightProfileLoadScope {
  world: World;
  incident: RecoveryIncident;
  groupIds: string[];
  modes: ResourceKind[];
  incompatibleSetup: boolean;
  draft: AfterFlightTransferDraft | null;
}
export function afterFlightProfileLoadIssue({
  world,
  incident,
  groupIds,
  modes,
  incompatibleSetup,
  draft,
}: AfterFlightProfileLoadScope): string | null {
  if (incompatibleSetup || modes.includes('hotel'))
    return 'Remove hotel and initial airport-transfer setup explicitly before loading this separate workflow.';
  if (!modes.includes('flight') || !modes.includes('bus'))
    return 'Select Flights and Ground transport before loading flight + transfer inputs.';
  const issue = afterFlightTransferGroupIssue(world, groupIds);
  if (issue) return issue;
  const id = groupIds[0],
    group = world.groups.find((item) => item.id === id);
  if (!incident.group_ids.includes(id) || group?.incident_id !== incident.id)
    return 'Saved inputs can only fill an already-selected group in this case.';
  if (draft && (draft.group_id !== id || draft.scope !== airportTransferScope(world, groupIds)))
    return 'Remove or reset the existing after-flight setup before loading saved inputs for this selection.';
  return null;
}

export function savedAfterFlightDraft(
  profile: Extract<AfterFlightTransferProfile, { configured: true }>,
  world: World,
  groupIds: string[],
): AfterFlightTransferDraft {
  const input = profile.inputs;
  return {
    group_id: input.group_id,
    scope: airportTransferScope(world, groupIds),
    // The UTC form changes only representation, never the saved instants or dates.
    pickup_window_start: new Date(input.pickup_window_start).toISOString().slice(0, 16),
    pickup_window_end: new Date(input.pickup_window_end).toISOString().slice(0, 16),
    airport_processing_minutes: String(input.airport_processing_minutes),
    suitcases: String(input.suitcases),
    handbags: String(input.handbags),
    max_inbound_options: String(input.max_inbound_options) as '1' | '2',
  };
}

export function loadSavedAfterFlightInput(
  args: AfterFlightProfileLoadScope & {
    profile: AfterFlightTransferProfile;
    responseVersion: number;
    reviewedScope: string;
  },
): AfterFlightTransferDraft {
  const {
    profile,
    responseVersion,
    reviewedScope,
    world,
    incident,
    groupIds,
    modes,
    incompatibleSetup,
  } = args;
  if (
    responseVersion !== world.version ||
    reviewedScope !== afterFlightProfileScope(world, incident, groupIds, modes, incompatibleSetup)
  )
    throw new Error(
      'The operation, selection or services changed. Review saved flight + transfer inputs again.',
    );
  verifyAfterFlightProfiles({ world_version: responseVersion, profiles: [profile] });
  if (!profile.configured) throw new Error(afterFlightProfileReason(profile.reason));
  const issue = afterFlightProfileLoadIssue(args);
  if (issue) throw new Error(issue);
  if (profile.group_id !== groupIds[0])
    throw new Error('Saved inputs can only fill the already-selected group in this case.');
  return savedAfterFlightDraft(profile, world, groupIds);
}

/** Explicit reads may not populate another case, selection or world after a later render. */
export class AfterFlightProfileReadGate {
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
