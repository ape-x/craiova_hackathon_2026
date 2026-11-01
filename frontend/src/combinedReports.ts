import type { MutationResult, OperationalReportReceipt } from './types';
import { sourceInstant } from './resourceFreshness';

type Arrival = Extract<OperationalReportReceipt, { type: 'arrival_report' }>;
type Restriction = Extract<OperationalReportReceipt, { type: 'airport_restriction' }>;
type Unavailability = Extract<OperationalReportReceipt, { type: 'resource_unavailability' }>;
export type CombinedReportCheck =
  | { state: 'none' }
  | { state: 'invalid' }
  | { state: 'combined'; kind: 'airport_and_arrival'; arrival: Arrival; restriction: Restriction }
  | { state: 'combined'; kind: 'resource_and_arrival'; arrival: Arrival; resource: Unavailability };

export const UNCONFIRMED_COMBINED_REPORT =
  'Răspunsul conține rapoarte incomplete sau necorelate. Nu pot confirma împreună modificarea operațională, poziția pasagerilor și starea căutării. Verifică starea cazului înainte de o nouă comandă.';

const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);
const text = (value: unknown, max = 128): value is string =>
  typeof value === 'string' && value.trim().length > 0 && value.length <= max;
const ids = (value: unknown, nonempty = false): value is string[] =>
  Array.isArray(value) &&
  (!nonempty || value.length > 0) &&
  value.length <= 1000 &&
  value.every((id) => text(id)) &&
  new Set(value).size === value.length;
const sameIds = (left: string[], right: string[]) =>
  left.length === right.length && left.every((id) => right.includes(id));
const airport = (value: unknown) => typeof value === 'string' && /^[A-Z0-9]{3,4}$/.test(value);

/** Compare JSON values without depending on object key order; arrays remain exact. */
function sameValue(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left))
    return (
      Array.isArray(right) &&
      left.length === right.length &&
      left.every((item, index) => sameValue(item, right[index]))
    );
  if (!record(left) || !record(right)) return false;
  const keys = Object.keys(left);
  return (
    keys.length === Object.keys(right).length &&
    keys.every((key) => Object.hasOwn(right, key) && sameValue(left[key], right[key]))
  );
}

function baseReport(value: unknown): value is Record<string, unknown> {
  return (
    record(value) &&
    text(value.id) &&
    value.source === 'operator_report' &&
    sourceInstant(value.received_at) !== null &&
    text(value.description, 2000) &&
    ids(value.affected_group_ids) &&
    ids(value.affected_offer_ids)
  );
}

function reportHistory(value: unknown): value is Record<string, unknown>[] {
  if (!Array.isArray(value) || !value.length || !value.every(record)) return false;
  const seen = new Set<string>();
  let previous = -Infinity;
  return value.every((entry) => {
    const received = sourceInstant(entry.received_at);
    if (!text(entry.id) || seen.has(entry.id) || received === null || received < previous)
      return false;
    seen.add(entry.id);
    previous = received;
    return true;
  });
}

/** Check the immutable response snapshot, including on replay. This proves what
 * was recorded together, not that its availability or passenger position is current now. */
