import type { RecoveryIncident, RomanianHotelRecoveryProfile, World } from './types';
import {
  bucharestDate,
  romanianHotelGroupIssue,
  romanianHotelScope,
  type RomanianHotelRecoveryForm,
} from './romanianHotelRecovery';
import { sourceInstant } from './sourceInstant';

const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);
const identifier = (value: unknown): value is string =>
  typeof value === 'string' &&
  value.length > 0 &&
  value.length <= 256 &&
  value === value.trim() &&
  !/[\u0000-\u001f\u007f]/.test(value);
const integer = (value: unknown, min: number, max: number): value is number =>
  typeof value === 'number' && Number.isInteger(value) && value >= min && value <= max;

export function romanianHotelProfileReason(reason: string): string {
  switch (reason) {
    case 'missing':
      return 'No saved Romania hotel setup is available for this group. Inputs are saved only after a complete successful proposal; enter fresh values above.';
    case 'stale':
      return 'The saved Romania hotel setup belongs to a different party, route or case. Enter fresh values for this group.';
    case 'invalid':
      return 'The saved Romania hotel inputs could not be verified. Enter them again above.';
    case 'scope':
      return 'This group is not consistently linked to this case. Review the passenger and case linkage first.';
    default:
      return 'Saved Romania hotel inputs are unavailable. Review the current group and enter its stay, bags and margins above.';
  }
}

export function verifyRomanianHotelProfile(value: unknown): RomanianHotelRecoveryProfile {
  const invalid = () => new Error('Saved Romania hotel inputs are incomplete or inconsistent.');
  if (
    !object(value) ||
    !Number.isSafeInteger(value.world_version) ||
    (value.world_version as number) < 0 ||
    !identifier(value.incident_id) ||
    !identifier(value.group_id)
  )
    throw invalid();
  if (value.configured === false) {
    if (
      typeof value.reason !== 'string' ||
      !value.reason.trim() ||
      value.reason.length > 160 ||
      ['inputs', 'source_job_id', 'saved_at'].some((key) => Object.hasOwn(value, key))
    )
      throw invalid();
    return value as RomanianHotelRecoveryProfile;
  }
  if (
    value.configured !== true ||
    !identifier(value.source_job_id) ||
    sourceInstant(value.saved_at) === null ||
    Object.hasOwn(value, 'reason')
  )
    throw invalid();
  const input = value.inputs;
  if (!object(input)) throw invalid();
  const keys = [
    'source',
    'hotel_id',
    'checkin_at',
    'checkout_at',
    'rooms',
    'minimum_rest_minutes',
    'suitcases',
    'handbags',
    'standard_baggage_confirmed',
    'airport_processing_minutes',
    'hotel_connection_minutes',
  ];
  const start = sourceInstant(input.checkin_at),
    end = sourceInstant(input.checkout_at);
  if (
    Object.keys(input).length !== keys.length ||
    keys.some((key) => !Object.hasOwn(input, key)) ||
    input.source !== 'transfero_direct_aeroport' ||
    input.hotel_id !== 'lpce923' ||
    start === null ||
    end === null ||
    start % 60000 !== 0 ||
    end % 60000 !== 0 ||
    end <= start ||
    bucharestDate(input.checkout_at as string)! <= bucharestDate(input.checkin_at as string)! ||
    !integer(input.rooms, 1, 12) ||
    !integer(input.minimum_rest_minutes, 0, 10080) ||
    input.minimum_rest_minutes * 60000 > end - start ||
    !integer(input.suitcases, 0, 12) ||
    !integer(input.handbags, 0, 12) ||
    input.standard_baggage_confirmed !== true ||
    !integer(input.airport_processing_minutes, 180, 720) ||
    !integer(input.hotel_connection_minutes, 20, 720)
  )
    throw invalid();
  return value as RomanianHotelRecoveryProfile;
}

