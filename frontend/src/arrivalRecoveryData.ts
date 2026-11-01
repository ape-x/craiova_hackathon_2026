import type { MutationResult, RecoveryIncident, RecoveryJobInput, World } from './types';
import { arrivalReportInput } from './operationalReports';
import type { ArrivalReportForm, ArrivalReportInput } from './operationalReports';
import { utcInputToIso } from './liveData';
import { airportTransferInputs, airportTransferScope } from './airportTransferRecovery';
import type { AirportTransferDraft } from './airportTransferRecovery';
import { afterFlightTransferInput, type AfterFlightTransferDraft } from './afterFlightTransfer';

export type ArrivalRecoveryInput = Pick<
  RecoveryJobInput,
  | 'objective'
  | 'modes'
  | 'max_provider_calls'
  | 'hotel_recovery'
  | 'airport_transfers'
  | 'after_flight_transfer'
>;
export interface ArrivalRecoveryForm {
  objective: RecoveryJobInput['objective'];
  modes: RecoveryJobInput['modes'];
  maxProviderCalls: string;
  airportTransfers?: AirportTransferDraft[];
  afterFlightTransfer?: AfterFlightTransferDraft | null;
}
export const emptyArrivalRecoveryForm = (): ArrivalRecoveryForm => ({
  objective: 'balanced',
  modes: ['flight'],
  maxProviderCalls: '8',
});

export function projectedArrivalRecovery(world: World, form: ArrivalReportForm) {
  try {
    const airport = form.airportCode.trim().toUpperCase(),
      ready = Date.parse(utcInputToIso(form.readyAt)),
      epoch = Date.parse(world.epoch);
    if (!/^[A-Z]{3}$/.test(airport) || !Number.isFinite(epoch) || !Number.isFinite(ready))
      return null;
    return {
      world: {
        ...world,
        groups: world.groups.map((group) =>
          form.groupIds.includes(group.id)
            ? { ...group, origin: airport, ready_at: (ready - epoch) / 60_000 }
            : group,
        ),
      },
      groupIds: form.groupIds,
      // Preview only: arrivalReportInput validates the actual report before submission.
      reportedArrivalGroupIds: [...form.groupIds],
      scopeKey: airportTransferScope(world, form.groupIds, JSON.stringify([airport, form.readyAt])),
    };
  } catch {
    return null;
  }
}

export function arrivalRecoveryIssue(
  form: ArrivalRecoveryForm,
  groupCount: number,
  context?: {
    world: World;
    groupIds: string[];
    scopeKey?: string;
    reportedArrivalGroupIds?: string[];
  } | null,
): string | null {
  if (
    !form.modes.length ||
    new Set(form.modes).size !== form.modes.length ||
    form.modes.some((mode) => !['flight', 'bus', 'hotel'].includes(mode))
  )
    return 'Choose the services to consider for this recovery request.';
  if (form.modes.includes('hotel'))
    return 'Hotel recovery needs fresh stay dates and the selected package’s passenger, baggage and connection inputs for the reported location. Record arrival only, then choose a package in “Hotel + both transfers + onward flight” below. Saved hotel inputs will not be reused.';
  if (!['balanced', 'fastest', 'lowest_cost'].includes(form.objective))
    return 'Choose a recovery objective.';
  if (
    !/^\d{1,2}$/.test(form.maxProviderCalls) ||
    Number(form.maxProviderCalls) < 3 ||
    Number(form.maxProviderCalls) > 26
  )
    return 'Enter a whole-number recovery call limit from 3 to 26.';
  if (!Number.isInteger(groupCount) || groupCount < 1 || groupCount > 24)
    return 'Select 1–24 linked groups for report and recalculation. Report-only supports a larger scope.';
  if (form.afterFlightTransfer) {
    if (!context)
      return 'Complete the reported airport and readiness before reviewing after-flight transfer inputs.';
    try {
      afterFlightTransferInput(
        form.afterFlightTransfer,
        context.world,
        context.groupIds,
        form.modes,
        Number(form.maxProviderCalls),
        Boolean(form.airportTransfers?.length),
        context.scopeKey,
        Date.now(),
        context.reportedArrivalGroupIds,
      );
    } catch (cause) {
      return cause instanceof Error ? cause.message : 'Review after-flight transfer inputs.';
    }
  }
  if (form.airportTransfers?.length) {
    if (!context)
      return 'Complete the reported airport and readiness before reviewing airport-transfer inputs.';
    try {
      airportTransferInputs(
        form.airportTransfers,
        context.world,
        context.groupIds,
        form.modes,
        Number(form.maxProviderCalls),
        false,
        context.scopeKey,
      );
    } catch (cause) {
      return cause instanceof Error ? cause.message : 'Review the airport-transfer inputs.';
    }
  }
  return null;
}

