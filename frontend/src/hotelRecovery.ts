import type { HotelRecoveryInput, PassengerGroup, World } from './types';
import { utcInputToIso } from './liveData';
import { isAirportNode } from './locationNodes';

export interface DeclaredRecoveryPointForm {
  label: string;
  latitude: string;
  longitude: string;
  confirmed: boolean;
}
export interface HotelRecoveryForm {
  checkin_at: string;
  checkout_at: string;
  rooms: string;
  minimum_rest_minutes: string;
  initial_point: DeclaredRecoveryPointForm;
  pickup_at: string;
  suitcases: string;
  onward_airport_code: string;
  airport_dropoff: DeclaredRecoveryPointForm;
  airport_processing_minutes: string;
}

export function emptyHotelRecoveryForm(): HotelRecoveryForm {
  return {
    checkin_at: '',
    checkout_at: '',
    rooms: '',
    minimum_rest_minutes: '',
    initial_point: { label: '', latitude: '', longitude: '', confirmed: false },
    pickup_at: '',
    suitcases: '',
    onward_airport_code: '',
    airport_dropoff: { label: '', latitude: '', longitude: '', confirmed: false },
    airport_processing_minutes: '',
  };
}

/** Early input guidance only; server validates UK coverage, source policy and all connections. */
export function hotelRecoveryGroupIssue(group: PassengerGroup, world: World): string | null {
  if (group.status === 'confirmed') return 'Existing supplier booking; excluded.';
  if (group.adult_count !== group.size || group.size < 1)
    return 'Confirm an explicit all-adult party in passenger requirements first.';
  if (group.accessible) return 'This workflow cannot verify the requested accessible transfers.';
  const origin = world.airports.find((node) => node.id === group.origin);
  if (!origin || !isAirportNode(origin)) return 'The group must start at an airport reference.';
  return null;
}

function integer(value: string, min: number, max: number, label: string): number {
  if (!/^\d+$/.test(value.trim())) throw new Error(`Enter ${label} as a whole number.`);
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number < min || number > max)
    throw new Error(`${label} must be between ${min} and ${max}.`);
  return number;
}

function point(
  value: DeclaredRecoveryPointForm,
  name: string,
): HotelRecoveryInput['initial_point'] {
  const label = value.label.trim();
  if (label.length < 2 || label.length > 160 || /[\u0000-\u001f\u007f]/.test(label))
    throw new Error(`Enter a ${name} label of 2–160 characters without control characters.`);
  const latitude = Number(value.latitude);
  const longitude = Number(value.longitude);
  if (
    !value.latitude.trim() ||
    !value.longitude.trim() ||
    !Number.isFinite(latitude) ||
    !Number.isFinite(longitude) ||
    Math.abs(latitude) > 90 ||
    Math.abs(longitude) > 180
  )
    throw new Error(`Enter valid latitude and longitude for the ${name}.`);
  if (!value.confirmed) throw new Error(`Confirm the ${name} as an operator-declared point.`);
  return { label, latitude, longitude, confirmed: true };
}

function minute(value: string, label: string): string {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value))
    throw new Error(`Enter ${label} as a complete UTC date and time, to the minute.`);
  return utcInputToIso(value);
}

export function hotelRecoveryTimeline(checkoutInput: string): {
  returnPickup: string;
  flightQueryDate: string;
} | null {
  try {
    const checkout = new Date(minute(checkoutInput, 'checkout'));
    const parts = new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Europe/London',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).formatToParts(checkout);
    const part = (kind: string) => parts.find((item) => item.type === kind)?.value;
    return {
      returnPickup: new Date(checkout.getTime() + 10 * 60_000).toISOString(),
      flightQueryDate: `${part('year')}-${part('month')}-${part('day')}`,
    };
  } catch {
    return null;
  }
}

export function hotelRecoveryInput(
  form: HotelRecoveryForm,
  groupIds: string[],
  world: World,
  maxCalls: number,
  now = Date.now(),
): HotelRecoveryInput {
  if (world.mode !== 'live')
    throw new Error('This sourced hotel workflow requires live operations.');
  if (groupIds.length !== 1)
    throw new Error('Select exactly one linked passenger group for hotel recovery.');
  const group = world.groups.find((item) => item.id === groupIds[0]);
  if (!group) throw new Error('The selected passenger group no longer exists.');
  const issue = hotelRecoveryGroupIssue(group, world);
  if (issue) throw new Error(issue);
  if (!Number.isInteger(maxCalls) || maxCalls < 10 || maxCalls > 26)
    throw new Error(
      'Hotel recovery needs an explicit provider-call limit of 10–26. Your limit has not been increased.',
    );
  const pickup_at = minute(form.pickup_at, 'initial pickup');
  const checkin_at = minute(form.checkin_at, 'check-in');
  const checkout_at = minute(form.checkout_at, 'checkout');
  const pickup = Date.parse(pickup_at),
    checkin = Date.parse(checkin_at),
    checkout = Date.parse(checkout_at);
  if (pickup <= now) throw new Error('Initial pickup must be in the future.');
  if (pickup >= checkin || checkout <= checkin)
    throw new Error(
      'Pickup must precede check-in, and checkout must follow check-in. Actual driving time is checked against sourced routes.',
    );
  const checkinDate = hotelRecoveryTimeline(form.checkin_at)?.flightQueryDate;
  const checkoutDate = hotelRecoveryTimeline(form.checkout_at)?.flightQueryDate;
  if (!checkinDate || !checkoutDate || checkoutDate <= checkinDate)
    throw new Error('This hotel search requires checkout on a later Europe/London calendar date.');
  const airport_processing_minutes = integer(
    form.airport_processing_minutes,
    1,
    720,
    'Airport processing minutes',
  );
  const epoch = Date.parse(world.epoch);
  const ready = epoch + (group.ready_at ?? 0) * 60_000;
  const deadline = epoch + group.deadline * 60_000;
  if (
    !Number.isFinite(epoch) ||
    !Number.isFinite(deadline) ||
    pickup < ready ||
    checkout + (10 + airport_processing_minutes) * 60_000 >= deadline
  )
    throw new Error(
      'The stay, return pickup and airport buffer must fit after the group’s readiness and before its arrival deadline.',
    );
  const rooms = integer(form.rooms, 1, Math.min(group.size, 30), 'Rooms');
  const minimum_rest_minutes = integer(
    form.minimum_rest_minutes,
    0,
    10080,
    'Minimum rest in minutes',
  );
  if (minimum_rest_minutes * 60_000 > checkout - checkin)
    throw new Error('The minimum rest cannot exceed the requested hotel interval.');
  const onward_airport_code = form.onward_airport_code.trim().toUpperCase();
  if (!/^[A-Z]{3}$/.test(onward_airport_code))
    throw new Error('Enter a three-letter UK onward-airport IATA code.');
  return {
    checkin_at,
    checkout_at,
    rooms,
    minimum_rest_minutes,
    initial_point: point(form.initial_point, 'initial meeting point'),
    pickup_at,
    suitcases: integer(form.suitcases, 0, 200, 'Suitcases'),
    onward_airport_code,
    airport_dropoff: point(form.airport_dropoff, 'airport drop-off'),
    airport_processing_minutes,
  };
}
