import type { MutationResult, RecoveryIncident, RecoveryJobInput, World } from './types';
import { arrivalReportInput } from './operationalReports';
import type { ArrivalReportForm, ArrivalReportInput } from './operationalReports';

export type ArrivalRecoveryInput = Pick<
  RecoveryJobInput,
  'objective' | 'modes' | 'max_provider_calls' | 'hotel_recovery'
>;
export interface ArrivalRecoveryForm {
  objective: RecoveryJobInput['objective'];
  modes: RecoveryJobInput['modes'];
  maxProviderCalls: string;
}
export const emptyArrivalRecoveryForm = (): ArrivalRecoveryForm => ({
  objective: 'balanced',
  modes: ['flight'],
  maxProviderCalls: '8',
});

export function arrivalRecoveryIssue(form: ArrivalRecoveryForm, groupCount: number): string | null {
  if (
    !form.modes.length ||
    new Set(form.modes).size !== form.modes.length ||
    form.modes.some((mode) => !['flight', 'bus', 'hotel'].includes(mode))
  )
    return 'Choose the services to consider for this recovery request.';
  if (form.modes.includes('hotel'))
    return 'Hotel recovery needs fresh stay dates, pickup and drop-off inputs for the reported location. Record arrival only, then complete “Hotel + both transfers + onward flight” below. Saved hotel inputs will not be reused.';
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
  const issue = arrivalRecoveryIssue(recovery, input.group_ids.length);
  if (issue) throw new Error(issue);
  return {
    ...input,
    recovery: {
      objective: recovery.objective,
      modes: [...recovery.modes],
      max_provider_calls: Number(recovery.maxProviderCalls),
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
