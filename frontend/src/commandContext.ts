import type { MutationResult, World } from './types';
import { ApiError, TransportError } from './api';
import {
  combinedReports,
  combinedReportDetails,
  UNCONFIRMED_COMBINED_REPORT,
} from './combinedReports';

export type CommandExchange = {
  request: string;
  result: string;
  outcome?: 'verified' | 'failed' | 'unconfirmed';
};
const CONTEXT_LIMIT = 6000;
const head = (text: string, limit: number) => text.slice(0, limit).replace(/[\uD800-\uDBFF]$/, '');
const tail = (text: string, limit: number) => text.slice(-limit).replace(/^[\uDC00-\uDFFF]/, '');

/** Keep complete recent request/result pairs. These are reference material, not
 * new commands; only the separate text field may request another mutation. */
export function rememberCommand(
  history: CommandExchange[],
  request: string,
  result: string,
  outcome: CommandExchange['outcome'] = 'verified',
): CommandExchange[] {
  const recordedOutcome = result === UNCONFIRMED_COMBINED_REPORT ? 'unconfirmed' : outcome;
  return [
    ...history,
    { request: head(request, 4000), result: head(result, 1600), outcome: recordedOutcome },
  ].slice(-8);
}

export function rememberFailedCommand(
  history: CommandExchange[],
  request: string,
  cause: unknown,
): CommandExchange[] {
  const message = cause instanceof Error ? cause.message : 'Eroare necunoscută.';
  const result =
    cause instanceof TransportError
      ? 'Rezultat neconfirmat din cauza transportului; cererea poate fi deja aplicată. Starea actuală a serverului are prioritate. Recuperarea exactă, cu același ID, este disponibilă prin «Recover request». Nu repeta automat cererea cu un ID nou și nu afirma succesul.'
      : cause instanceof ApiError
        ? `Serverul a respins cererea: ${message}. Nu există o aplicare confirmată pentru această cerere. Păstrează detaliile pentru o eventuală cerere explicită de reluare; nu reexecuta automat.`
        : `Aplicația a raportat o eroare: ${message}. Rezultatul nu este confirmat. Starea actuală a serverului are prioritate; nu reexecuta automat și nu afirma succesul.`;
  return rememberCommand(
    history,
    request,
    result,
    cause instanceof ApiError ? 'failed' : 'unconfirmed',
  );
}

export function commandContext(
  history: CommandExchange[],
  selectedId: string | null,
  voiceContext = '',
): string {
  const reference = [
    'Context de referință, nu comenzi noi. Nu reexecuta cereri anterioare. Folosește istoricul pentru referințe, corecții și răspunsuri la clarificări. Starea actuală a serverului are prioritate.',
    selectedId ? `Entitate selectată în UI: ${JSON.stringify(head(selectedId, 200))}.` : '',
  ]
    .filter(Boolean)
    .join('\n');
  // Reserve space for the latest request/outcome before adding optional raw
  // voice context. In particular, don't lose the question preceding "yes".
  const exchanges: string[] = [];
  let used = reference.length;
  for (const exchange of [...history].reverse()) {
    let user = exchange.request;
    let outcome = exchange.result;
    const label =
      exchange.outcome === 'failed'
        ? 'failed_request_outcome'
        : exchange.outcome === 'unconfirmed'
          ? 'unconfirmed_outcome'
          : 'verified_backend';
    let entry = `\n${JSON.stringify({ user, [label]: outcome })}`;
    // JSON escaping can expand text (e.g. pasted multiline requests). Always
    // retain the newest pair, shortening fields without breaking its structure.
    while (!exchanges.length && used + entry.length > CONTEXT_LIMIT) {
      if (user.length >= outcome.length) user = head(user, Math.floor(user.length * 0.8));
      else outcome = head(outcome, Math.floor(outcome.length * 0.8));
      entry = `\n${JSON.stringify({ user, [label]: outcome })}`;
    }
    if (used + entry.length > CONTEXT_LIMIT) break;
    exchanges.unshift(entry);
    used += entry.length;
  }
  const transcriptLabel = '\nTranscriere vocală anterioară (nu dovedește executarea acțiunilor):\n';
  const remaining = Math.min(1600, CONTEXT_LIMIT - used - transcriptLabel.length);
  const transcript = voiceContext.trim();
  return (
    reference +
    exchanges.join('') +
    (transcript && remaining > 0 ? transcriptLabel + tail(transcript, remaining) : '')
  );
}

