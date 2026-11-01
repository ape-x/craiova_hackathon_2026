import type { MutationResult, OperationalReportReceipt } from './types';
import { sourceInstant } from './resourceFreshness';

type Arrival = Extract<OperationalReportReceipt, { type: 'arrival_report' }>;
type Restriction = Extract<OperationalReportReceipt, { type: 'airport_restriction' }>;
export type CombinedReportCheck =
  | { state: 'none' }
  | { state: 'invalid' }
  | { state: 'combined'; arrival: Arrival; restriction: Restriction };

export const UNCONFIRMED_COMBINED_REPORT =
  'Răspunsul conține rapoarte incomplete sau necorelate. Nu pot confirma împreună restricția, poziția pasagerilor și starea căutării. Verifică starea cazului înainte de o nouă comandă.';

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
    ids(value.affected_offer_ids) &&
    text(value.incident_id) &&
    airport(value.airport_code)
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
  const [restriction, arrival] = reports;
  if (
    !baseReport(restriction) ||
    !baseReport(arrival) ||
    restriction.type !== 'airport_restriction' ||
    arrival.type !== 'arrival_report' ||
    restriction.id === arrival.id ||
    restriction.incident_id !== result.incident_id ||
    arrival.incident_id !== result.incident_id ||
    !sameValue(result.report, arrival) ||
    !ids(arrival.group_ids, true) ||
    arrival.group_ids.length > 24 ||
    sourceInstant(arrival.ready_at) === null ||
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
    (result.recovery_job && result.recovery_blocked) ||
    (result.recovery_job &&
      (result.recovery_job.incident_id !== result.incident_id ||
        !ids(result.recovery_job.group_ids, true) ||
        !sameIds(result.recovery_job.group_ids, arrival.group_ids))) ||
    (result.focus_id != null && result.focus_id !== arrival.airport_code) ||
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
    arrival: arrival as unknown as Arrival,
    restriction: restriction as unknown as Restriction,
  };
}

export function combinedReportDetails(
  bundle: Extract<CombinedReportCheck, { state: 'combined' }>,
  replayed = false,
): string {
  const { restriction, arrival } = bundle;
  const operations = restriction.operations
    .map((operation) => (operation === 'arrivals' ? 'aterizări' : 'decolări'))
    .join(' și ');
  return `Rapoarte ale operatorului ${replayed ? 'deja ' : ''}înregistrate: restricția cazului ${restriction.incident_id} pentru ${restriction.airport_code}, ${operations}, ${restriction.action === 'restrict' ? 'aplicată' : 'eliminată'}; grupurile ${arrival.group_ids.join(', ')} raportate la ${arrival.airport_code}, pregătite de la ${arrival.ready_at}. Aceste rapoarte nu confirmă aterizarea, permisiunea de zbor sau rezervări.`;
}
