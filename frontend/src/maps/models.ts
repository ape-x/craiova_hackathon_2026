import type { World } from '../types';
import type { GeoPoint, ModelOptions } from './contracts';
import { observedPoint, pointOf } from './overlays';
import {
  passengerPoint,
  resourcePoint,
  resourceProgress,
  sceneHeading,
  scenePoint,
} from './placement';

export const MODEL_URLS = {
  aircraft: '/scene/models/aircraft.glb',
  bus: '/scene/models/bus.glb',
  terminal: '/scene/models/terminal.glb',
  passenger1: '/scene/models/passengers-1.glb',
  passenger2: '/scene/models/passengers-2.glb',
  passenger3: '/scene/models/passengers-3.glb',
  passenger4: '/scene/models/passengers-4.glb',
  passenger5: '/scene/models/passengers-5.glb',
} as const;
export interface GeoModel {
  id: string;
  title: string;
  observed: boolean;
  kind?: 'aircraft' | 'bus' | 'terminal' | 'passengers';
  options: ModelOptions;
}
export interface ModelVisibility {
  vehicles: boolean;
  passengers: boolean;
  terminal: boolean;
}
export const ALL_MODELS: ModelVisibility = { vehicles: true, passengers: true, terminal: true };

function headingBetween(a: GeoPoint, b: GeoPoint): number {
  const radians = Math.PI / 180;
  const delta = (b.lng - a.lng) * radians;
  const y = Math.sin(delta) * Math.cos(b.lat * radians);
  const x =
    Math.cos(a.lat * radians) * Math.sin(b.lat * radians) -
    Math.sin(a.lat * radians) * Math.cos(b.lat * radians) * Math.cos(delta);
  return (Math.atan2(y, x) / radians + 360) % 360;
}

/** Generic authored assets use meters, Y-up, front -Z. Google uses a vertical Z axis: tilt 270 maps Y-up
 * to the map vertical and -Z-forward to north before applying heading.
 * Model scale is deliberately enlarged, disclosed in the map legend. Roll/tilt
 * are a level visual posture, not measured aircraft attitude.
 */
