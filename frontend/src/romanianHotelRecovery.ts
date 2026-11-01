import type { PassengerGroup, RomanianHotelRecoveryInput, World } from './types';
import { utcInputToIso } from './liveData';
import { passengerArrivalPending } from './passengerArrival';
import { groupRecoveryEntry } from './groupRecoveryEntry';
import { sourceInstant } from './resourceFreshness';

export interface RomanianHotelRecoveryForm {
  scope: string;
  checkin_at: string;
  checkout_at: string;
  rooms: string;
  minimum_rest_minutes: string;
  suitcases: string;
  handbags: string;
  standard_baggage_confirmed: boolean;
  airport_processing_minutes: string;
  hotel_connection_minutes: string;
}
export const emptyRomanianHotelRecoveryForm = (): RomanianHotelRecoveryForm => ({
  scope: '',
  checkin_at: '',
  checkout_at: '',
  rooms: '',
  minimum_rest_minutes: '',
  suitcases: '',
  handbags: '',
  standard_baggage_confirmed: false,
  airport_processing_minutes: '',
  hotel_connection_minutes: '',
});

export function romanianHotelScope(world: World, group: PassengerGroup): string {
  return JSON.stringify([
    world.scenario_id,
    world.epoch,
    group.id,
    group.incident_id,
    group.original_flight_id,
    group.origin,
    group.destination,
    group.ready_at,
    group.deadline,
    group.size,
    group.adult_count,
    group.accessible,
  ]);
}

export function romanianHotelGroupIssue(group: PassengerGroup, world: World): string | null {
  const entry = groupRecoveryEntry(world, group.id);
  if (!entry || entry.intent.kind !== 'recover')
    return 'Select an unconfirmed group uniquely linked to this case.';
  if (
    !Number.isInteger(group.size) ||
    group.size < 1 ||
    group.size > 12 ||
    group.adult_count !== group.size
  )
    return 'This package requires 1–12 explicitly declared adult passengers.';
  if (group.accessible) return 'Accessible coach travel is not verified by this source.';
  if (
    group.origin !== 'OTP' ||
    !world.airports.some(
      (node) => node.id === 'OTP' && node.code === 'OTP' && node.kind === 'airport',
    )
  )
    return 'This package starts from the group’s reported OTP airport location.';
  if (passengerArrivalPending(world, group))
    return 'Record the passengers’ actual airport location and readiness before hotel recovery.';
  if (
    group.destination === 'OTP' ||
    !world.airports.some(
      (node) =>
        node.id === group.destination && node.code === group.destination && node.kind === 'airport',
    )
  )
    return 'The group needs an onward flight from OTP to another airport.';
  return null;
}

function integer(value: string, min: number, max: number, label: string) {
  if (!/^\d+$/.test(value.trim())) throw new Error(`Enter ${label} as an explicit whole number.`);
  const count = Number(value);
  if (!Number.isSafeInteger(count) || count < min || count > max)
    throw new Error(`${label} must be between ${min} and ${max}.`);
  return count;
}
function minute(value: string, label: string) {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value))
    throw new Error(`Enter ${label} as a complete UTC date and time, to the minute.`);
  return utcInputToIso(value);
}
export function bucharestDate(value: string): string | null {
  const timestamp = sourceInstant(value);
  if (timestamp === null) return null;
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Europe/Bucharest',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(timestamp);
  const part = (name: string) => parts.find((item) => item.type === name)?.value;
  return `${part('year')}-${part('month')}-${part('day')}`;
}

/** Input checks do not establish coach inventory, hotel availability or a flight connection. */
export function romanianHotelRecoveryInput(
  form: RomanianHotelRecoveryForm,
  groupIds: string[],
  world: World,
  maxCalls: number,
  now = Date.now(),
): RomanianHotelRecoveryInput {
  if (world.mode !== 'live' || groupIds.length !== 1)
    throw new Error('Select exactly one linked live passenger group for this package.');
  const group = world.groups.find((item) => item.id === groupIds[0]);
  if (!group) throw new Error('The selected group is no longer available.');
  const issue = romanianHotelGroupIssue(group, world);
  if (issue) throw new Error(issue);
  if (form.scope !== romanianHotelScope(world, group))
    throw new Error(
      'The group, airport or readiness changed. Reset and review the Romania package inputs.',
    );
  if (!Number.isInteger(maxCalls) || maxCalls < 8 || maxCalls > 26)
    throw new Error(
      'This package needs an explicit limit of 8–26 provider calls. Your limit was not increased.',
    );
  const checkin_at = minute(form.checkin_at, 'check-in');
  const checkout_at = minute(form.checkout_at, 'checkout');
  const checkin = Date.parse(checkin_at),
    checkout = Date.parse(checkout_at);
  const checkinDate = bucharestDate(checkin_at),
    checkoutDate = bucharestDate(checkout_at);
  if (
    !Number.isFinite(now) ||
    checkin <= now ||
    checkout <= checkin ||
    !checkinDate ||
    !checkoutDate ||
    checkoutDate <= checkinDate
  )
    throw new Error(
      'Check-in must be future and checkout must be on a later Europe/Bucharest date.',
    );
  const airport_processing_minutes = integer(
    form.airport_processing_minutes,
    180,
    720,
    'Airport processing minutes',
  );
  const hotel_connection_minutes = integer(
    form.hotel_connection_minutes,
    20,
    720,
    'Hotel connection minutes',
  );
  const epoch = sourceInstant(world.epoch);
  if (
    epoch === null ||
    !Number.isFinite(group.ready_at) ||
    !Number.isFinite(group.deadline) ||
    checkin < epoch + group.ready_at! * 60_000 ||
    checkout > now + 10080 * 60_000 ||
    checkout + (hotel_connection_minutes + airport_processing_minutes) * 60_000 >=
      epoch + group.deadline * 60_000
  )
    throw new Error(
      'The stay and connection margins must fit after reported readiness and before the group deadline within the seven-day planning horizon.',
    );
  const minimum_rest_minutes = integer(form.minimum_rest_minutes, 0, 10080, 'Minimum rest minutes');
  if (minimum_rest_minutes * 60_000 > checkout - checkin)
    throw new Error('The minimum rest cannot exceed the requested hotel interval.');
  if (!form.standard_baggage_confirmed)
    throw new Error(
      'Confirm standard baggage for each adult; extra or overweight baggage is unsupported.',
    );
  return {
    source: 'transfero_direct_aeroport',
    hotel_id: 'lpce923',
    checkin_at,
    checkout_at,
    rooms: integer(form.rooms, 1, group.size, 'Rooms'),
    minimum_rest_minutes,
    suitcases: integer(form.suitcases, 0, group.size, 'Hold bags'),
    handbags: integer(form.handbags, 0, group.size, 'Hand bags'),
    standard_baggage_confirmed: true,
    airport_processing_minutes,
    hotel_connection_minutes,
  };
}