function recordedResourceAndArrival(
  result: MutationResult,
  resource: Unavailability,
  arrival: Arrival,
): boolean {
  const world = result.world;
  const affectedOffers = [
    ...new Set([...resource.affected_offer_ids, ...arrival.affected_offer_ids]),
  ];
  const affectedGroups = [
    ...new Set([...resource.affected_group_ids, ...arrival.affected_group_ids]),
  ];
  if (
    result.mode !== 'ai' ||
    result.command_interpretation_calls !== 1 ||
    result.focus_id !== arrival.airport_code ||
    !Number.isSafeInteger(world.version) ||
    !Array.isArray(world.resources) ||
    !Array.isArray(world.groups) ||
    !Array.isArray(world.incidents) ||
    !Array.isArray(world.airports) ||
    !Array.isArray(world.events) ||
    (result.invalidated_offer_ids !== undefined &&
      (!ids(result.invalidated_offer_ids) ||
        !sameIds(result.invalidated_offer_ids, affectedOffers))) ||
    (result.affected_group_ids !== undefined &&
      (!ids(result.affected_group_ids) || !sameIds(result.affected_group_ids, affectedGroups))) ||
    !arrival.affected_group_ids.every((id) => arrival.group_ids.includes(id)) ||
    (result.manual_review_required !== undefined && result.manual_review_required !== false) ||
    (result.confirmed_conflicts !== undefined &&
      (!Array.isArray(result.confirmed_conflicts) || result.confirmed_conflicts.length !== 0))
  )
    return false;
  const targets = world.resources.filter(
    (item) => record(item) && item.id === resource.resource_id,
  );
  if (
    targets.length !== 1 ||
    targets[0].available !== false ||
    !['bus', 'flight', 'hotel'].includes(targets[0].kind)
  )
    return false;
  const history = targets[0].provenance?.unavailability_reports;
  if (!reportHistory(history) || !sameValue(history.at(-1), resource)) return false;

  const cases = world.incidents.filter((item) => record(item) && item.id === arrival.incident_id);
  const airports = world.airports.filter(
    (item) => record(item) && item.id === arrival.airport_code,
  );
  const epoch = sourceInstant(world.epoch);
  const ready = sourceInstant(arrival.ready_at);
  if (
    cases.length !== 1 ||
    !ids(cases[0].group_ids, true) ||
    airports.length !== 1 ||
    airports[0].kind !== 'airport' ||
    airports[0].code !== arrival.airport_code ||
    epoch === null ||
    ready === null
  )
    return false;
  const groupsRecorded = arrival.group_ids.every((id) => {
    const groups = world.groups.filter((item) => record(item) && item.id === id);
    if (groups.length !== 1 || !cases[0].group_ids.includes(id)) return false;
    const group = groups[0];
    const entries = group.provenance?.arrival_reports;
    if (
      group.incident_id !== arrival.incident_id ||
      group.origin !== arrival.airport_code ||
      group.ready_at !== Math.ceil((ready - epoch) / 60_000) ||
      !text(group.original_flight_id) ||
      !reportHistory(entries)
    )
      return false;
    const latest = entries.at(-1)!;
    return (
      latest.group_id === id &&
      latest.scenario_id === world.scenario_id &&
      latest.epoch === world.epoch &&
      latest.original_flight_id === group.original_flight_id &&
      Object.entries(arrival).every(
        ([key, value]) =>
          ['group_ids', 'affected_group_ids', 'affected_offer_ids'].includes(key) ||
          sameValue(latest[key], value),
      )
    );
  });
  if (!groupsRecorded) return false;
  const eventFor = (report: OperationalReportReceipt, type: string, target: string) => {
    const events = world.events.filter(
      (event) => record(event) && record(event.data) && event.data.id === report.id,
    );
    return events.length === 1 &&
      events[0].type === type &&
      events[0].target_id === target &&
      events[0].version === world.version &&
      sameValue(events[0].data, report)
      ? events[0]
      : null;
  };
  const resourceEvent = eventFor(
    resource,
    'operator_resource_unavailability',
    resource.resource_id,
  );
  const arrivalEvent = eventFor(arrival, 'operator_arrival_report', arrival.incident_id);
  return Boolean(
    resourceEvent &&
    arrivalEvent &&
    text(resourceEvent.id) &&
    text(arrivalEvent.id) &&
    resourceEvent.id !== arrivalEvent.id,
  );
}

function conflicts(value: unknown): boolean {
  return (
    Array.isArray(value) &&
    value.length <= 1000 &&
    value.every(
      (conflict) =>
        record(conflict) &&
        text(conflict.group_id) &&
        (conflict.offer_id === null || text(conflict.offer_id)) &&
        ids(conflict.resource_ids),
    )
  );
}