export function buildModelDescriptors(
  world: World,
  selectedId: string | null,
  visibility = ALL_MODELS,
): GeoModel[] {
  const models: GeoModel[] = [];
  const used = new Set<string>();
  const observations = [...world.observations].sort(
    (a, b) =>
      Number(String(b.id ?? b.fr24_id) === selectedId) -
      Number(String(a.id ?? a.fr24_id) === selectedId),
  );
  for (const observation of visibility.vehicles ? observations : []) {
    const id = String(observation.id ?? observation.fr24_id ?? '');
    const point = observedPoint(observation);
    const heading = observation.heading ?? observation.track;
    // Keep the provenance marker when attitude/height are unavailable; placing
    // a 3D aircraft at an invented altitude or direction would be misleading.
    if (
      !id ||
      used.has(id) ||
      !point ||
      point.altitude === undefined ||
      typeof heading !== 'number' ||
      !Number.isFinite(heading) ||
      heading < 0 ||
      heading > 360
    )
      continue;
    used.add(id);
    models.push({
      id,
      observed: true,
      kind: 'aircraft',
      title: `${observation.callsign ?? id}: generic aircraft at last source position and direction; scale enlarged 2.5×. See marker for timestamp and provenance.`,
      options: {
        src: MODEL_URLS.aircraft,
        position: point,
        orientation: { heading: heading % 360, tilt: 270, roll: 0 },
        scale: 2.5,
        altitudeMode: 'ABSOLUTE',
      },
    });
    if (models.length === 20) break;
  }
  for (const resource of visibility.vehicles ? world.resources : []) {
    if (resource.kind === 'hotel') continue;
    const point = resourcePoint(resource, world);
    const origin = world.airports.find((airport) => airport.id === resource.origin);
    const destination = world.airports.find((airport) => airport.id === resource.destination);
    if (!point || !origin || !destination) continue;
    const flight = resource.kind === 'flight';
    const progress = resourceProgress(resource, world);
    const travelling = progress > 0 && progress < 1;
    models.push({
      id: resource.id,
      observed: false,
      kind: flight ? 'aircraft' : 'bus',
      title: `${resource.id} · SIMULATED ${resource.available ? 'vehicle' : 'UNAVAILABLE vehicle'}. Authored layout, not GPS telemetry.`,
      options: {
        src: flight ? MODEL_URLS.aircraft : MODEL_URLS.bus,
        position: point,
        orientation: {
          heading: travelling
            ? headingBetween(pointOf(origin), pointOf(destination))
            : (sceneHeading(progress === 1 ? destination : origin) + (flight ? 180 : 90)) % 360,
          tilt: 270,
          roll: 0,
        },
        scale: 1,
        altitudeMode: travelling && flight ? 'RELATIVE_TO_GROUND' : 'CLAMP_TO_GROUND',
      },
    });
  }
  for (const airport of visibility.terminal ? world.airports : []) {
    if (
      !world.resources.some((r) => r.origin === airport.id || r.destination === airport.id) &&
      !world.groups.some((g) => g.origin === airport.id)
    )
      continue;
    models.push({
      id: airport.id,
      observed: false,
      kind: 'terminal',
      title: `${airport.id} · SIMULATED terminal from the authored airport scene. Not a surveyed building.`,
      options: {
        src: MODEL_URLS.terminal,
        position: scenePoint(airport, 0, 51),
        orientation: { heading: sceneHeading(airport), tilt: 270, roll: 0 },
        scale: 1,
        altitudeMode: 'CLAMP_TO_GROUND',
      },
    });
  }
  const passengerAssets = [
    MODEL_URLS.passenger1,
    MODEL_URLS.passenger2,
    MODEL_URLS.passenger3,
    MODEL_URLS.passenger4,
    MODEL_URLS.passenger5,
  ];
  for (const group of visibility.passengers ? world.groups : []) {
    const point = passengerPoint(group, world);
    if (!point || group.size < 1) continue;
    // A moving family is represented by its vehicle, not people floating beside it.
    const boarded = world.offers.some(
      (offer) =>
        offer.group_id === group.id &&
        offer.status === 'confirmed' &&
        offer.itinerary.legs.some(
          (leg) =>
            leg.kind !== 'hotel' &&
            world.sim_time >= leg.departure &&
            world.sim_time < leg.arrival &&
            world.resources.some((r) => r.id === leg.resource_id && r.available),
        ),
    );
    if (boarded) continue;
    models.push({
      id: group.id,
      observed: false,
      kind: 'passengers',
      title: `${group.name} · ${group.size} SIMULATED passengers · ${group.status}. Up to five representative figures.`,
      options: {
        src: passengerAssets[Math.min(5, Math.max(1, Math.floor(group.size))) - 1],
        position: point,
        orientation: { heading: 0, tilt: 270, roll: 0 },
        scale: 1,
        altitudeMode: 'CLAMP_TO_GROUND',
      },
    });
  }
  return models;
}

/** Verify local assets lazily. Google has no documented per-model load event.
 * This detects missing/corrupt assets; it does not claim a verified GPU render.
 */
export function createModelAssetLoader(fetcher: typeof fetch) {
  const pending = new Map<string, Promise<void>>();
  return (src: string): Promise<void> => {
    if (!Object.values(MODEL_URLS).some((value) => value === src))
      return Promise.reject(new Error('Unrecognized authored model URL.'));
    let checked = pending.get(src);
    if (!checked) {
      checked = (async () => {
        const response = await fetcher(src, {
          credentials: 'same-origin',
          // Asset names are stable across deployments. Revalidate the cached
          // binary before Google loads it so a new export replaces old meshes.
          cache: 'no-cache',
          signal: AbortSignal.timeout(10_000),
        });
        if (!response.ok)
          throw new Error(`Authored 3D model unavailable (HTTP ${response.status}).`);
        const bytes = await response.arrayBuffer();
        if (bytes.byteLength < 20 || bytes.byteLength > 2_000_000)
          throw new Error('Authored 3D model has an invalid size.');
        const header = new DataView(bytes);
        if (
          header.getUint32(0, true) !== 0x46546c67 ||
          header.getUint32(4, true) !== 2 ||
          header.getUint32(8, true) !== bytes.byteLength
        )
          throw new Error('Authored 3D model is not a valid GLB 2 asset.');
      })();
      pending.set(src, checked);
      // Retry after a later explicit layer mount if a transient fetch failed.
      void checked.catch(() => {
        if (pending.get(src) === checked) pending.delete(src);
      });
    }
    return checked;
  };
}

let checkedAssets: ReturnType<typeof createModelAssetLoader> | undefined;
export function ensureModelAsset(src: string): Promise<void> {
  checkedAssets ??= createModelAssetLoader(fetch);
  return checkedAssets(src);
}
