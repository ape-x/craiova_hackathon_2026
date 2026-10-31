import type { AppConfig, MutationResult, ScenarioChange, World } from './types';

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
  config: (signal?: AbortSignal) => request<AppConfig>('/config', undefined, signal),
  world: (signal?: AbortSignal) => request<World>('/world', undefined, signal),
  login: (token: string) => request('/auth/login', { token }),
  logout: () => request('/auth/logout', {}),
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
  command: (world: World, text: string) =>
    request<MutationResult>('/commands', {
      text,
      expected_version: world.version,
      idempotency_key: key(),
    }),
  reset: (world: World) =>
    request<MutationResult>('/reset', { expected_version: world.version, idempotency_key: key() }),
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
    .find((offer) => offer.group_id === groupId && ['held', 'confirmed'].includes(offer.status));
}
export function formatTime(minutes: number) {
  const day = Math.floor(minutes / 1440);
  return `${String(Math.floor(minutes / 60) % 24).padStart(2, '0')}:${String(Math.floor(minutes % 60)).padStart(2, '0')}${day ? ` +${day}d` : ''}`;
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