/** An optional plural receipt is atomic: never silently fall back to one child. */
export function combinedReports(result: MutationResult): CombinedReportCheck {
  if (result.reports === undefined) return { state: 'none' };
  const reports: unknown = result.reports;
  if (
    result.applied !== true ||
    result.world.mode !== 'live' ||
    !text(result.incident_id) ||
    !Array.isArray(reports) ||
    reports.length !== 2
  )
    return { state: 'invalid' };
  const [first, arrival] = reports;
  if (
    !baseReport(first) ||
    !baseReport(arrival) ||
    arrival.type !== 'arrival_report' ||
    first.id === arrival.id ||
    arrival.incident_id !== result.incident_id ||
    !airport(arrival.airport_code) ||
    !sameValue(result.report, arrival) ||
    !ids(arrival.group_ids, true) ||
    arrival.group_ids.length > 24 ||
    sourceInstant(arrival.ready_at) === null ||
    (result.recovery_job && result.recovery_blocked) ||
    (result.recovery_job &&
      (result.recovery_job.incident_id !== result.incident_id ||
        !ids(result.recovery_job.group_ids, true) ||
        !sameIds(result.recovery_job.group_ids, arrival.group_ids))) ||
    (result.focus_id != null && result.focus_id !== arrival.airport_code)
  )
    return { state: 'invalid' };
  if (first.type === 'resource_unavailability') {
    if (
      !text(first.resource_id) ||
      typeof first.previous_available !== 'boolean' ||
      !recordedResourceAndArrival(result, first as Unavailability, arrival as Arrival)
    )
      return { state: 'invalid' };
    return {
      state: 'combined',
      kind: 'resource_and_arrival',
      resource: first as Unavailability,
      arrival: arrival as Arrival,
    };
  }
  const restriction = first;
  if (
    restriction.type !== 'airport_restriction' ||
    restriction.incident_id !== result.incident_id ||
    !airport(restriction.airport_code) ||
    !Array.isArray(restriction.operations) ||
    restriction.operations.length < 1 ||
    restriction.operations.length > 2 ||
    new Set(restriction.operations).size !== restriction.operations.length ||
    restriction.operations.some((operation) => !['arrivals', 'departures'].includes(operation)) ||
    !['restrict', 'clear'].includes(String(restriction.action)) ||
    !ids(restriction.recovery_group_ids) ||
    (restriction.recovery_group_ids.length > 0 &&
      (!sameIds(restriction.recovery_group_ids, arrival.group_ids) ||
        !(result.recovery_job || result.recovery_blocked))) ||
    ((result.recovery_job || result.recovery_blocked) &&
      !sameIds(restriction.recovery_group_ids, arrival.group_ids)) ||
    (restriction.manual_review_required !== undefined &&
      typeof restriction.manual_review_required !== 'boolean') ||
    (restriction.confirmed_conflicts !== undefined &&
      (!conflicts(restriction.confirmed_conflicts) ||
        restriction.manual_review_required !==
          (restriction.confirmed_conflicts as unknown[]).length > 0)) ||
    (result.manual_review_required !== undefined &&
      result.manual_review_required !== restriction.manual_review_required) ||
    (result.confirmed_conflicts !== undefined &&
      !sameValue(result.confirmed_conflicts, restriction.confirmed_conflicts))
  )
    return { state: 'invalid' };
  return {
    state: 'combined',
    kind: 'airport_and_arrival',
    arrival: arrival as unknown as Arrival,
    restriction: restriction as unknown as Restriction,
  };
}

export function combinedReportDetails(
  bundle: Extract<CombinedReportCheck, { state: 'combined' }>,
  replayed = false,
): string {
  const { arrival } = bundle;
  if (bundle.kind === 'resource_and_arrival') {
    const { resource } = bundle;
    return `Rapoarte ale operatorului ${replayed ? 'deja ' : ''}înregistrate: serviciul ${resource.resource_id} raportat indisponibil pentru planificare; grupurile ${arrival.group_ids.join(', ')} raportate la ${arrival.airport_code}, pregătite de la ${arrival.ready_at}, în cazul ${arrival.incident_id}. Propuneri afectate de indisponibilitate: ${resource.affected_offer_ids.length}, pentru ${resource.affected_group_ids.length} grupuri, posibil din alte cazuri. Aceste rapoarte nu confirmă aterizarea, disponibilitatea actuală la furnizor sau rezervări.`;
  }
  const { restriction } = bundle;
  const operations = restriction.operations
    .map((operation) => (operation === 'arrivals' ? 'aterizări' : 'decolări'))
    .join(' și ');
  return `Rapoarte ale operatorului ${replayed ? 'deja ' : ''}înregistrate: restricția cazului ${restriction.incident_id} pentru ${restriction.airport_code}, ${operations}, ${restriction.action === 'restrict' ? 'aplicată' : 'eliminată'}; grupurile ${arrival.group_ids.join(', ')} raportate la ${arrival.airport_code}, pregătite de la ${arrival.ready_at}. Aceste rapoarte nu confirmă aterizarea, permisiunea de zbor sau rezervări.`;
}
