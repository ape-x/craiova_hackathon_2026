import type { AfterFlightTransferInput, ResourceKind, World } from './types';
import { airportTransferScope } from './airportTransferRecovery';
import { utcInputToIso } from './liveData';
import { LIVE_PASSENGER_HORIZON_MINUTES } from './passengerDeadline';
import { passengerArrivalPending } from './passengerArrival';

export interface AfterFlightTransferDraft {
  group_id: string;
  scope: string;
  pickup_window_start: string;
  pickup_window_end: string;
  airport_processing_minutes: string;
  suitcases: string;
  handbags: string;
  max_inbound_options: '1' | '2';
}
export const emptyAfterFlightTransfer = (
  group_id: string,
  scope: string,
): AfterFlightTransferDraft => ({
  group_id,
  scope,
  pickup_window_start: '',
  pickup_window_end: '',
  airport_processing_minutes: '',
  suitcases: '',
  handbags: '',
  max_inbound_options: '2',
});
export function afterFlightTransferGroupIssue(
  world: World,
  groupIds: string[],
  reportedArrivalGroupIds: string[] = [],
): string | null {
  if (world.mode !== 'live' || groupIds.length !== 1)
    return 'Select exactly one group for flight to OTP + transfer to CRA.';
  const group = world.groups.find((item) => item.id === groupIds[0]);
  const origin = world.airports.find((item) => item.id === group?.origin);
  // An explicit arrival-report preview may name an airport not yet materialized.
  // The backend resolves its catalog identity before recording or sourcing.
  if (
    !group ||
    !/^[A-Z]{3}$/.test(group.origin) ||
    (origin
      ? origin.kind !== 'airport' || origin.code !== group.origin
      : !reportedArrivalGroupIds.includes(group.id))
  )
    return 'The group needs a recorded origin airport.';
  if (group.origin === 'OTP')
    return 'This group is already reported at OTP. Use the initial OTP → CRA transfer setup.';
  if (group.destination !== 'CRA')
    return 'This workflow supports groups whose final destination is CRA.';
  if (
    !Number.isInteger(group.size) ||
    group.size < 1 ||
    group.size > 7 ||
    group.adult_count !== group.size
  )
    return 'This source supports 1–7 explicitly recorded adults.';
  if (group.accessible) return 'Accessible vehicle suitability is not confirmed by this source.';
  if (
    group.status === 'confirmed' ||
    (world.offers ?? []).some(
      (offer) =>
        offer.group_id === group.id &&
        (offer.status === 'confirmed' || Boolean(offer.external_bookings?.length)),
    ) ||
    (world.reservations ?? []).some(
      (reservation) => reservation.group_id === group.id && reservation.status === 'confirmed',
    )
  )
    return 'This group has recorded bookings; review them separately.';
  if (passengerArrivalPending(world, group) && !reportedArrivalGroupIds.includes(group.id))
    return 'Passenger arrival is unresolved. Record their actual airport and readiness before searching.';
  return null;
}
export function afterFlightTransferInput(
  draft: AfterFlightTransferDraft | null | undefined,
  world: World,
  groupIds: string[],
  modes: ResourceKind[],
  allowance: number,
  incompatibleSetup = false,
  extraScope = '',
  now = Date.now(),
  reportedArrivalGroupIds: string[] = [],
): AfterFlightTransferInput | undefined {
  if (!draft) return undefined;
  if (incompatibleSetup || modes.includes('hotel'))
    throw new Error(
      'Flight + airport transfer cannot be combined with hotel or initial airport-transfer setup. Remove that setup explicitly.',
    );
  if (!modes.includes('flight') || !modes.includes('bus'))
    throw new Error('Select Flights and Ground transport for flight to OTP + transfer to CRA.');
  const issue = afterFlightTransferGroupIssue(world, groupIds, reportedArrivalGroupIds);
  if (issue) throw new Error(issue);
  if (
    draft.group_id !== groupIds[0] ||
    draft.scope !== airportTransferScope(world, groupIds, extraScope)
  )
    throw new Error(
      'The selected group, location or readiness changed. Reset the after-flight inputs before searching.',
    );
  if (!Number.isInteger(allowance) || allowance < 6 || allowance > 26)
    throw new Error(
      'This workflow requires at least 6 calls within your selected limit. The limit will not be increased.',
    );
  const integer = (value: string, label: string, min: number, max: number) => {
    if (!/^\d+$/.test(value) || Number(value) < min || Number(value) > max)
      throw new Error(
        `Enter ${label}: a whole number from ${min} to ${max}. Unknown values are not zero.`,
      );
    return Number(value);
  };
  const airport_processing_minutes = integer(
      draft.airport_processing_minutes,
      'arrival processing minutes',
      1,
      720,
    ),
    suitcases = integer(draft.suitcases, 'total suitcases', 0, 7),
    handbags = integer(draft.handbags, 'total handbags', 0, 7);
  if (!['1', '2'].includes(draft.max_inbound_options))
    throw new Error('Choose at most one or two inbound flight options.');
  const pickup_window_start = utcInputToIso(draft.pickup_window_start),
    pickup_window_end = utcInputToIso(draft.pickup_window_end),
    start = Date.parse(pickup_window_start),
    end = Date.parse(pickup_window_end),
    epoch = Date.parse(world.epoch),
    group = world.groups.find((item) => item.id === draft.group_id)!;
  if (start % 60_000 !== 0 || end % 60_000 !== 0 || start <= now || end < start)
    throw new Error('Enter an ordered future pickup window with whole-minute times.');
  if (
    !Number.isFinite(world.sim_time) ||
    end > epoch + (world.sim_time + LIVE_PASSENGER_HORIZON_MINUTES) * 60_000
  )
    throw new Error('The pickup window must be within seven days.');
  if (
    !Number.isFinite(epoch) ||
    !Number.isFinite(group.deadline) ||
    start < epoch + Math.max(world.sim_time, group.ready_at ?? 0) * 60_000 ||
    end > epoch + group.deadline * 60_000
  )
    throw new Error(
      'The pickup window must follow recorded readiness and end no later than the group deadline; source driving time is checked after quoting.',
    );
  if (
    end - airport_processing_minutes * 60_000 <=
    Math.max(now, epoch + Math.max(world.sim_time, group.ready_at ?? 0) * 60_000) + 30 * 60_000
  )
    throw new Error(
      'The window must leave time for an incoming flight after the 30-minute departure planning allowance and your arrival processing.',
    );
  return {
    group_id: draft.group_id,
    via_airport: 'OTP',
    destination_airport: 'CRA',
    pickup_window_start,
    pickup_window_end,
    airport_processing_minutes,
    suitcases,
    handbags,
    max_inbound_options: Number(draft.max_inbound_options) as 1 | 2,
  };
}
