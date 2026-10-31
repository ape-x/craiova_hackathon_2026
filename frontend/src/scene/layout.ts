/** Authored simulation layout in meters: +X east, +Z south. Not a survey. */
export const gates: [number, number, number][] = [
  [-73, 0, -48],
  [2, 0, -48],
  [77, 0, -48],
];
export const busSpots: [number, number, number][] = [
  [-38, 0, 126],
  [8, 0, 139],
  [57, 0, 133],
  [112, 0, 163],
  [-89, 0, 152],
  [-143, 0, 166],
];
export const groupPosition = (index: number): [number, number, number] => [
  -85 + (index % 13) * 5.5,
  0.2,
  155 + Math.floor(index / 13) * 4.5,
];
