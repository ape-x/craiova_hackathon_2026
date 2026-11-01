import { ApiError, SearchOutcomeUnknownError } from './api';
import { verifiedCommandResult } from './commandContext';
import { combinedReports } from './combinedReports';
import type { DelegationRequest } from './liveVoice';
import type { MutationResult } from './types';

export type VoiceReviewOutcome =
  { kind: 'response'; result: MutationResult } | { kind: 'failure'; cause: unknown };

/** Observe the existing request/recovery path; never retry or submit from review. */
export async function observeVoiceRequestOutcome(
  action: () => Promise<MutationResult>,
  observer?: (outcome: VoiceReviewOutcome) => void,
): Promise<MutationResult> {
  try {
    const result = await action();
    observer?.({ kind: 'response', result });
    return result;
  } catch (cause) {
    observer?.({ kind: 'failure', cause });
    throw cause;
  }
}

export type VoiceRequestReview = Readonly<{
  sequence: number;
  scenarioId: string;
  delegationId: string;
  text: string;
  status:
    | 'pending'
    | 'not_applied'
    | 'rejected'
    | 'applied'
    | 'response_received'
    | 'recovery_recorded'
    | 'report_recorded'
    | 'reports_recorded'
    | 'unknown';
  detail: string;
}>;

export function beginVoiceRequestReview(
  request: DelegationRequest,
  scenarioId: string,
  sequence: number,
): VoiceRequestReview {
  // Retain the exact delegated text, never the cumulative transcript or context.
  return Object.freeze({
    sequence,
    scenarioId,
    delegationId: request.delegationId,
    text: request.text,
    status: 'pending',
    detail: 'Waiting for the backend to confirm the outcome of this request.',
  });
}

function sameAttempt(current: VoiceRequestReview | null, attempt: VoiceRequestReview): boolean {
  return (
    current?.sequence === attempt.sequence &&
    current.scenarioId === attempt.scenarioId &&
    current.delegationId === attempt.delegationId
  );
}

export function settleVoiceRequestReview(
  current: VoiceRequestReview | null,
  attempt: VoiceRequestReview,
  scenarioId: string | undefined,
  outcome: VoiceReviewOutcome,
): VoiceRequestReview | null {
  if (!current || !sameAttempt(current, attempt)) return current;
  if (current.scenarioId !== scenarioId) return null;
  if (outcome.kind === 'failure') {
    const rejected =
      outcome.cause instanceof ApiError && !(outcome.cause instanceof SearchOutcomeUnknownError);
    return Object.freeze({
      ...current,
      status: rejected ? 'rejected' : 'unknown',
      detail:
        outcome.cause instanceof Error
          ? outcome.cause.message
          : 'The outcome could not be confirmed.',
    });
  }
  const result = outcome.result;
  if (result.world.scenario_id !== scenarioId) return null;
  const bundle = combinedReports(result);
  const status: VoiceRequestReview['status'] =
    bundle.state === 'invalid'
      ? 'unknown'
      : result.recovery_job
        ? 'recovery_recorded'
        : bundle.state === 'combined'
          ? 'reports_recorded'
          : result.report
            ? 'report_recorded'
            : result.applied === false
              ? 'not_applied'
              : result.applied === true
                ? 'applied'
                : 'response_received';
  return Object.freeze({ ...current, status, detail: verifiedCommandResult(result) });
}

export const voiceReviewCanCorrect = (review: VoiceRequestReview): boolean =>
  review.status === 'not_applied' || review.status === 'rejected';

export const voiceReviewStatusLabel: Record<VoiceRequestReview['status'], string> = {
  pending: 'Awaiting backend result',
  not_applied: 'No operational change applied',
  rejected: 'Request rejected',
  applied: 'Operational change recorded',
  response_received: 'Backend response received',
  recovery_recorded: 'Recovery request recorded',
  report_recorded: 'Operational report recorded',
  reports_recorded: 'Operational reports recorded',
  unknown: 'Outcome unconfirmed',
};

export type VoiceReviewDraftContext = {
  scenarioId: string | undefined;
  draft: string;
  voiceActive: boolean;
  busy: boolean;
};

export function voiceReviewDraftIssue(
  review: VoiceRequestReview,
  context: VoiceReviewDraftContext,
): string | null {
  if (review.scenarioId !== context.scenarioId)
    return 'This request belongs to a previous operation.';
  if (review.status === 'pending')
    return 'Wait for the backend result before correcting this request.';
  if (review.status === 'unknown')
    return 'Do not send a corrected copy while its outcome is unknown. If Recover request is available in the existing notification, use it to check the same request.';
  if (!voiceReviewCanCorrect(review))
    return 'This request may have changed the operation or started recovery. Review its recorded result; copying it as a new request is unavailable.';
  if (context.voiceActive)
    return 'End the voice session with the microphone control before editing.';
  if (context.busy) return 'Wait for the current operation to finish before editing.';
  if (context.draft.length > 0)
    return 'The command draft already contains text. Clear it first; review never replaces an existing draft.';
  return null;
}

/** Copy only. The ordinary command form owns the later explicit submission. */
export function voiceReviewDraftText(
  current: VoiceRequestReview | null,
  displayed: VoiceRequestReview,
  context: VoiceReviewDraftContext,
): string | null {
  if (!current || !sameAttempt(current, displayed) || voiceReviewDraftIssue(current, context))
    return null;
  return current.text;
}
