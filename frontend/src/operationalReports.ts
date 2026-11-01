import type { PassengerGroup, RecoveryIncident, Resource, World } from './types';
import { utcInputToIso } from './liveData';
import { sourceInstant } from './resourceFreshness';
import type { ArrivalRecoveryInput } from './arrivalRecoveryData';

export interface ArrivalReportInput {
  group_ids: string[];
  airport_code: string;
  ready_at: string;
  description: string;
  recovery?: ArrivalRecoveryInput;
}
export interface ArrivalReportForm {
  groupIds: string[];
  airportCode: string;
  readyAt: string;
  description: string;
}

export function arrivalReportIssue(
  world: World,
  incident: RecoveryIncident,
  group: PassengerGroup,
) {
  if (world.mode !== 'live') return 'Operator reports are available in live operations.';
  if (group.incident_id !== incident.id || !incident.group_ids.includes(group.id))
    return 'This group is not linked to this case.';
  if (
    group.status === 'confirmed' ||
    (world.offers ?? []).some(
      (offer) =>
        offer.group_id === group.id &&
        (offer.status === 'confirmed' || Boolean(offer.external_bookings?.length)),
    ) ||
    (world.reservations ?? []).some(
      (item) => item.group_id === group.id && item.status === 'confirmed',
    )
  )
    return 'A booking is recorded. Review it separately before changing this group’s position.';
  return null;
}

export function unavailabilityIssue(world: World, resource: Resource) {
  if (world.mode !== 'live') return 'Operator reports are available in live operations.';
  if (
    resource.booking_status === 'confirmed' ||
    (world.reservations ?? []).some(
      (item) => item.resource_id === resource.id && item.status === 'confirmed',
    ) ||
    (world.offers ?? []).some(
      (offer) =>
        (offer.status === 'confirmed' &&
          offer.itinerary.legs.some((leg) => leg.resource_id === resource.id)) ||
        offer.external_bookings?.some((booking) => booking.resource_id === resource.id),
    )
  )
    return 'A booking is recorded for this service. This report cannot cancel or change it.';
  return null;
}

export function reportDescription(value: string): string {
  const description = value.trim();
  if (description.length < 3 || description.length > 2000 || /\p{Cc}/u.test(description))
    throw new Error('Enter a description of 3–2,000 characters without control characters.');
  return description;
}

export function arrivalReportInput(
  world: World,
  incident: RecoveryIncident,
  form: ArrivalReportForm,
): ArrivalReportInput {
  if (
    !form.groupIds.length ||
    form.groupIds.length > 500 ||
    new Set(form.groupIds).size !== form.groupIds.length
  )
    throw new Error('Select at least one linked passenger group.');
  for (const id of form.groupIds) {
    const group = world.groups.find((item) => item.id === id);
    if (!group) throw new Error('A selected group is no longer present. Review the selection.');
    const issue = arrivalReportIssue(world, incident, group);
    if (issue) throw new Error(issue);
  }
  const airport_code = form.airportCode.trim().toUpperCase();
  if (!/^[A-Z]{3}$/.test(airport_code))
    throw new Error('Enter the reported airport’s three-letter IATA code.');
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(form.readyAt))
    throw new Error('Enter the passenger-ready date and time in UTC, to the whole minute.');
  return {
    group_ids: [...form.groupIds],
    airport_code,
    ready_at: utcInputToIso(form.readyAt),
    description: reportDescription(form.description),
  };
}

export function affectedPendingGroups(world: World, resourceId: string): PassengerGroup[] {
  const groupIds = new Set(
    (world.offers ?? [])
      .filter(
        (offer) =>
          ['proposed', 'accepted'].includes(offer.status) &&
          offer.itinerary.legs.some((leg) => leg.resource_id === resourceId),
      )
      .map((offer) => offer.group_id),
  );
  return world.groups.filter((group) => groupIds.has(group.id));
}

export function latestOperatorReport(
  provenance: Record<string, unknown> | undefined,
  field: 'arrival_reports' | 'unavailability_reports',
) {
  const records = provenance?.[field];
  if (!Array.isArray(records)) return null;
  const last: unknown = records.at(-1);
  if (!last || typeof last !== 'object' || Array.isArray(last)) return null;
  const report = last as Record<string, unknown>;
  if (
    report.source !== 'operator_report' ||
    typeof report.description !== 'string' ||
    typeof report.received_at !== 'string' ||
    sourceInstant(report.received_at) === null
  )
    return null;
  return { description: report.description, receivedAt: report.received_at };
}
