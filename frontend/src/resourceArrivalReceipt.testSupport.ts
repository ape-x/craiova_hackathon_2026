import type { MutationResult, OperationalReportReceipt, ResourceKind, World } from './types';

/** An immutable response snapshot with both reports recorded in one revision. */
export function resourceArrivalReceipt(
  options: {
    groupIds?: string[];
    resourceId?: string;
    resourceKind?: ResourceKind;
    airportCode?: string;
    readyAt?: string;
  } = {},
): MutationResult {
  const groupIds = options.groupIds ?? ['G1'];
  const resourceId = options.resourceId ?? 'BUS1';
  const airportCode = options.airportCode ?? 'OTP';
  const readyAt = options.readyAt ?? '2026-11-01T12:30:00+02:00';
  const epoch = new Date(Date.parse(readyAt) - 3_600_000).toISOString();
  const arrival: OperationalReportReceipt = {
    id: `opr_${'a'.repeat(24)}`,
    type: 'arrival_report',
    source: 'operator_report',
    received_at: epoch,
    description: 'Selected passengers reported ready at the airport.',
    incident_id: 'CASE1',
    airport_code: airportCode,
    group_ids: groupIds,
    ready_at: readyAt,
    affected_group_ids: [],
    affected_offer_ids: [],
  };
  const resource: OperationalReportReceipt = {
    id: `opr_${'b'.repeat(24)}`,
    type: 'resource_unavailability',
    source: 'operator_report',
    received_at: epoch,
    description: 'Operator reports the selected service unavailable.',
    resource_id: resourceId,
    previous_available: true,
    affected_group_ids: [groupIds[0], 'OTHER-CASE-GROUP'],
    affected_offer_ids: ['O1', 'O2'],
  };
  const arrivalEntry = Object.fromEntries(
    Object.entries(arrival).filter(
      ([key]) => !['group_ids', 'affected_group_ids', 'affected_offer_ids'].includes(key),
    ),
  );
  const world = {
    scenario_id: 'OP1',
    mode: 'live',
    version: 8,
    epoch,
    incidents: [
      { id: 'CASE1', group_ids: [...groupIds, 'UNSELECTED'] },
      { id: 'CASE2', group_ids: ['OTHER-CASE-GROUP'] },
    ],
    airports: [{ id: airportCode, code: airportCode, kind: 'airport' }],
    groups: [
      ...groupIds.map((id) => ({
        id,
        incident_id: 'CASE1',
        original_flight_id: 'ORIGINAL-FLIGHT',
        origin: airportCode,
        ready_at: 60,
        provenance: {
          arrival_reports: [
            {
              ...structuredClone(arrivalEntry),
              scenario_id: 'OP1',
              epoch,
              original_flight_id: 'ORIGINAL-FLIGHT',
              group_id: id,
              previous_origin: 'CRA',
              previous_ready_at: 0,
              affected_offer_ids: [],
            },
          ],
        },
      })),
      { id: 'UNSELECTED', incident_id: 'CASE1', origin: 'CRA' },
      { id: 'OTHER-CASE-GROUP', incident_id: 'CASE2', origin: 'CRA' },
    ],
    resources: [
      {
        id: resourceId,
        kind: options.resourceKind ?? 'bus',
        available: false,
        provenance: { unavailability_reports: [structuredClone(resource)] },
      },
    ],
    events: [
      {
        id: 'EVENT1',
        type: 'operator_resource_unavailability',
        target_id: resourceId,
        version: 8,
        data: structuredClone(resource),
      },
      {
        id: 'EVENT2',
        type: 'operator_arrival_report',
        target_id: 'CASE1',
        version: 8,
        data: structuredClone(arrival),
      },
    ],
  } as unknown as World;
  return {
    world,
    applied: true,
    mode: 'ai',
    report: structuredClone(arrival),
    reports: [structuredClone(resource), structuredClone(arrival)],
    incident_id: 'CASE1',
    focus_id: airportCode,
    command_interpretation_calls: 1,
    invalidated_offer_ids: [...resource.affected_offer_ids],
    affected_group_ids: [...resource.affected_group_ids],
  };
}
