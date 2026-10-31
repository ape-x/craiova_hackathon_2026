import type { Airport } from './types';

/** Older airport catalogs omit kind; supplier properties and stops are never airports. */
export const isAirportNode = (node: Airport): boolean =>
  node.kind === undefined || node.kind === 'airport';

export const locationKindLabel = (node: Airport): string =>
  node.kind === 'hotel' ? 'Hotel property' : node.kind === 'station' ? 'Coach station' : 'Airport';
