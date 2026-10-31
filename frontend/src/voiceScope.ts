import type { World } from './types';
import type { VoiceStartupScope } from './liveVoice';

/** Current selection supplies references, never a command or permission to run recovery. */
export function voiceStartupScope(
  world: World | null,
  selectedId: string | null,
  caseId: string | null,
  caseOpen: boolean,
): VoiceStartupScope | undefined {
  if (!world || world.mode !== 'live') return undefined;
  const incidents = world.incidents ?? [];
  const selectedGroup = world.groups.find((group) => group.id === selectedId);
  const selectedCase = incidents.find((incident) => incident.id === caseId);
  if (caseOpen && selectedCase) {
    const linked =
      selectedGroup?.incident_id === selectedCase.id &&
      selectedCase.group_ids.includes(selectedGroup.id);
    return { incident_id: selectedCase.id, ...(linked ? { group_id: selectedGroup!.id } : {}) };
  }
  if (selectedGroup) {
    const incident = incidents.find(
      (item) => item.id === selectedGroup.incident_id && item.group_ids.includes(selectedGroup.id),
    );
    return { group_id: selectedGroup.id, ...(incident ? { incident_id: incident.id } : {}) };
  }
  // Closing the case to reach the microphone keeps its explicit reference;
  // stale cases and arbitrary vehicle/quote selections never create a scope.
  return selectedCase ? { incident_id: selectedCase.id } : undefined;
}