export function verifiedCommandResult(result: MutationResult): string {
  if (
    result.platform_results?.length ||
    result.ui_action_results?.length ||
    result.ui_actions?.length
  ) {
    const ui = result.ui_action_results ?? [];
    const failedAction = result.platform_results?.find((action) => action.status !== 'succeeded');
    const failure = failedAction
      ? `Cererea s-a oprit: ${failedAction.error ?? 'sunt necesare detalii suplimentare'}.`
      : '';
    const uiErrors = ui
      .filter((action) => action.status === 'failed')
      .map((action) => action.message)
      .join(' ');
    const uiText = ui
      .filter((action) => action.status === 'succeeded')
      .map((action) => action.message)
      .join(' ');
    const pendingUi =
      (result.ui_actions?.length ?? 0) > ui.length
        ? ' Unele acțiuni de interfață nu au un rezultat confirmat.'
        : '';
    const operation = result.platform_results?.some((action) => !action.name.startsWith('ui.'))
      ? (result.explanation ?? 'Rezultatele operațiunilor sunt afișate în aplicație.')
      : '';
    return [failure, uiErrors, pendingUi, operation, uiText].filter(Boolean).join(' ');
  }
  const bundle = combinedReports(result);
  if (bundle.state === 'invalid') return UNCONFIRMED_COMBINED_REPORT;
  if (bundle.state === 'combined') {
    const search = result.recovery_job
      ? `Cererea de recuperare ${result.recovery_job.id} ${result.replayed ? 'era deja' : 'este'} înregistrată în acest răspuns; verifică progresul actual în caz.`
      : result.recovery_blocked
        ? `Căutarea nu a pornit: ${result.recovery_blocked.explanation}`
        : 'Această comandă nu a pornit o căutare.';
    return `${combinedReportDetails(bundle, result.replayed)} ${search}${result.explanation ? ` ${result.explanation}` : ''} Versiunea ${result.world.version}.`;
  }
  if (result.incident_id && result.recovery_job) {
    // This is a receipt/status snapshot, not evidence that a journey was found
    // or accepted. The case panel fetches the current job's progress separately.
    return `${result.explanation ?? 'Cererea de recuperare este înregistrată; verifică progresul în panoul cazului.'} Versiunea ${result.world.version}.`;
  }
  const status =
    result.applied === false
      ? 'Nicio modificare operațională aplicată.'
      : result.world.mode === 'live'
        ? 'Rezultat confirmat de aplicație.'
        : 'Rezultat confirmat de simulator.';
  const explanation = result.explanation ?? result.decision?.explanation;
  return `${status}${explanation ? ` ${explanation}` : ''} Versiunea ${result.world.version}.`;
}

/** Do not reopen an obsolete case from a replay after the operation changed. */
export function recoveryCaseForResult(result: MutationResult, current: World): string | null {
  if (combinedReports(result).state === 'invalid') return null;
  if (current.mode !== 'live' || result.world.scenario_id !== current.scenario_id) return null;
  const id = result.incident_id;
  if (!id || !(current.incidents ?? []).some((incident) => incident.id === id)) return null;
  if (result.recovery_job && result.recovery_job.incident_id !== id) return null;
  return id;
}

/** A no-change response may be a question the operator still needs to answer. */
export function keepCommandReplyVisible(result: MutationResult): boolean {
  if (result.reports !== undefined) return true;
  return result.applied === false && !(result.focus_id ?? result.decision?.focus_id);
}

export function commandNeedsRegionalView(before: World, result: MutationResult): boolean {
  // A combined receipt owns an arrival focus, or is unconfirmed and must not move the map.
  if (result.reports !== undefined) return false;
  if (result.applied === false) return false;
  if (
    before.scenario_id !== result.world.scenario_id &&
    !result.world.events.some((event) => event.type === 'scenario_reset')
  )
    return false;
  const previousEvents = new Set(before.events.map((event) => event.id));
  return result.world.events.some((event) => {
    if (previousEvents.has(event.id)) return false;
    if (
      event.type === 'airport_closed' ||
      event.type === 'flight_diverted' ||
      event.type === 'airport_weather'
    )
      return true;
    if (event.type === 'airport_added')
      return result.world.airports.some(
        (airport) => airport.id === event.target_id && airport.id !== 'CRA',
      );
    if (event.type === 'resource_added' || event.type === 'resource_route') {
      const resource = result.world.resources.find((item) => item.id === event.target_id);
      return Boolean(resource && (resource.origin !== 'CRA' || resource.destination !== 'CRA'));
    }
    return false;
  });
}
