import type { MutationResult, World } from './types';
import { combinedReports } from './combinedReports';

/** Focus a reported passenger location only while that report is still current. */
export function reportedArrivalFocus(result: MutationResult, current: World): string | null {
  if (combinedReports(result).state === 'invalid') return null;
  const report = result.report;
  if (
    result.applied !== true ||
    current.mode !== 'live' ||
    result.world.scenario_id !== current.scenario_id ||
    report?.type !== 'arrival_report' ||
    report.source !== 'operator_report' ||
    !report.group_ids.length
  )
    return null;
  if (
    !current.airports.some(
      (airport) =>
        airport.id === report.airport_code &&
        airport.code === report.airport_code &&
        airport.kind === 'airport',
    )
  )
    return null;
  const currentReport = report.group_ids.every((id) => {
    const group = current.groups.find((item) => item.id === id);
    const history = group?.provenance?.arrival_reports;
    const last = Array.isArray(history) ? history.at(-1) : null;
    return (
      group?.incident_id === report.incident_id &&
      group.origin === report.airport_code &&
      last !== null &&
      typeof last === 'object' &&
      last.id === report.id
    );
  });
  return currentReport ? report.airport_code : null;
}
