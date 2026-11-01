import { reportedArrivalFocus } from './arrivalFocus';
import { combinedReports } from './combinedReports';
import { recoveryCaseForResult } from './commandContext';
import type { MutationResult, SceneMode, World } from './types';

export type CommandView = { selectedId: string | null; mode: SceneMode };

const targetedEvents = new Set([
  'resource_unavailable',
  'flight_cancelled',
  'airport_closed',
  'resource_capacity',
  'group_deadline',
  'group_accessibility',
  'resource_accessibility',
  'flight_diverted',
  'group_budget',
  'resource_restored',
  'airport_added',
  'resource_added',
  'resource_schedule',
  'resource_route',
  'resource_cost',
  'airport_restored',
  'airport_diversion_eligibility',
  'group_destination',
  'group_priority',
  'airport_weather',
]);

function targetView(world: World, id: string | null | undefined): CommandView | null {
  if (!id) return null;
  if (world.groups.some((group) => group.id === id)) return { selectedId: id, mode: 'passenger' };
  if (world.airports.some((airport) => airport.id === id))
    return {
      selectedId: id,
      mode: world.mode === 'simulation' && id !== 'CRA' ? 'region' : 'airport',
    };
  const resource = world.resources.find((item) => item.id === id);
  if (resource) {
    // The architectural airport only renders these CRA services. Everything
    // else belongs on the network, where its actual endpoints are represented.
    const local = world.resources
      .filter(
        (item) =>
          item.kind === resource.kind &&
          (item.origin === 'CRA' || (item.kind === 'bus' && item.destination === 'CRA')),
      )
      .slice(0, resource.kind === 'flight' ? 3 : 6)
      .some((item) => item.id === id);
    return {
      selectedId: id,
      mode:
        world.mode === 'live' || (resource.kind === 'hotel' ? id === 'H-CRA' : local)
          ? 'airport'
          : 'region',
    };
  }
  if (world.observations.some((item) => String(item.id ?? item.fr24_id ?? '') === id))
    return { selectedId: id, mode: 'airport' };
  return null;
}

/** A receipt drives navigation only after its world has actually been accepted.
 * Never derive targets from transcript text or from unrelated historical events.
 */
export function commandView(
  before: World,
  result: MutationResult,
  current: World,
): CommandView | null {
  if (
    result.world.scenario_id !== current.scenario_id ||
    result.world.version !== current.version ||
    current.version < before.version ||
    combinedReports(result).state === 'invalid'
  )
    return null;

  const applied =
    result.applied === true ||
    (result.applied !== false &&
      result.decision?.applied_version === result.world.version &&
      result.decision.source_version === before.version);
  const replaced = before.scenario_id !== current.scenario_id;
  if (
    replaced &&
    !(
      applied &&
      before.mode === 'simulation' &&
      current.mode === 'simulation' &&
      current.events.some(
        (event) =>
          event.type === 'scenario_reset' &&
          event.version === current.version &&
          event.data.source === 'simulation',
      )
    )
  )
    return null;

  if (result.reports !== undefined || result.report?.type === 'arrival_report')
    return targetView(current, reportedArrivalFocus(result, current));
  if (result.report) {
    const report = result.report;
    if (!applied || current.mode !== 'live' || report.source !== 'operator_report') return null;
    if (report.type === 'airport_restriction') {
      const latest = current.incidents
        ?.find((item) => item.id === report.incident_id)
        ?.airport_restriction_reports?.filter((item) => item.airport_code === report.airport_code)
        .at(-1);
      return latest?.id === report.id ? targetView(current, report.airport_code) : null;
    }
    const history = current.resources.find((item) => item.id === report.resource_id)?.provenance
      ?.unavailability_reports;
    const latest = Array.isArray(history) ? history.at(-1) : null;
    return latest?.id === report.id ? targetView(current, report.resource_id) : null;
  }

  // A focus-only command intentionally has applied:false: it changes the view,
  // not operational data. Validate its entity before following it.
  const explicit =
    targetView(current, result.focus_id) ?? targetView(current, result.decision?.focus_id);
  if (explicit) return explicit;
  const incidentId = recoveryCaseForResult(result, current);
  if (incidentId && result.recovery_job) {
    const groupId = result.recovery_job.group_ids.find((id) =>
      current.groups.some((group) => group.id === id && group.incident_id === incidentId),
    );
    const target = targetView(current, groupId);
    if (target) return target;
  }
  if (!applied) return null;

  const previousEvents = new Set(before.events.map((event) => event.id));
  for (const event of [...current.events].reverse()) {
    if (event.version !== result.world.version || previousEvents.has(event.id)) continue;
    if (targetedEvents.has(event.type)) {
      const target = targetView(current, event.target_id);
      if (target) return target;
    }
    if (event.type === 'passengers_added' && Array.isArray(event.data.group_ids)) {
      for (const id of event.data.group_ids) {
        const target = typeof id === 'string' ? targetView(current, id) : null;
        if (target) return target;
      }
    }
  }
  return replaced ? { selectedId: null, mode: 'region' } : null;
}
