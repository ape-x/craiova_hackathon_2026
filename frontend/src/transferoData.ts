import type { Resource } from './types';
import { sourceInstant } from './sourceInstant';

function object(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}
const text = (value: unknown): value is string =>
  typeof value === 'string' &&
  value.length > 0 &&
  value.length <= 240 &&
  !/[\u0000-\u001f\u007f]/.test(value);
export function isTransfero(resource: Pick<Resource, 'kind' | 'source'>): boolean {
  return resource.kind === 'bus' && resource.source === 'Transfero';
}

/** Bounded source display evidence; server-side binding and eligibility remain authoritative. */
export function transferoEvidence(resource: Resource) {
  if (!isTransfero(resource)) return null;
  const quote = object(resource.provenance?.transfero_quote);
  if (
    !quote ||
    quote.provider !== 'Transfero' ||
    quote.source_url !== 'https://www.transfero.ro/cauta-bilete/' ||
    quote.booking_supported !== false ||
    !Array.isArray(quote.options) ||
    quote.options.length > 30
  )
    return null;
  const matches = quote.options
    .map(object)
    .filter((option) => option?.id === resource.provenance?.transfero_option_id);
  if (matches.length !== 1 || !matches[0]) return null;
  const option = matches[0],
    policy = object(option.policy);
  const departure = sourceInstant(option.departure_at),
    arrival = sourceInstant(option.arrival_at),
    received = sourceInstant(quote.received_at);
  const stop = (value: unknown) => {
    const item = object(value);
    return item &&
      text(item.name) &&
      text(item.city) &&
      item.source_url === 'https://direct-aeroport.ro/contact/'
      ? { name: item.name, city: item.city }
      : null;
  };
  const origin = stop(option.departure_stop),
    destination = stop(option.arrival_stop);
  const price = object(option.price),
    evidence = resource.price_evidence;
  if (
    departure === null ||
    arrival === null ||
    received === null ||
    arrival <= departure ||
    option.operator !== 'Direct Aeroport' ||
    !text(option.vehicle_type) ||
    option.timezone !== 'Europe/Bucharest' ||
    option.timezone_basis !== 'app_interpretation_of_source_local_clock' ||
    option.availability_status !== 'quoted_not_reserved' ||
    option.supplier_valid_until != null ||
    !Number.isInteger(option.available_seats) ||
    (option.available_seats as number) < 0 ||
    resource.capacity !== option.available_seats ||
    option.price_scope !== 'requested_party' ||
    evidence?.kind !== 'transfero_ecb_reference' ||
    price?.currency !== 'RON' ||
    price.amount !== evidence.original.amount ||
    !origin ||
    !destination ||
    policy?.required_advance_hours !== 12 ||
    policy.boarding_minutes !== 20 ||
    policy.airport_processing_minutes !== 180 ||
    policy.timing_basis !== 'app_conservative_maximum_of_operator_and_attached_terms' ||
    policy.schedule_basis !== 'source_estimated_local_timetable' ||
    policy.boarding_location_basis !==
      'operator_named_facility_exact_bay_confirmed_after_booking' ||
    policy.source_url !== 'https://direct-aeroport.ro/termeni-conditii-calatorie/' ||
    received !== sourceInstant(resource.quote_freshness?.received_at)
  )
    return null;
  return {
    departure,
    arrival,
    received,
    origin,
    destination,
    vehicleType: option.vehicle_type,
    reportedSeats: option.available_seats as number,
    advanceCutoff: departure - 12 * 3_600_000,
  };
}
