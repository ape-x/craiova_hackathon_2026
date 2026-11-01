/** Accept an explicit aware source timestamp without normalizing an impossible calendar date. */
export function sourceInstant(value: unknown): number | null {
  if (typeof value !== 'string') return null;
  const match =
    /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(?::(\d{2})(\.\d{1,6})?)?(Z|[+-]\d{2}:?\d{2})$/.exec(
      value,
    );
  if (!match) return null;
  const calendar = `${match[1]}T${match[2]}:${match[3] ?? '00'}`;
  const wall = Date.parse(`${calendar}${match[4] ?? ''}Z`),
    instant = Date.parse(value);
  return Number.isFinite(wall) &&
    Number.isFinite(instant) &&
    new Date(wall).toISOString().slice(0, 19) === calendar
    ? instant
    : null;
}
