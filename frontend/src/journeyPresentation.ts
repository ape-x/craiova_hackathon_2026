import type { Offer, Resource } from './types';

interface SegmentTime {
  instant: number;
  label: string;
}
export interface PresentedFlightSegment {
  origin: string;
  destination: string;
  departure: SegmentTime;
  arrival: SegmentTime;
  carrier: string | null;
  flightNumber: string | null;
  technicalStops: number | null;
  waitBeforeMinutes: number;
}
const datedTime = new Intl.DateTimeFormat('en-GB', {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  timeZone: 'UTC',
  hourCycle: 'h23',
});
function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}
function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}
function segmentTime(value: unknown, local: unknown, airport: string): SegmentTime | null {
  if (typeof value !== 'string' || !/(Z|[+-]\d{2}:\d{2})$/.test(value)) return null;
  const instant = Date.parse(value);
  if (!Number.isFinite(instant)) return null;
  // Nuitee supplies both the absolute instant and the airport-local wall clock.
  // Derive only their stated offset; do not guess an airport's time zone.
  if (typeof local === 'string') {
    const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::(\d{2})(?:\.\d{1,6})?)?$/.exec(local);
    const wall = match ? Date.parse(`${local}Z`) : NaN;
    const canonical = match ? `${match[1]}T${match[2]}:${match[3] ?? '00'}` : '';
    const offset = (wall - instant) / 60000;
    if (
      Number.isFinite(wall) &&
      new Date(wall).toISOString().slice(0, 19) === canonical &&
      Number.isInteger(offset) &&
      Math.abs(offset) <= 14 * 60
    ) {
      const absolute = Math.abs(offset);
      const zone = `UTC${offset < 0 ? '-' : '+'}${String(Math.floor(absolute / 60)).padStart(2, '0')}:${String(absolute % 60).padStart(2, '0')}`;
      return { instant, label: `${datedTime.format(wall)} · local at ${airport} (${zone})` };
    }
  }
  return { instant, label: `${datedTime.format(instant)} UTC · ${airport}` };
}
export function flightSegments(resource: Resource | undefined): PresentedFlightSegment[] {
  const raw = resource?.kind === 'flight' ? resource.provenance?.segments : null;
  if (!resource || !Array.isArray(raw) || raw.length === 0 || raw.length > 10) return [];
  const result: PresentedFlightSegment[] = [];
  for (const value of raw) {
    const segment = record(value);
    if (!segment || segment.mode !== 'flight') return [];
    const origin = text(segment.origin),
      destination = text(segment.destination);
    if (!origin || !destination || origin === destination) return [];
    const departure = segmentTime(segment.departure_at, segment.departure_local, origin);
    const arrival = segmentTime(segment.arrival_at, segment.arrival_local, destination);
    const previous = result.at(-1);
    if (
      !departure ||
      !arrival ||
      arrival.instant <= departure.instant ||
      (previous &&
        (previous.destination !== origin || previous.arrival.instant > departure.instant))
    )
      return [];
    result.push({
      origin,
      destination,
      departure,
      arrival,
      carrier: text(segment.carrier),
      flightNumber: text(segment.flight_number),
      technicalStops:
        Number.isInteger(segment.technical_stop_count) && Number(segment.technical_stop_count) >= 0
          ? Number(segment.technical_stop_count)
          : null,
      waitBeforeMinutes: previous ? (departure.instant - previous.arrival.instant) / 60000 : 0,
    });
  }
  return result[0].origin === resource.origin && result.at(-1)?.destination === resource.destination
    ? result
    : [];
}
export function waitingTime(minutes: number): string {
  const whole = Math.ceil(minutes);
  return whole >= 60
    ? `${Math.floor(whole / 60)} h${whole % 60 ? ` ${whole % 60} min` : ''}`
    : `${whole} min`;
}
export function sourcedArrival(offer: Offer, resources: Resource[]): string | null {
  const leg = offer.itinerary.legs.at(-1);
  return (
    flightSegments(resources.find((item) => item.id === leg?.resource_id)).at(-1)?.arrival.label ??
    null
  );
}
