import type { World } from './types';

type GroupRecoveryScope = {
  scenarioId: string;
  sourceVersion: number;
  groupId: string;
};
export type GroupRecoveryIntent =
  | (GroupRecoveryScope & { kind: 'recover' | 'review_case'; incidentId: string })
  | (GroupRecoveryScope & { kind: 'setup' });
export interface GroupRecoveryEntry {
  intent: GroupRecoveryIntent;
  label: string;
  detail: string;
}

export function isGroupSupplierConfirmed(
  world: Pick<World, 'groups' | 'offers' | 'reservations' | 'resources'>,
  groupId: string,
): boolean {
  const offers = world.offers.filter((offer) => offer.group_id === groupId);
  const allocatedResourceIds = new Set(
    offers
      .filter((offer) => ['held', 'proposed', 'accepted', 'confirmed'].includes(offer.status))
      .flatMap((offer) => offer.itinerary.legs.map((leg) => leg.resource_id)),
  );
  return (
    world.groups.some((group) => group.id === groupId && group.status === 'confirmed') ||
    offers.some(
      (offer) => offer.status === 'confirmed' || Boolean(offer.external_bookings?.length),
    ) ||
    world.reservations.some(
      (reservation) => reservation.group_id === groupId && reservation.status === 'confirmed',
    ) ||
    world.resources.some(
      (resource) =>
        allocatedResourceIds.has(resource.id) && resource.booking_status === 'confirmed',
    )
  );
}

/** Navigation only: a reciprocal case link never authorizes a provider request. */
export function groupRecoveryEntry(world: World, groupId: string): GroupRecoveryEntry | null {
  if (
    world.mode !== 'live' ||
    !world.scenario_id ||
    !Number.isSafeInteger(world.version) ||
    world.version < 0
  )
    return null;
  const groups = world.groups.filter((group) => group.id === groupId);
  if (!groups.length) return null;
  const scope: GroupRecoveryScope = {
    scenarioId: world.scenario_id,
    sourceVersion: world.version,
    groupId,
  };
  const setup = (detail: string): GroupRecoveryEntry => ({
    intent: { ...scope, kind: 'setup' },
    label: 'Set up recovery case',
    detail,
  });
  if (groups.length !== 1)
    return setup(
      'This passenger identifier has conflicting records. Review the group requirements and case linkage before starting a supplier search.',
    );
  const group = groups[0],
    cases = world.incidents ?? [];
  const owners = cases.filter((incident) => incident.group_ids.includes(groupId));
  if (!group.incident_id && !owners.length)
    return setup(
      'This group is not linked to a recovery case. Open recovery setup, choose the exact flight case and explicitly link this group’s passenger requirements.',
    );
  const linked = cases.filter((incident) => incident.id === group.incident_id);
  if (
    linked.length !== 1 ||
    owners.length !== 1 ||
    owners[0] !== linked[0] ||
    linked[0].group_ids.filter((id) => id === groupId).length !== 1
  )
    return setup(
      'This group’s case linkage is incomplete or conflicting. Review its passenger requirements and case membership before starting a supplier search.',
    );

  const bookings = isGroupSupplierConfirmed(world, groupId);
  return {
    intent: { ...scope, kind: bookings ? 'review_case' : 'recover', incidentId: linked[0].id },
    label: bookings ? 'Review linked case' : 'Find sourced alternatives',
    detail: bookings
      ? `This group has recorded bookings or an allocated confirmed service. Open case ${linked[0].id} to review them; no new recovery is preselected.`
      : `Open case ${linked[0].id} with only this group selected. Review services and the call allowance before starting a new supplier search.`,
  };
}
