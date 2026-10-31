import type { MutationResult, World } from './types';
import { ApiError, TransportError } from './api';

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
  return [...history, { request: head(request, 4000), result: head(result, 1600), outcome }].slice(
    -8,
  );
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
  const status =
    result.applied === false
      ? 'Nicio modificare operațională aplicată.'
      : 'Rezultat confirmat de simulator.';
  const explanation = result.explanation ?? result.decision?.explanation;
  return `${status}${explanation ? ` ${explanation}` : ''} Versiunea ${result.world.version}.`;
}

/** A no-change response may be a question the operator still needs to answer. */
export function keepCommandReplyVisible(result: MutationResult): boolean {
  return result.applied === false && !(result.focus_id ?? result.decision?.focus_id);
}

export function commandNeedsRegionalView(before: World, result: MutationResult): boolean {
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