export interface RomanianHotelProfileLoadScope {
  world: World;
  incident: RecoveryIncident;
  groupIds: string[];
  packageSelected: boolean;
  incompatibleSetup: boolean;
  allowance: number;
  draft: RomanianHotelRecoveryForm;
}
export function romanianHotelProfileScope(args: RomanianHotelProfileLoadScope): string {
  return JSON.stringify([
    args.world.scenario_id,
    args.world.mode,
    args.world.epoch,
    args.world.version,
    args.incident.id,
    args.incident.group_ids,
    args.groupIds,
    args.groupIds.map((id) => args.world.groups.find((item) => item.id === id) ?? null),
    args.packageSelected,
    args.incompatibleSetup,
    args.allowance,
    args.draft,
  ]);
}
export function romanianHotelProfileLoadIssue(args: RomanianHotelProfileLoadScope): string | null {
  if (!args.packageSelected)
    return 'Choose the Romania hotel package explicitly before loading its saved setup.';
  if (args.incompatibleSetup)
    return 'Remove initial airport-transfer or after-flight setup explicitly before loading this separate hotel package.';
  if (args.groupIds.length !== 1)
    return 'Select exactly one group in this case before reviewing saved Romania inputs.';
  const group = args.world.groups.find((item) => item.id === args.groupIds[0]);
  if (
    !group ||
    group.incident_id !== args.incident.id ||
    !args.incident.group_ids.includes(group.id)
  )
    return 'Saved inputs can only fill the already-selected group in this case.';
  const issue = romanianHotelGroupIssue(group, args.world);
  if (issue) return issue;
  if (args.draft.scope && args.draft.scope !== romanianHotelScope(args.world, group))
    return 'Reset the earlier Romania draft explicitly before loading saved inputs for this group or readiness.';
  return null;
}
export function romanianHotelDraftHasValues(draft: RomanianHotelRecoveryForm): boolean {
  return Object.entries(draft).some(
    ([key, value]) => key !== 'scope' && (typeof value === 'boolean' ? value : value !== ''),
  );
}
export function savedRomanianHotelDraft(
  profile: Extract<RomanianHotelRecoveryProfile, { configured: true }>,
  args: RomanianHotelProfileLoadScope,
): RomanianHotelRecoveryForm {
  const input = profile.inputs,
    group = args.world.groups.find((item) => item.id === profile.group_id)!;
  return {
    scope: romanianHotelScope(args.world, group),
    checkin_at: new Date(input.checkin_at).toISOString().slice(0, 16),
    checkout_at: new Date(input.checkout_at).toISOString().slice(0, 16),
    rooms: String(input.rooms),
    minimum_rest_minutes: String(input.minimum_rest_minutes),
    suitcases: String(input.suitcases),
    handbags: String(input.handbags),
    standard_baggage_confirmed: input.standard_baggage_confirmed,
    airport_processing_minutes: String(input.airport_processing_minutes),
    hotel_connection_minutes: String(input.hotel_connection_minutes),
  };
}
export function loadSavedRomanianHotelInput(
  args: RomanianHotelProfileLoadScope & {
    profile: RomanianHotelRecoveryProfile;
    reviewedScope: string;
    replacementConfirmed: boolean;
    disabled: boolean;
  },
): RomanianHotelRecoveryForm {
  if (args.disabled)
    throw new Error('Wait for the pending operation to finish before changing recovery inputs.');
  if (args.reviewedScope !== romanianHotelProfileScope(args))
    throw new Error(
      'The operation, selection, allowance or draft changed. Review saved Romania inputs again.',
    );
  const profile = verifyRomanianHotelProfile(args.profile);
  if (
    profile.world_version !== args.world.version ||
    profile.incident_id !== args.incident.id ||
    profile.group_id !== args.groupIds[0]
  )
    throw new Error(
      'The saved response belongs to a different operation version, case or group. Review again.',
    );
  if (!profile.configured) throw new Error(romanianHotelProfileReason(profile.reason));
  const issue = romanianHotelProfileLoadIssue(args);
  if (issue) throw new Error(issue);
  const group = args.world.groups.find((item) => item.id === profile.group_id)!;
  if (
    [profile.inputs.rooms, profile.inputs.suitcases, profile.inputs.handbags].some(
      (count) => count > group.size,
    )
  )
    throw new Error(
      'The saved room or baggage counts do not fit the current party. Enter fresh inputs.',
    );
  if (romanianHotelDraftHasValues(args.draft) && !args.replacementConfirmed)
    throw new Error('Confirm replacing the current Romania inputs with the reviewed saved values.');
  return savedRomanianHotelDraft(profile, args);
}

export class RomanianHotelProfileReadGate {
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
