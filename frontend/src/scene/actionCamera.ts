import type { SceneMode, World } from '../types';
import { busSpots, gates, groupPosition } from './layout';
import { buildNetworkProjection } from './networkProjection';

type Vec = [number, number, number];
export type SceneCamera = { target: Vec; position: Vec };

/** Frame the same schematic coordinates and visible slots used by the scene. */
export function sceneCameraFor(
  world: World,
  mode: SceneMode,
  selectedId: string | null,
  narrow = 1,
  followSelection = false,
): SceneCamera {
  if (mode === 'region') {
    if (followSelection && selectedId) {
      const projection = buildNetworkProjection(world.airports);
      const resource = world.resources.find((item) => item.id === selectedId);
      const ids = resource ? [resource.origin, resource.destination] : [selectedId];
      const points = ids.flatMap((id) => {
        const node = world.airports.find((airport) => airport.id === id || airport.code === id);
        const point = node && projection.points.get(node.id);
        return point ? [point] : [];
      });
      if (points.length) {
        const xs = points.map((point) => point[0]);
        const zs = points.map((point) => point[2]);
        const x = (Math.min(...xs) + Math.max(...xs)) / 2;
        const z = (Math.min(...zs) + Math.max(...zs)) / 2;
        const height =
          Math.max(
            120,
            (Math.max(...xs) - Math.min(...xs)) * 1.5,
            (Math.max(...zs) - Math.min(...zs)) * 1.8,
          ) * narrow;
        // Terrain elevation is illustrative; leave room above its raised relief.
        const y = projection.regional ? 15 : 2;
        return { target: [x, y, z], position: [x + height * 0.2, y + height, z + height] };
      }
    }
    return { target: [0, 0, 10], position: [25 * narrow, 370 * narrow, 360 * narrow] };
  }
  if (mode === 'passenger') {
    const index = Math.max(
      0,
      world.groups.findIndex((group) => group.id === selectedId),
    );
    const p = groupPosition(index < 26 ? index : 0);
    return {
      target: [p[0], 2, p[2]],
      position: [p[0] + 30 * narrow, 26 * narrow, p[2] + 42 * narrow],
    };
  }
  const bus = world.resources
    .filter((item) => item.kind === 'bus' && (item.origin === 'CRA' || item.destination === 'CRA'))
    .slice(0, 6)
    .findIndex((item) => item.id === selectedId);
  if (bus >= 0) {
    const p = busSpots[bus];
    return {
      target: [p[0], 2, p[2] - 25],
      position: [p[0] + 96 * narrow, 92 * narrow, p[2] + 115 * narrow],
    };
  }
  if (followSelection) {
    const flight = world.resources
      .filter((item) => item.kind === 'flight' && item.origin === 'CRA')
      .slice(0, 3)
      .findIndex((item) => item.id === selectedId);
    if (flight >= 0) {
      const p = gates[flight];
      return {
        target: [p[0], 3, p[2]],
        position: [p[0] + 90 * narrow, 85 * narrow, p[2] + 115 * narrow],
      };
    }
    if (selectedId === 'H-CRA' && world.resources.some((item) => item.id === selectedId))
      return {
        target: [172, 10, 77],
        position: [172 + 100 * narrow, 95 * narrow, 77 + 120 * narrow],
      };
  }
  return { target: [0, 0, -15], position: [250 * narrow, 270 * narrow, 350 * narrow] };
}
