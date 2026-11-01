import type { Camera, GeoPoint } from './contracts';

const wrap = (degrees: number) => ((degrees % 360) + 360) % 360;
const valid = (point: GeoPoint) =>
  Number.isFinite(point.lat) &&
  Number.isFinite(point.lng) &&
  Math.abs(point.lat) <= 90 &&
  Math.abs(point.lng) <= 180;

function distance(a: GeoPoint, b: GeoPoint): number {
  const radians = Math.PI / 180;
  const latitude = (b.lat - a.lat) * radians;
  const longitude = (b.lng - a.lng) * radians;
  const haversine =
    Math.sin(latitude / 2) ** 2 +
    Math.cos(a.lat * radians) * Math.cos(b.lat * radians) * Math.sin(longitude / 2) ** 2;
  return 12_742_000 * Math.asin(Math.sqrt(Math.min(1, Math.max(0, haversine))));
}

/** Frame geographic airport extents, including networks crossing the date line.
 * This is an overview camera; close-up selection uses its own camera settings.
 */
export function networkCamera(input: GeoPoint[], fallback: GeoPoint): Camera {
  const points = input.filter(valid);
  if (!points.length) points.push(valid(fallback) ? fallback : { lat: 0, lng: 0 });
  const latitudes = points.map((point) => point.lat);
  const longitudes = points.map((point) => wrap(point.lng)).sort((a, b) => a - b);
  let start = longitudes[0];
  let largestGap = -1;
  for (let index = 0; index < longitudes.length; index++) {
    const next = longitudes[(index + 1) % longitudes.length];
    const gap = next + (index === longitudes.length - 1 ? 360 : 0) - longitudes[index];
    if (gap > largestGap) {
      largestGap = gap;
      start = next;
    }
  }
  // The largest empty longitude arc lies outside the network. Its complement
  // keeps 179°E and 179°W together instead of centering them at Greenwich.
  const center = {
    lat: (Math.min(...latitudes) + Math.max(...latitudes)) / 2,
    lng: wrap(start + (360 - largestGap) / 2 + 180) - 180,
    altitude: 0,
  };
  const radius = Math.max(...points.map((point) => distance(center, point)));
  return {
    center,
    // Leave geographic margin for labels and the oblique camera. Large routes
    // must not inherit the old fixed 330 km view; global extents use a globe view.
    range: Math.max(330_000, Math.min(30_000_000, radius * 3.6)),
    tilt: 25,
    heading: 0,
  };
}
