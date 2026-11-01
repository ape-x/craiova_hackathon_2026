import type {
  AirportOperation,
  MutationResult,
  PassengerGroup,
  RecoveryIncident,
  World,
} from './types';
import type { ArrivalRecoveryForm, ArrivalRecoveryInput } from './arrivalRecoveryData';
import { arrivalRecoveryIssue } from './arrivalRecoveryData';
import { reportDescription } from './operationalReports';

export interface AirportRestrictionInput {
  airport_code: string;
  operations: AirportOperation[];
  action: 'restrict' | 'clear';
  description: string;
  recovery?: ArrivalRecoveryInput & { group_ids: string[] };
}
export interface AirportRestrictionForm {
  airportCode: string;
  operations: AirportOperation[];
  action: 'restrict' | 'clear';
  description: string;
  groupIds: string[];
}
export const emptyAirportRestrictionForm = (): AirportRestrictionForm => ({
  airportCode: '',
  operations: ['arrivals'],
  action: 'restrict',
  description: '',
  groupIds: [],
});

export function airportOperationsLabel(operations: readonly AirportOperation[]): string {
  return operations.includes('arrivals') && operations.includes('departures')
    ? 'Arrivals and departures'
    : operations.includes('arrivals')
      ? 'Arrivals'
      : 'Departures';
}

/** The legacy creation constraint blocked both directions until explicitly migrated. */
export function activeCaseRestrictions(incident: RecoveryIncident) {
  const restrictions = [...(incident.airport_restrictions ?? [])];
  if (incident.unavailable_airport_code) {
    for (const operation of ['arrivals', 'departures'] as const) {
      if (
        !restrictions.some(
          (item) =>
            item.airport_code === incident.unavailable_airport_code && item.operation === operation,
        )
      )
        restrictions.push({
          airport_code: incident.unavailable_airport_code,
          operation,
          source: 'operator_report',
          report_id: incident.id,
          received_at: incident.created_at,
          description: incident.description,
        });
    }
  }
  return restrictions;
}

export function restrictionRecoveryGroupIssue(
  world: World,
  incident: RecoveryIncident,
  group: PassengerGroup,
): string | null {
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
    return 'Recorded booking · review separately before recalculating this group.';
  return null;
}

export function airportRestrictionRecoveryIssue(
  recovery: ArrivalRecoveryForm,
  groupCount: number,
): string | null {
  if (recovery.modes.includes('hotel'))
    return 'Hotel recovery needs fresh stay and transfer inputs. Record this restriction update only, then complete “Hotel + both transfers + onward flight” below. Saved hotel inputs will not be reused.';
  const issue = arrivalRecoveryIssue(recovery, groupCount);
  return issue?.startsWith('Select 1–24')
    ? 'Select 1–24 linked groups for optional recalculation. Recording the restriction does not require a group selection.'
    : issue;
}

export function airportRestrictionSubmission(
  world: World,
  incident: RecoveryIncident,
  form: AirportRestrictionForm,
  recovery: ArrivalRecoveryForm,
  action: 'report' | 'recover',
): AirportRestrictionInput {
  if (world.mode !== 'live')
    throw new Error('Case airport restrictions are available in live operations.');
  const airport_code = form.airportCode.trim().toUpperCase();
  if (!/^[A-Z]{3}$/.test(airport_code))
    throw new Error('Enter the airport’s three-letter IATA code.');
  if (
    !form.operations.length ||
    new Set(form.operations).size !== form.operations.length ||
    form.operations.some((operation) => !['arrivals', 'departures'].includes(operation))
  )
    throw new Error('Choose arrivals, departures or both.');
  if (!['restrict', 'clear'].includes(form.action)) throw new Error('Choose restrict or clear.');
  const input: AirportRestrictionInput = {
    airport_code,
    operations: [...form.operations],
    action: form.action,
    description: reportDescription(form.description),
  };
  if (action === 'report') return input;
  const issue = airportRestrictionRecoveryIssue(recovery, form.groupIds.length);
  if (issue) throw new Error(issue);
  if (new Set(form.groupIds).size !== form.groupIds.length)
    throw new Error('Select each linked group only once.');
  for (const id of form.groupIds) {
    const group = world.groups.find((item) => item.id === id);
    if (!group) throw new Error('A selected group is no longer present. Review the selection.');
    const groupIssue = restrictionRecoveryGroupIssue(world, incident, group);
    if (groupIssue) throw new Error(groupIssue);
  }
  return {
    ...input,
    recovery: {
      group_ids: [...form.groupIds],
      objective: recovery.objective,
      modes: [...recovery.modes],
      max_provider_calls: Number(recovery.maxProviderCalls),
    },
  };
}

export function airportRestrictionRecoveryReceipt(result: MutationResult) {
  const report = result.report;
  if (report?.type !== 'airport_restriction') return null;
  const job = result.recovery_job;
  const scope = report.recovery_group_ids;
  if (
    result.applied !== true ||
    report.incident_id !== result.incident_id ||
    (job && result.recovery_blocked) ||
    (job &&
      (job.incident_id !== report.incident_id ||
        !scope?.length ||
        new Set(scope).size !== scope.length ||
        job.group_ids.length !== scope.length ||
        !job.group_ids.every((id) => scope.includes(id))))
  )
    return {
      title: 'Restriction receipt needs review',
      detail:
        'The returned scope or outcome is inconsistent. Review this case before requesting another search.',
      warning: true,
    };
  if (result.recovery_blocked)
    return {
      title: 'Restriction update recorded · recovery not started',
      detail: result.recovery_blocked.explanation,
      warning: true,
    };
  if (!job) return null;
  const waiting = job.status === 'queued' && job.stage === 'waiting_for_previous';
  return {
    title: waiting
      ? 'Restriction update recorded · recovery waiting'
      : 'Restriction update recorded · recovery request recorded',
    detail: `${job.id} · Recorded status: ${job.status.replaceAll('_', ' ')}. ${waiting ? 'At recording, this request was waiting for earlier attempts to finish. ' : ''}${job.calls_dispatched} calls dispatched at recording · limit ${job.max_provider_calls}. Review current progress below; this receipt does not confirm an itinerary or booking.`,
    warning: ['needs_review', 'unknown_outcome', 'blocked', 'failed', 'cancelled'].includes(
      job.status,
    ),
  };
}
