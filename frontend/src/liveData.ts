export const MANIFEST_COLUMNS = [
  'passenger_id',
  'group_id',
  'name',
  'origin',
  'destination',
  'original_flight_id',
  'ready_at',
  'deadline',
  'group_budget_eur',
  'accessible',
  'priority',
  'is_adult',
] as const;
export const MANIFEST_TEMPLATE = MANIFEST_COLUMNS.join(',') + '\n';

export function supplierStationRoute(segment: unknown): string | null {
  if (!segment || typeof segment !== 'object') return null;
  const stationName = (side: 'departure_station' | 'arrival_station') => {
    const station = (segment as Record<string, unknown>)[side];
    if (!station || typeof station !== 'object') return null;
    const name = (station as Record<string, unknown>).name;
    return typeof name === 'string' && name.trim() ? name.trim() : null;
  };
  const departure = stationName('departure_station');
  const arrival = stationName('arrival_station');
  return departure && arrival
    ? `${departure} → ${arrival}`
    : departure
      ? `Departure station: ${departure}`
      : arrival
        ? `Arrival station: ${arrival}`
        : null;
}

export function inspectManifest(csv: string): { rowCount: number; error: string | null } {
  const lines = csv.trim().split(/\r?\n/);
  const header = (lines[0] ?? '')
    .replace(/^\uFEFF/, '')
    .split(',')
    .map((value) => value.trim().replace(/^"|"$/g, ''));
  const missing = MANIFEST_COLUMNS.filter((column) => !header.includes(column));
  if (missing.length) return { rowCount: 0, error: `Missing CSV columns: ${missing.join(', ')}` };
  if (lines.length < 2)
    return { rowCount: 0, error: 'Add passenger records below the CSV header.' };
  return { rowCount: lines.length - 1, error: null };
}

export function utcInputToIso(value: string): string {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$/.test(value))
    throw new Error('Enter a complete UTC date and time.');
  const date = new Date(value + 'Z');
  if (!Number.isFinite(date.getTime()) || !date.toISOString().startsWith(value.slice(0, 16)))
    throw new Error('Enter a valid UTC date and time.');
  return date.toISOString();
}
