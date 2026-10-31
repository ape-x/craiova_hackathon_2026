import type { World } from '../types';
import type { GeoPoint, ModelOptions } from './contracts';
import { observedPoint, pointOf, resourcePoint } from './overlays';

export const MODEL_URLS = {
  aircraft: '/scene/models/aircraft.glb',
  bus: '/scene/models/bus.glb',
} as const;
export interface GeoModel {
  id: string;
  title: string;
  observed: boolean;
  options: ModelOptions;
}

function headingBetween(a: GeoPoint, b: GeoPoint): number {
  const radians = Math.PI / 180;
  const delta = (b.lng - a.lng) * radians;
  const y = Math.sin(delta) * Math.cos(b.lat * radians);
  const x =
    Math.cos(a.lat * radians) * Math.sin(b.lat * radians) -
    Math.sin(a.lat * radians) * Math.cos(b.lat * radians) * Math.cos(delta);
  return (Math.atan2(y, x) / radians + 360) % 360;
}

/** Generic authored assets use meters, Y-up, front -Z. Heading 0 faces north.
 * Model scale is deliberately enlarged, disclosed in the map legend. Roll/tilt
 * are a level visual posture, not measured aircraft attitude.
 */
export function buildModelDescriptors(world: World, selectedId: string | null): GeoModel[] {
  const models: GeoModel[] = [];
  const used = new Set<string>();
  const observations = [...world.observations].sort(
    (a, b) =>
      Number(String(b.id ?? b.fr24_id) === selectedId) -
      Number(String(a.id ?? a.fr24_id) === selectedId),
  );
  for (const observation of observations) {
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
      title: `${observation.callsign ?? id}: generic aircraft at last source position and direction; scale enlarged 2.5×. See marker for timestamp and provenance.`,
      options: {
        src: MODEL_URLS.aircraft,
        position: point,
        orientation: { heading: heading % 360, tilt: 0, roll: 0 },
        scale: 2.5,
        altitudeMode: 'ABSOLUTE',
      },
    });
    if (models.length === 20) break;
  }
  const resource = world.resources.find((item) => item.id === selectedId);
  if (!resource || resource.kind === 'hotel') return models;
  const point = resourcePoint(resource, world);
  const origin = world.airports.find((airport) => airport.id === resource.origin);
  const destination = world.airports.find((airport) => airport.id === resource.destination);
  if (!point || !origin || !destination) return models;
  const flight = resource.kind === 'flight';
  models.push({
    id: resource.id,
    observed: false,
    title: `${resource.id} · SIMULATED position and direction. Generic model enlarged ${flight ? '2.5' : '4'}×; not GPS telemetry.`,
    options: {
      src: flight ? MODEL_URLS.aircraft : MODEL_URLS.bus,
      position: point,
      orientation: {
        heading: headingBetween(pointOf(origin), pointOf(destination)),
        tilt: 0,
        roll: 0,
      },
      scale: flight ? 2.5 : 4,
      altitudeMode: flight && point.altitude ? 'ABSOLUTE' : 'CLAMP_TO_GROUND',
    },
  });
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
          cache: 'force-cache',
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
    }
    return checked;
  };
}

let checkedAssets: ReturnType<typeof createModelAssetLoader> | undefined;
export function ensureModelAsset(src: string): Promise<void> {
  checkedAssets ??= createModelAssetLoader(fetch);
  return checkedAssets(src);
}
