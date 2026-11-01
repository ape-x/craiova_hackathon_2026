import type { PassengerGroup, RecoveryIncident, World } from './types';
import type { RecoveryFormDraft } from './recoveryFormDraft';

export interface RecoveryDataContext {
  scenarioId: string;
  incidentId: string;
  groupIds: string[];
  searchDraft?: RecoveryFormDraft;
}

export type RecoveryDataContextState =
  | { kind: 'general' }
  | { kind: 'stale'; reason: string }
  | { kind: 'ready'; incident: RecoveryIncident; groups: PassengerGroup[] };

export function resolveRecoveryDataContext(
  world: World,
  context?: RecoveryDataContext,
): RecoveryDataContextState {
  if (!context) return { kind: 'general' };
  if (world.mode !== 'live' || context.scenarioId !== world.scenario_id)
    return {
      kind: 'stale',
      reason:
        'The operation has changed. Open the intended recovery case again from the workspace.',
    };
  const incidents = (world.incidents ?? []).filter(
    (incident) => incident.id === context.incidentId,
  );
  if (incidents.length !== 1)
    return {
      kind: 'stale',
      reason: 'The original recovery case is no longer available in this operation.',
    };
  const incident = incidents[0];
  if (new Set(context.groupIds).size !== context.groupIds.length)
    return {
      kind: 'stale',
      reason: 'The original passenger selection is ambiguous. Reopen the case.',
    };
  const groups: PassengerGroup[] = [];
  for (const id of context.groupIds) {
    const matches = world.groups.filter((group) => group.id === id);
    if (
      matches.length !== 1 ||
      matches[0].incident_id !== incident.id ||
      !incident.group_ids.includes(id)
    )
      return {
        kind: 'stale',
        reason:
          'The original passenger selection is no longer linked to this case. Open the intended case again to review its groups.',
      };
    groups.push(matches[0]);
  }
  return { kind: 'ready', incident, groups };
}

export function recoveryDataReturnIssue(
  world: World,
  context: RecoveryDataContext | undefined,
  pending: boolean,
): string | null {
  if (pending)
    return 'Finish or recover the pending submission before returning to the recovery case.';
  const state = resolveRecoveryDataContext(world, context);
  if (state.kind === 'stale') return state.reason;
  if (state.kind === 'general') return 'No recovery case was selected for this data session.';
  return null;
}