export function arrivalReportSubmission(
  world: World,
  incident: RecoveryIncident,
  form: ArrivalReportForm,
  recovery: ArrivalRecoveryForm,
  action: 'report' | 'recover',
): ArrivalReportInput {
  const input = arrivalReportInput(world, incident, form);
  if (action === 'report') return input;
  const projected = projectedArrivalRecovery(world, form);
  const issue = arrivalRecoveryIssue(recovery, input.group_ids.length, projected);
  if (issue) throw new Error(issue);
  const transfers =
    recovery.airportTransfers?.length && projected
      ? airportTransferInputs(
          recovery.airportTransfers,
          projected.world,
          input.group_ids,
          recovery.modes,
          Number(recovery.maxProviderCalls),
          false,
          projected.scopeKey,
        )
      : [];
  const afterFlight = projected
    ? afterFlightTransferInput(
        recovery.afterFlightTransfer,
        projected.world,
        input.group_ids,
        recovery.modes,
        Number(recovery.maxProviderCalls),
        transfers.length > 0,
        projected.scopeKey,
        Date.now(),
        projected.reportedArrivalGroupIds,
      )
    : undefined;
  return {
    ...input,
    recovery: {
      objective: recovery.objective,
      modes: [...recovery.modes],
      max_provider_calls: Number(recovery.maxProviderCalls),
      ...(transfers.length ? { airport_transfers: transfers } : {}),
      ...(afterFlight ? { after_flight_transfer: afterFlight } : {}),
    },
  };
}

export function arrivalRecoveryReceipt(result: MutationResult) {
  const job = result.recovery_job;
  const blocked = result.recovery_blocked;
  if (!job && !blocked) return null;
  const report = result.report;
  const validReport =
    result.applied === true &&
    report?.type === 'arrival_report' &&
    report.incident_id === result.incident_id;
  const matchingJob =
    !job ||
    (job.incident_id === result.incident_id &&
      report?.type === 'arrival_report' &&
      job.group_ids.length === report.group_ids.length &&
      job.group_ids.every((id) => report.group_ids.includes(id)));
  if (!validReport || !matchingJob || (job && blocked))
    return {
      title: 'Recovery receipt needs review',
      detail:
        'The returned scope or outcome is inconsistent. Review the case before requesting another search.',
      warning: true,
    };
  if (blocked)
    return {
      title: 'Arrival recorded · recovery not started',
      detail: blocked.explanation,
      warning: true,
    };
  if (!job) return null;
  const waiting = job.status === 'queued' && job.stage === 'waiting_for_previous';
  return {
    title: waiting
      ? 'Arrival recorded · recovery waiting'
      : 'Arrival recorded · recovery request recorded',
    detail: `${job.id} · Recorded status: ${job.status.replaceAll('_', ' ')}. ${waiting ? 'At recording, this request was waiting for earlier attempts to finish. ' : ''}${job.calls_dispatched} calls dispatched at recording · limit ${job.max_provider_calls}. Review current progress below; this receipt does not confirm a completed itinerary or booking.`,
    warning: ['needs_review', 'unknown_outcome', 'blocked', 'failed', 'cancelled'].includes(
      job.status,
    ),
  };
}
