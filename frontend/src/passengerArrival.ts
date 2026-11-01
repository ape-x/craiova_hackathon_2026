import type { PassengerGroup, World } from './types';
import { sourceInstant } from './resourceFreshness';
const object = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === 'object' && !Array.isArray(value);

/** A current server report can resolve passenger planning readiness without
 * changing the original flight's observed or operational status. */
export function passengerArrivalPending(world: World, group: PassengerGroup): boolean {
  const pending = world.resources.some(
    (resource) =>
      resource.id === group.original_flight_id &&
      (resource.status === 'needs_diversion' ||
        (resource.status === 'weather_held' && group.origin === resource.destination)),
  );
  if (!pending) return false;
  if (world.mode !== 'live') return true;
  const history = group.provenance?.arrival_reports;
  if (!Array.isArray(history) || !history.length || !history.every(object)) return true;
  const ids = new Set<string>();
  let previous = -Infinity;
  for (const entry of history) {
    const received = sourceInstant(entry.received_at);
    if (
      typeof entry.id !== 'string' ||
      !entry.id ||
      ids.has(entry.id) ||
      received === null ||
      received < previous
    )
      return true;
    ids.add(entry.id);
    previous = received;
  }
  const latest = history.at(-1)!,
    epoch = sourceInstant(world.epoch),
    ready = sourceInstant(latest.ready_at);
  const airport = world.airports.find((item) => item.id === group.origin);
  const cases = (world.incidents ?? []).filter(
    (item) => item.id === group.incident_id && item.group_ids.includes(group.id),
  );
  return !(
    latest.source === 'operator_report' &&
    latest.type === 'arrival_report' &&
    /^opr_[a-f0-9]{24}$/.test(String(latest.id)) &&
    latest.scenario_id === world.scenario_id &&
    latest.epoch === world.epoch &&
    latest.original_flight_id === group.original_flight_id &&
    latest.group_id === group.id &&
    latest.incident_id === group.incident_id &&
    cases.length === 1 &&
    latest.airport_code === group.origin &&
    airport?.kind === 'airport' &&
    airport.code === group.origin &&
    epoch !== null &&
    ready !== null &&
    Math.ceil((ready - epoch) / 60_000) === group.ready_at
  );
}
