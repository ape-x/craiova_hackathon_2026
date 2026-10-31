/** Bounded schematic geography for the fixed regional camera, not navigation. */
export type NetworkPoint = [number, number, number];
type AirportLocation = { id: string; code: string; lat: number; lon: number };

export const regionalPoint = (lat: number, lon: number, height = 0): NetworkPoint => [
  (lon - 24.95) * 79,
  height,
  -(lat - 44.9) * 111,
];

export const inIllustratedRegion = (lat: number, lon: number) => {
  const [x, , z] = regionalPoint(lat, lon);
  return Math.abs(x) <= 175 && Math.abs(z) <= 115;
};

const wrap = (longitude: number) => ((longitude % 360) + 360) % 360;
const valid = (a: AirportLocation) =>
  Number.isFinite(a.lat) &&
  Number.isFinite(a.lon) &&
  Math.abs(a.lat) <= 90 &&
  Math.abs(a.lon) <= 180;

export function buildNetworkProjection(input: AirportLocation[]) {
  const airports = input.filter(valid).sort((a, b) => a.id.localeCompare(b.id));
  const localCount = airports.filter((a) => inIllustratedRegion(a.lat, a.lon)).length;
  // Preserve the original local illustration with one remote connection. All
  // nodes still render; further remote destinations use a fitted world network.
  const regional = airports.length > 0 && localCount >= Math.max(airports.length - 1, 2);
  const longitudes = airports.map((a) => wrap(a.lon)).sort((a, b) => a - b);
  let cut = longitudes[0] ?? 0;
  let largestGap = -1;
  for (let i = 0; i < longitudes.length; i++) {
    const next = longitudes[(i + 1) % longitudes.length] + (i === longitudes.length - 1 ? 360 : 0);
    const gap = next - longitudes[i];
    if (gap > largestGap) {
      largestGap = gap;
      cut = wrap(next);
    }
  }
  const longitude = (lon: number) => {
    // Normalize both operands first. A negative longitude minus a cut already
    // expressed in 0..360 can otherwise become -360.00000000000006, wrapping
    // the westernmost airport to the opposite edge of the world.
    const offset = wrap(wrap(lon) - cut);
    return offset > 360 - 1e-9 ? 0 : offset;
  };
  const centerLat = airports.length
    ? airports.reduce((sum, a) => sum + a.lat, 0) / airports.length
    : 0;
  const latitudeScale = Math.max(0.15, Math.cos((centerLat * Math.PI) / 180));
  const xs = airports.map((a) => longitude(a.lon) * latitudeScale);
  const zs = airports.map((a) => -a.lat);
  const minX = Math.min(...xs, xs[0] ?? 0),
    maxX = Math.max(...xs, xs[0] ?? 0);
  const minZ = Math.min(...zs, zs[0] ?? 0),
    maxZ = Math.max(...zs, zs[0] ?? 0);
  const centerX = (minX + maxX) / 2,
    centerZ = (minZ + maxZ) / 2;
  const scale = Math.min(320 / Math.max(maxX - minX, 1), 175 / Math.max(maxZ - minZ, 1));
  const project = (lat: number, lon: number, height = 2): NetworkPoint => {
    if (regional) {
      const p = regionalPoint(lat, lon, height);
      if (!inIllustratedRegion(lat, lon)) {
        const factor = Math.max(Math.abs(p[0]) / 175, Math.abs(p[2]) / 115, 1);
        p[0] /= factor;
        p[2] /= factor;
      }
      return p;
    }
    return [(longitude(lon) * latitudeScale - centerX) * scale, height, (-lat - centerZ) * scale];
  };
  const points = new Map(airports.map((a) => [a.id, project(a.lat, a.lon)]));
  if (!regional) {
    // Nearby airports (e.g. CRA/OTP/SBZ in a Lisbon network) need distinct hit
    // targets. This slight schematic displacement never changes model geography.
    for (let iteration = 0; iteration < 40; iteration++) {
      for (let i = 0; i < airports.length; i++) {
        const a = points.get(airports[i].id)!;
        for (let j = i + 1; j < airports.length; j++) {
          const b = points.get(airports[j].id)!;
          const dx = b[0] - a[0],
            dz = (b[2] - a[2]) * 0.7;
          const distance = Math.hypot(dx, dz);
          if (distance >= 45) continue;
          const ux = distance > 0.001 ? dx / distance : Math.cos(j * 2.4);
          const uz = distance > 0.001 ? dz / distance : Math.sin(j * 2.4);
          const push = (45 - distance) / 2;
          a[0] = Math.max(-175, Math.min(175, a[0] - ux * push));
          a[2] = Math.max(-105, Math.min(105, a[2] - (uz * push) / 0.7));
          b[0] = Math.max(-175, Math.min(175, b[0] + ux * push));
          b[2] = Math.max(-105, Math.min(105, b[2] + (uz * push) / 0.7));
        }
      }
    }
  }
  return { regional, points, project };
}
