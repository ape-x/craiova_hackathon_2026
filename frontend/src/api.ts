import type {
  AppConfig,
  CommandScope,
  DemoCaseInput,
  AirportTransferProfiles,
  AfterFlightTransferProfiles,
  RomanianHotelRecoveryProfile,
  FlightLookup,
  IncidentInput,
  IncidentResult,
  InventoryResourceRecord,
  RecoveryJob,
  RecoveryJobInput,
  RecoveryJobResponse,
  MutationResult,
  ScenarioChange,
  World,
} from './types';
import type { PlanningTransferInput, PlanningTransferQuote } from './planningTransferData';
import type { ArrivalReportInput } from './operationalReports';
import type { AirportRestrictionInput } from './airportRestrictionData';

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}
export class TransportError extends Error {
  constructor(public retry: <T>() => Promise<T>) {
    super(
      'The server did not confirm the outcome. Reconnect before retrying; the same request ID will be reused.',
    );
    this.name = 'TransportError';
  }
}
export class SearchOutcomeUnknownError extends ApiError {
  constructor(message: string) {
    super(503, message);
    this.name = 'SearchOutcomeUnknownError';
  }
}
export async function request<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const guardedMutation = Boolean(body && typeof body === 'object' && 'idempotency_key' in body);
  const encodedBody = body === undefined ? undefined : JSON.stringify(body);
  const retry = <R>() =>
    request<R>(path, encodedBody === undefined ? undefined : JSON.parse(encodedBody));
  const timeout = AbortSignal.timeout(body === undefined ? 15000 : 105000);
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      credentials: 'same-origin',
      headers:
        body === undefined
          ? { Accept: 'application/json' }
          : { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: encodedBody,
      signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
    });
  } catch (cause) {
    if (signal?.aborted) throw cause;
    throw new TransportError(retry);
  }
  let data: Record<string, unknown>;
  try {
    data = await response.json();
    if (!data || typeof data !== 'object' || Array.isArray(data))
      throw new Error('Invalid JSON response');
  } catch (cause) {
    if (signal?.aborted) throw cause;
    if (response.ok || (guardedMutation && response.status >= 500)) throw new TransportError(retry);
    throw new ApiError(
      response.status,
      response.statusText || `Request failed (HTTP ${response.status})`,
    );
  }
  if (!response.ok) {
    const details = data.details;
    if (
      ['/transfers/planning-quotes', '/airport-transfers/quotes'].includes(path) &&
      guardedMutation &&
      response.status === 503 &&
      data.applied === null &&
      details !== null &&
      typeof details === 'object' &&
      !Array.isArray(details) &&
      'unknown_outcome' in details &&
      details.unknown_outcome === true &&
      !('in_progress' in details && details.in_progress === true)
    )
      throw new SearchOutcomeUnknownError(
        typeof data.detail === 'string'
          ? data.detail
          : 'The previous source search has an unrecoverable unknown outcome.',
      );
    if (guardedMutation && response.status >= 500 && data.applied !== false)
      throw new TransportError(retry);
    const detail = data.detail ?? data.error ?? response.statusText;
    throw new ApiError(
      response.status,
      typeof detail === 'string' ? detail : JSON.stringify(detail),
    );
  }
  return data as T;
}
export const key = () => crypto.randomUUID();
export const api = {
  createDemoCase: (world: World, input: DemoCaseInput) =>
    request<IncidentResult>('/demo/cases', {
      ...input,
      expected_version: world.version,
      idempotency_key: key(),
    }),
  config: (signal?: AbortSignal) => request<AppConfig>('/config', undefined, signal),
  world: (signal?: AbortSignal) => request<World>('/world', undefined, signal),
  login: (token: string) => request('/auth/login', { token }),
  logout: () => request('/auth/logout', {}),
  planningTransferQuote: (world: World, input: PlanningTransferInput) =>
    request<PlanningTransferQuote>('/transfers/planning-quotes', {
      ...input,
      expected_version: world.version,
      idempotency_key: key(),
    }),
  importPlanningTransfer: (world: World, quoteId: string, optionId: string) =>
    request<MutationResult>(`/transfers/planning-quotes/${encodeURIComponent(quoteId)}/import`, {
      option_id: optionId,
      policy_id: 'ecb_reference_v1',
      expected_version: world.version,
      idempotency_key: key(),
    }),
  lookupFlight: (input: { flight_number: string; date_utc: string }, signal?: AbortSignal) =>
    request<FlightLookup>('/flights/lookup', input, signal),
  recoveryJobs: (incidentId: string, signal?: AbortSignal) =>
    request<{ jobs: RecoveryJob[] }>(
      `/incidents/${encodeURIComponent(incidentId)}/recovery-jobs`,
      undefined,
      signal,
    ),
  airportTransferProfiles: (incidentId: string, signal?: AbortSignal) =>
    request<AirportTransferProfiles>(
      `/incidents/${encodeURIComponent(incidentId)}/airport-transfer-profiles`,
      undefined,
      signal,
    ),
  afterFlightTransferProfiles: (incidentId: string, signal?: AbortSignal) =>
    request<AfterFlightTransferProfiles>(
      `/incidents/${encodeURIComponent(incidentId)}/after-flight-transfer-profiles`,
      undefined,
      signal,
    ),
  romanianHotelRecoveryProfile: (incidentId: string, groupId: string, signal?: AbortSignal) =>
    request<RomanianHotelRecoveryProfile>(
      `/incidents/${encodeURIComponent(incidentId)}/groups/${encodeURIComponent(groupId)}/romanian-hotel-recovery-profile`,
      undefined,
      signal,
    ),
  inventoryResource: (resourceId: string, scenarioId: string, signal?: AbortSignal) =>
    request<InventoryResourceRecord>(
      `/inventory/resources/${encodeURIComponent(resourceId)}?scenario_id=${encodeURIComponent(scenarioId)}`,
      undefined,
      signal,
    ),
  startRecoveryJob: (world: World, incidentId: string, input: RecoveryJobInput) =>
    request<RecoveryJobResponse>(`/incidents/${encodeURIComponent(incidentId)}/recovery-jobs`, {
      ...input,
      expected_version: world.version,
      idempotency_key: key(),
    }),
  cancelRecoveryJob: (jobId: string) =>
    request<RecoveryJobResponse>(`/recovery-jobs/${encodeURIComponent(jobId)}/cancel`, {
      idempotency_key: key(),
    }),
  createIncident: (world: World, input: IncidentInput) =>
    request<IncidentResult>('/incidents', {
      ...input,
      expected_version: world.version,
      idempotency_key: key(),
    }),
  reportArrival: (world: World, incidentId: string, input: ArrivalReportInput) =>
    request<MutationResult>(`/incidents/${encodeURIComponent(incidentId)}/arrival-report`, {
      ...input,
      expected_version: world.version,
      idempotency_key: key(),
    }),
  reportAirportRestriction: (world: World, incidentId: string, input: AirportRestrictionInput) =>
    request<MutationResult>(`/incidents/${encodeURIComponent(incidentId)}/airport-restrictions`, {
      ...input,
      expected_version: world.version,
      idempotency_key: key(),
    }),
  reportUnavailability: (world: World, resourceId: string, input: { description: string }) =>
    request<MutationResult>(`/live/resources/${encodeURIComponent(resourceId)}/unavailability`, {
      ...input,
      expected_version: world.version,
      idempotency_key: key(),
    }),
  bindIncidentGroups: (world: World, incidentId: string, groupIds: string[]) =>
    request<IncidentResult>(`/incidents/${encodeURIComponent(incidentId)}/groups`, {
      group_ids: groupIds,
      expected_version: world.version,
      idempotency_key: key(),
    }),
  refreshIncidentFlight: (
    world: World,
    incidentId: string,
    lookupId: string,
    selectionId: string,
  ) =>
    request<IncidentResult>(`/incidents/${encodeURIComponent(incidentId)}/refresh-flight`, {
      lookup_id: lookupId,
      selection_id: selectionId,
      expected_version: world.version,
      idempotency_key: key(),
    }),
  event: (world: World, change: ScenarioChange) =>
    request<MutationResult>('/events', {
      ...change,
      expected_version: world.version,
      idempotency_key: key(),
    }),
  replan: (world: World, mode: 'ai' | 'solver') =>
    request<MutationResult>('/replan', {
      mode,
      expected_version: world.version,
      idempotency_key: key(),
    }),
  command: (world: World, text: string, context = '', scope?: CommandScope) =>
    request<MutationResult>('/commands', {
      text,
      context,
      ...(scope && Object.keys(scope).length ? { scope } : {}),
      expected_version: world.version,
      idempotency_key: key(),
    }),
  reset: (world: World) =>
    request<MutationResult>('/reset', { expected_version: world.version, idempotency_key: key() }),
  liveInput: (world: World, action: string, input: Record<string, unknown>) =>
    request<MutationResult>(`/live/${action}`, {
      ...input,
      expected_version: world.version,
      idempotency_key: key(),
    }),
};

