import type { RecoveryJob, RecoveryJobStage } from './types';

export const recoveryJobActive = (job: RecoveryJob): boolean =>
  job.status === 'queued' || job.status === 'running';

export const recoveryStageLabel: Record<RecoveryJobStage, string> = {
  queued: 'Queued',
  validating: 'Checking case and passenger requirements',
  planning_queries: 'Preparing provider searches',
  searching_flights: 'Searching flight suppliers',
  evaluating: 'Checking returned alternatives',
  deciding: 'Evaluating recovery decisions',
  applying: 'Recording proposals',
  finished: 'Finished',
};

/** A poll started before a mutation must not erase a new job or regress its status. */
export function mergeRecoveryJobs(current: RecoveryJob[], incoming: RecoveryJob[]): RecoveryJob[] {
  const jobs = new Map(current.map((job) => [job.id, job]));
  for (const job of incoming) {
    const previous = jobs.get(job.id);
    const difference = previous ? Date.parse(job.updated_at) - Date.parse(previous.updated_at) : 1;
    if (
      !previous ||
      difference > 0 ||
      (difference === 0 && (!recoveryJobActive(job) || recoveryJobActive(previous)))
    )
      jobs.set(job.id, job);
  }
  return [...jobs.values()].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
}
