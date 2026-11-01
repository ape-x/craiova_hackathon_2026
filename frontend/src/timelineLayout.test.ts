import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import Timeline from './Timeline';
import { layoutTimelineLane, TIMELINE_BAR_HEIGHT } from './timelineLayout';
import type { Resource, World } from './types';

const interval = (id: string, departure: number, arrival: number) => ({ id, departure, arrival });

describe('visible timeline interval packing', () => {
  it('separates overlapping intervals and reuses rows when an earlier interval ends', () => {
    const resources = [
      interval('later', 40, 50),
      interval('long', 0, 40),
      interval('overlap', 10, 20),
      interval('touching', 20, 30),
    ];
    const layout = layoutTimelineLane(resources, 0, 100);
    expect(layout.bars.map(({ resource, row }) => [resource.id, row])).toEqual([
      ['long', 0],
      ['overlap', 1],
      ['touching', 1],
      ['later', 0],
    ]);
    expect(layout.height).toBe(46);
    expect(resources.map(({ id }) => id)).toEqual(['later', 'long', 'overlap', 'touching']);
    expect(layout.bars[0].resource).toBe(resources[1]);
  });

  it('gives every simultaneous resource its own row, including more than three resources', () => {
    const resources = Array.from({ length: 10 }, (_, index) => interval(`F${index}`, 10, 90));
    const layout = layoutTimelineLane(resources, 0, 100);
    expect(new Set(layout.bars.map(({ row }) => row)).size).toBe(10);
    expect(layout.height).toBe(206);
    for (let index = 1; index < layout.bars.length; index++) {
      expect(layout.bars[index].top).toBeGreaterThanOrEqual(
        layout.bars[index - 1].top + TIMELINE_BAR_HEIGHT,
      );
    }
    expect(layout.bars.at(-1)!.top + TIMELINE_BAR_HEIGHT).toBeLessThan(layout.height);
  });

  it('clips crossing intervals to the window and does not reserve rows for off-screen intervals', () => {
    const layout = layoutTimelineLane(
      [
        interval('past', 0, 50),
        interval('crosses-start', 20, 75),
        interval('crosses-both', 0, 200),
        interval('crosses-end', 125, 200),
        interval('future', 150, 180),
      ],
      50,
      100,
    );
    expect(
      layout.bars.map(({ resource, row, left, width }) => [resource.id, row, left, width]),
    ).toEqual([
      ['crosses-start', 0, 0, 25],
      ['crosses-both', 1, 0, 100],
      ['crosses-end', 0, 75, 25],
    ]);
    expect(layout.height).toBe(46);
  });

  it('leaves one readable empty row when no interval has duration inside the window', () => {
    const layout = layoutTimelineLane(
      [interval('past', 0, 100), interval('future', 200, 250), interval('instant', 150, 150)],
      100,
      100,
    );
    expect(layout).toEqual({ bars: [], height: 26 });
    expect(layoutTimelineLane([], 100, 100)).toEqual({ bars: [], height: 26 });
  });

  it('recomputes visible geometry when the user moves or zooms the window', () => {
    const resources = [interval('F1', 50, 100), interval('F2', 150, 200)];
    const earlier = layoutTimelineLane(resources, 0, 100);
    const later = layoutTimelineLane(resources, 100, 100);
    const wider = layoutTimelineLane(resources, 0, 200);
    expect(earlier.bars.map(({ resource, left, width }) => [resource.id, left, width])).toEqual([
      ['F1', 50, 50],
    ]);
    expect(later.bars.map(({ resource, left, width }) => [resource.id, left, width])).toEqual([
      ['F2', 50, 50],
    ]);
    expect(
      wider.bars.map(({ resource, row, left, width }) => [resource.id, row, left, width]),
    ).toEqual([
      ['F1', 0, 25, 25],
      ['F2', 0, 75, 25],
    ]);
  });
});

const resource = (
  id: string,
  departure: number,
  arrival: number,
  kind: Resource['kind'] = 'flight',
): Resource => ({
  id,
  name: id,
  kind,
  origin: 'CRA',
  destination: 'OTP',
  departure,
  arrival,
  available: true,
  accessible: true,
  capacity: 20,
  cost_per_person: 0,
  room_capacity: 0,
  vehicle_id: null,
  status: 'scheduled',
  source: 'simulation',
});

const renderTimeline = (resources: Resource[], released: string[] = []) => {
  const world = {
    mode: 'simulation',
    sim_time: 100,
    resources,
    reservations: resources.map(({ id }) => ({
      resource_id: id,
      status: released.includes(id) ? 'released' : 'held',
    })),
  } as World;
  return renderToStaticMarkup(createElement(Timeline, { world, onSelect: () => {} }));
};

describe('timeline rendering', () => {
  it('distinguishes no allocations from allocations outside the current window', () => {
    const outside = renderTimeline([
      resource('F1', 0, 100),
      resource('B1', 820, 900, 'bus'),
      resource('H1', 0, 50, 'hotel'),
    ]);
    expect(outside).toContain('No flights in this time window');
    expect(outside).toContain('No transfers in this time window');
    expect(outside).toContain('No rooms in this time window');
    expect(outside).not.toContain('class="schedule-bar"');

    const empty = renderTimeline([]);
    expect(empty).toContain('No allocated flights');
    expect(empty).toContain('No allocated transfers');
    expect(empty).toContain('No allocated rooms');
  });

  it('keeps label rows aligned with taller lanes and renders distinct readable bars', () => {
    const html = renderTimeline([
      resource('F1', 100, 400),
      resource('F2', 150, 450),
      resource('B1', 200, 350, 'bus'),
    ]);
    expect(html).toContain('grid-template-rows:24px 46px 26px 26px');
    expect(html).toContain('class="timeline-lane flight" style="height:46px"');
    expect(html).toContain('class="timeline-lane bus" style="height:26px"');
    expect(html).toContain('top:4px;height:18px');
    expect(html).toContain('top:24px;height:18px');
    expect(html).toContain('aria-label="Select F2"');
    expect(html).toContain('F2: CRA → OTP, 02:30–07:30');
  });

  it('preserves the available, active-reservation filter and the first-ten-resource limit', () => {
    const unavailable = { ...resource('unavailable', 100, 300), available: false };
    const html = renderTimeline(
      [
        unavailable,
        resource('released', 100, 300),
        ...Array.from({ length: 11 }, (_, index) => resource(`F${index + 1}`, 100, 300)),
      ],
      ['released'],
    );
    expect(html.match(/class="schedule-bar"/g)).toHaveLength(10);
    expect(html).toContain('aria-label="Select F10"');
    expect(html).not.toContain('aria-label="Select F11"');
    expect(html).not.toContain('aria-label="Select unavailable"');
    expect(html).not.toContain('aria-label="Select released"');
  });
});
