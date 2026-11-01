import { groupRecoveryEntry, type GroupRecoveryIntent } from './groupRecoveryEntry';
import { resolveRecoveryDataContext, type RecoveryDataContext } from './recoveryDataContext';
import type { RecoveryIncident, World } from './types';

/** Recheck the clicked target without silently moving it to a different case. */
export function currentGroupRecoveryIntent(world: World, requested: GroupRecoveryIntent) {
  const current = groupRecoveryEntry(world, requested.groupId)?.intent;
  if (
    !current ||
    requested.scenarioId !== current.scenarioId ||
    requested.kind !== current.kind ||
    (requested.kind !== 'setup' &&
      (current.kind === 'setup' || requested.incidentId !== current.incidentId))
  )
    return null;
  return current;
}

export function recoveryGroupSelectionIssue(
  world: World,
  incident: RecoveryIncident,
  groupIds: string[],
): string | null {
  if (groupIds.length > 24 || new Set(groupIds).size !== groupIds.length)
    return 'Select at most 24 distinct passenger groups; no subset was chosen.';
  for (const id of groupIds) {
    const entry = groupRecoveryEntry(world, id);
    if (!entry || entry.intent.kind === 'setup')
      return 'A selected group has missing or conflicting case membership. Review the selection before searching.';
    if (entry.intent.incidentId !== incident.id)
      return 'A selected group belongs to a different recovery case. Review the selection before searching.';
    if (entry.intent.kind === 'review_case')
      return 'A selected group has recorded bookings or an allocated confirmed service. Review its existing travel before requesting alternatives.';
  }
  return null;
}

/** One explicit navigation selection; never choose the remaining valid subset. */
export function initialRecoverySelection(
  world: World,
  incident: RecoveryIncident,
  requested?: RecoveryDataContext,
): { groupIds: string[]; issue: string | null } {
  if (!requested) return { groupIds: [], issue: null };
  const context = resolveRecoveryDataContext(world, requested);
  const issue =
    context.kind !== 'ready'
      ? context.kind === 'stale'
        ? context.reason
        : 'Choose the intended passenger groups.'
      : requested.incidentId !== incident.id
        ? 'The requested passenger selection belongs to another case.'
        : recoveryGroupSelectionIssue(world, incident, requested.groupIds);
  return { groupIds: issue ? [] : [...requested.groupIds], issue };
}