export function availableUnits(world: World, resourceId: string): number {
  const resource = world.resources.find((item) => item.id === resourceId);
  if (!resource?.available) return 0;
  const used = world.reservations.reduce(
    (total, reservation) =>
      total +
      (reservation.resource_id === resourceId && reservation.status !== 'released'
        ? reservation.quantity
        : 0),
    0,
  );
  return Math.max(0, resource.capacity - used);
}
export function activeOffer(world: World, groupId: string) {
  return [...world.offers]
    .reverse()
    .find(
      (offer) =>
        offer.group_id === groupId &&
        ['held', 'proposed', 'accepted', 'confirmed'].includes(offer.status),
    );
}
export function formatTime(minutes: number) {
  const day = Math.floor(minutes / 1440);
  return `${String(Math.floor(minutes / 60) % 24).padStart(2, '0')}:${String(Math.floor(minutes % 60)).padStart(2, '0')}${day ? ` +${day}d` : ''}`;
}
export function formatOperationTime(epoch: string, minutes: number): string {
  const date = new Date(Date.parse(epoch) + minutes * 60_000);
  if (!Number.isFinite(date.getTime())) return 'Time unavailable';
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Europe/Bucharest',
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}
export function formatWorldTime(world: Pick<World, 'mode' | 'epoch'>, minutes: number): string {
  return world.mode === 'live' ? formatOperationTime(world.epoch, minutes) : formatTime(minutes);
}
export function formatCost(value: number) {
  return new Intl.NumberFormat('en-GB', {
    style: 'currency',
    currency: 'EUR',
    maximumFractionDigits: 0,
  }).format(value);
}

export function downloadSession(world: World) {
  // Passenger bearer links are omitted from portable audit exports.
  const payload = {
    ...world,
    observations: [],
    observation_export: 'External observations omitted from portable audit',
    incidents: (world.incidents ?? []).map(({ flight: _flight, ...incident }) => ({
      ...incident,
      flight: null,
      flight_context_state: incident.source === 'demo_scenario' ? 'not_applicable' : 'expired',
    })),
    incident_flight_export: 'Transient provider flight context omitted from portable audit',
    groups: world.groups.map(({ token: _token, ...group }) => group),
  };
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' }),
  );
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `recovery-${world.scenario_id}-v${world.version}.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}
