import type { AirportTransferRecoveryInput, ResourceKind, World } from './types';
import { airportCabGroupIssue } from './airportCabData';
import { utcInputToIso } from './liveData';
import { addCalendarDays, bucharestDate } from './realData';

export interface AirportTransferDraft {
  group_id: string;
  pickup_at: string;
  suitcases: string;
  handbags: string;
  scope: string;
}
export function airportTransferGroupIssue(world: World, groupId: string) {
  const issue = airportCabGroupIssue(
    world,
    world.groups.find((group) => group.id === groupId),
  );
  if (issue) return issue;
  if (
    (world.offers ?? []).some(
      (offer) => offer.group_id === groupId && Boolean(offer.external_bookings?.length),
    ) ||
    (world.reservations ?? []).some(
      (reservation) => reservation.group_id === groupId && reservation.status === 'confirmed',
    )
  )
    return 'This group has recorded bookings; review them separately.';
  return null;
}
export function airportTransferScope(world: World, groupIds: string[], extraScope = '') {
  return JSON.stringify([
    world.scenario_id,
    world.mode,
    world.epoch,
    [...groupIds]
      .sort()
      .map((id) => world.groups.find((group) => group.id === id) ?? { id, missing: true }),
    extraScope,
  ]);
}
export function emptyAirportTransferDraft(group_id: string, scope: string): AirportTransferDraft {
  return { group_id, scope, pickup_at: '', suitcases: '', handbags: '' };
}
export function airportTransferInputs(
  drafts: AirportTransferDraft[],
  world: World,
  groupIds: string[],
  modes: ResourceKind[],
  allowance: number,
  hotelRecovery = false,
  extraScope = '',
  now = Date.now(),
): AirportTransferRecoveryInput[] {
  if (!drafts.length) return [];
  if (hotelRecovery)
    throw new Error(
      'Airport-to-airport sourcing cannot be combined with the hotel recovery workflow. Remove that transfer setup or choose Source alternatives.',
    );
  if (!modes.includes('bus'))
    throw new Error(
      'Select Ground transport to authorize these airport-transfer searches, or remove their setup.',
    );
  if (drafts.length > 12 || new Set(drafts.map((item) => item.group_id)).size !== drafts.length)
    throw new Error('Choose at most 12 different groups for airport-transfer searches.');
  if (!Number.isInteger(allowance) || allowance < 2 * drafts.length + 1 || allowance > 26)
    throw new Error(
      `These transfer searches require at least ${2 * drafts.length + 1} calls within your chosen total limit (two per group plus a decision). The limit will not be raised automatically.`,
    );
  const scope = airportTransferScope(world, groupIds, extraScope);
  return drafts.map((draft) => {
    if (!groupIds.includes(draft.group_id))
      throw new Error(
        'An airport-transfer group is no longer selected for this recovery. Remove its setup or review the selection.',
      );
    if (draft.scope !== scope)
      throw new Error(
        'The selected groups, location or readiness changed. Reset the airport-transfer inputs before searching.',
      );
    const group = world.groups.find((item) => item.id === draft.group_id);
    const issue = airportTransferGroupIssue(world, draft.group_id);
    if (issue) throw new Error(issue);
    for (const name of ['suitcases', 'handbags'] as const) {
      if (!/^\d+$/.test(draft[name]) || Number(draft[name]) > 7)
        throw new Error(
          `Enter the total ${name} for ${group!.name}: a whole number from 0 to 7. Unknown baggage is not zero.`,
        );
    }
    const pickup_at = utcInputToIso(draft.pickup_at),
      pickup = Date.parse(pickup_at),
      epoch = Date.parse(world.epoch);
    if (pickup % 60_000 !== 0 || pickup < now + 9 * 3_600_000)
      throw new Error(
        'The current conservative rule requires a whole-minute pickup at least 9 hours ahead. Source policy is checked again when dispatched.',
      );
    if (bucharestDate(new Date(pickup)) > addCalendarDays(bucharestDate(new Date(now)), 31))
      throw new Error('Airport-transfer pickup must be within 31 days.');
    if (
      !Number.isFinite(epoch) ||
      !Number.isFinite(group!.deadline) ||
      pickup < epoch + (group!.ready_at ?? 0) * 60_000 ||
      pickup >= epoch + group!.deadline * 60_000
    )
      throw new Error(
        'Pickup must follow recorded readiness and leave driving time before the group deadline.',
      );
    return {
      group_id: draft.group_id,
      destination_airport: 'CRA',
      pickup_at,
      suitcases: Number(draft.suitcases),
      handbags: Number(draft.handbags),
    };
  });
}
