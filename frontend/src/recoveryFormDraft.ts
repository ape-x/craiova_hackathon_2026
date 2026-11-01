import type { AirportTransferDraft } from './airportTransferRecovery';
import type { AfterFlightTransferDraft } from './afterFlightTransfer';
import type { emptyHotelRecoveryForm } from './hotelRecovery';
import type { RecoveryDataContext } from './recoveryDataContext';
import { initialRecoverySelection } from './recoveryNavigation';
import type { RecoveryIncident, RecoveryJobInput, World } from './types';

export interface RecoveryFormDraft {
  groupIds: string[];
  objective: RecoveryJobInput['objective'];
  modes: RecoveryJobInput['modes'];
  limit: string;
  instructions: string;
  hotelMode: boolean;
  hotelFields: ReturnType<typeof emptyHotelRecoveryForm>;
  airportTransfers: AirportTransferDraft[];
  afterFlightTransfer: AfterFlightTransferDraft | null;
}

/** Preserve editable input exactly, including incomplete or invalid values. */
export function cloneRecoveryFormDraft(draft: RecoveryFormDraft): RecoveryFormDraft {
  return structuredClone(draft);
}

/** Navigation restores a draft for review; normal submission validation still applies. */
export function restoreRecoveryFormDraft(
  world: World,
  incident: RecoveryIncident,
  context?: RecoveryDataContext,
): { draft: RecoveryFormDraft | null; issue: string | null } {
  if (!context?.searchDraft) return { draft: null, issue: null };
  const selection = initialRecoverySelection(world, incident, context);
  if (selection.issue) return { draft: null, issue: selection.issue };
  const draft = context.searchDraft;
  if (
    draft.groupIds.length !== context.groupIds.length ||
    draft.groupIds.some((id, index) => id !== context.groupIds[index])
  )
    return {
      draft: null,
      issue:
        'The saved form and navigation selection differ. Review the complete selection; no subset or replacement draft was restored.',
    };
  return { draft: cloneRecoveryFormDraft(draft), issue: null };
}
