import type { FlightLookup, FlightSelection, PassengerGroup, RecoveryIncident } from './types';

export const normalizeFlightReference = (value: string) =>
  value.trim().toUpperCase().replace(/[\s-]/g, '');
export function validateFlightQuery(
  flightNumber: string,
  date: string,
  todayUTC = new Date().toISOString().slice(0, 10),
): string | null {
  const normalized = normalizeFlightReference(flightNumber);
  if (
    flightNumber.trim().length > 16 ||
    !/[A-Z]/.test(normalized) ||
    !/^[A-Z0-9]{2,3}\d{1,4}[A-Z]?$/.test(normalized)
  )
    return 'Enter the flight number, including its airline prefix.';
  const parsed = new Date(`${date}T00:00:00Z`);
  if (
    !/^\d{4}-\d{2}-\d{2}$/.test(date) ||
    !Number.isFinite(parsed.getTime()) ||
    parsed.toISOString().slice(0, 10) !== date
  )
    return 'Choose a valid flight date in UTC.';
  if (date < '2022-06-01' || date > todayUTC)
    return 'Choose a UTC date from 1 June 2022 through today.';
  return null;
}
export function flightLookupCurrent(
  lookup: FlightLookup | null,
  now = Date.now(),
): lookup is FlightLookup {
  return Boolean(
    lookup?.state === 'ready' &&
    lookup.lookup_id &&
    lookup.expires_at &&
    Number.isFinite(Date.parse(lookup.expires_at)) &&
    Date.parse(lookup.expires_at) > now,
  );
}
export function selectedFlight(lookup: FlightLookup | null, selectionId: string, now = Date.now()) {
  return flightLookupCurrent(lookup, now)
    ? (lookup.matches.find((flight) => flight.selection_id === selectionId) ?? null)
    : null;
}
export function groupBindingIssue(
  group: PassengerGroup,
  flight: FlightSelection | null,
  requestedFlight: string,
  incidentId?: string,
): string | null {
  if (group.incident_id && group.incident_id !== incidentId)
    return 'Already linked to another case';
  if (group.status === 'confirmed')
    return 'Existing supplier bookings require review before linking';
  const references = [requestedFlight, flight?.flight_number, flight?.callsign, flight?.fr24_id]
    .filter((value): value is string => Boolean(value))
    .map(normalizeFlightReference);
  if (!references.includes(normalizeFlightReference(group.original_flight_id)))
    return 'Original flight reference does not match';
  return null;
}

export function savedCaseBindingIssue(
  group: PassengerGroup,
  incident: RecoveryIncident,
): string | null {
  return groupBindingIssue(
    group,
    incident.flight_context_state === 'expired' ? null : incident.flight,
    incident.requested_flight_number,
    incident.id,
  );
}
export function utcTimestamp(value: string | null | undefined) {
  if (!value || !Number.isFinite(Date.parse(value))) return 'Unknown';
  return (
    new Intl.DateTimeFormat('en-GB', {
      timeZone: 'UTC',
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    }).format(new Date(value)) + ' UTC'
  );
}
export const incidentKindLabel = (kind: RecoveryIncident['kind']) =>
  kind === 'destination_unavailable' ? 'Destination unavailable' : 'Flight disruption';
