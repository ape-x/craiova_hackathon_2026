import type { Airport } from './types';

/** Keep the rendered option and submitted destination in sync after a live airport closure. */
export function diversionChoice(airports: Airport[], requestedId: string) {
  const eligible = airports.filter((airport) => airport.available && airport.diversion_eligible);
  return {
    airports: eligible,
    selectedId: eligible.some((airport) => airport.id === requestedId)
      ? requestedId
      : (eligible[0]?.id ?? ''),
  };
}
