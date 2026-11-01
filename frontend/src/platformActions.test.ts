import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, TransportError } from './api';
import {
  platformCommandScope,
  platformScreens,
  platformViews,
  validatePlatformAction,
} from './platformActions';
import type { World } from './types';

const world = {
  scenario_id: 'scenario',
  version: 7,
  mode: 'live',
  airports: [{ id: 'LHR' }],
  resources: [{ id: 'FLIGHT-1' }],
  groups: [
    { id: 'G1', incident_id: 'CASE1' },
    { id: 'G2', incident_id: 'CASE2' },
  ],
  incidents: [
    { id: 'CASE1', group_ids: ['G1'] },
    { id: 'CASE2', group_ids: ['G2'] },
  ],
  observations: [{ fr24_id: 'OBS1' }],
} as World;
const action = (name: string, args: Record<string, unknown> = {}) => ({ name, arguments: args });
afterEach(() => vi.unstubAllGlobals());

describe('application action boundary', () => {
  it.each(platformViews)('accepts the explicit %s view', (view) => {
    expect(
      validatePlatformAction(action('ui.navigate', { view }), world, {}).arguments,
    ).toMatchObject({ view });
  });
  it.each(platformScreens)('supports the %s screen in its allowed operation mode', (screen) => {
    const state = ['scenario', 'new-scenario', 'reset'].includes(screen)
      ? { ...world, mode: 'simulation' as const }
      : { ...world, demo: true };
    expect(
      validatePlatformAction(action('ui.open', { screen }), state, {}).arguments,
    ).toMatchObject({ screen });
  });
  it.each(['LHR', 'FLIGHT-1', 'G1', 'OBS1'])(
    'resolves existing selectable target %s',
    (selected_id) => {
      expect(
        validatePlatformAction(action('ui.navigate', { view: 'map', selected_id }), world, {})
          .arguments,
      ).toMatchObject({ selected_id });
    },
  );
  it.each([
    action('window.open', { url: 'https://example.invalid' }),
    action('ui.navigate', { view: 'https://example.invalid' }),
    action('ui.navigate', { view: 'map', url: 'javascript:alert(1)' }),
    action('ui.open', { screen: 'settings', selector: '.delete-all' }),
    action('ui.refresh', { path: '/admin' }),
    action('ui.export', { script: 'alert(1)' }),
    action('ui.close', { anything: true }),
    action('ui.navigate', { view: 'map', selected_id: 'DELETED' }),
    action('ui.open', { screen: 'recovery-case', incident_id: 'DELETED' }),
    action('ui.open', { screen: 'recovery-case', incident_id: 'CASE1', group_id: 'G2' }),
    action('ui.open', { screen: 'settings', tab: 'search' }),
    action('ui.theme', { value: 'system' }),
    action('ui.timeline', { action: 'execute' }),
    null,
    [],
    { name: 'ui.close', arguments: [] },
  ])('rejects arbitrary operations and stale or contradictory context: %j', (value) => {
    expect(() => validatePlatformAction(value, world, {})).toThrow();
  });
  it('enforces mode, demo, and planner capability guards before UI effects', () => {
    for (const screen of ['scenario', 'new-scenario', 'reset', 'demo-setup'])
      expect(() => validatePlatformAction(action('ui.open', { screen }), world, {})).toThrow();
    expect(() =>
      validatePlatformAction(
        action('ui.navigate', { view: 'map', map_style: 'architectural' }),
        world,
        {},
      ),
    ).toThrow();
    expect(() =>
      validatePlatformAction(action('ui.planner', { value: 'ai' }), world, {}),
    ).toThrow();
    expect(
      validatePlatformAction(action('ui.planner', { value: 'ai' }), world, { ai_configured: true })
        .name,
    ).toBe('ui.planner');
    for (const tab of ['passengers', 'transport', 'confirm'])
      expect(() =>
        validatePlatformAction(
          action('ui.open', { screen: 'live-data', tab }),
          { ...world, demo: true },
          {},
        ),
      ).toThrow();
  });
  it('preserves exact tab, query, section and timeline intents while accepting absent optional fields', () => {
    expect(
      validatePlatformAction(
        action('ui.open', {
          screen: 'live-data',
          tab: 'planning_transfer',
          incident_id: 'CASE1',
          group_id: 'G1',
          query: null,
        }),
        world,
        {},
      ).arguments,
    ).toMatchObject({ tab: 'planning_transfer', incident_id: 'CASE1', group_id: 'G1' });
    expect(
      validatePlatformAction(
        action('ui.navigate', { view: 'resources', tab: 'hotel', query: 'Hilton' }),
        world,
        {},
      ).arguments,
    ).toMatchObject({ tab: 'hotel', query: 'Hilton' });
    expect(validatePlatformAction(action('ui.timeline', { action: 'zoom_in' }), world, {})).toEqual(
      action('ui.timeline', { action: 'zoom_in' }),
    );
  });
});

describe('current application command context', () => {
  it('follows a changed selection, filters stale IDs and respects the open case', () => {
    expect(platformCommandScope(world, 'G1', null, false)).toEqual({
      incident_id: 'CASE1',
      group_id: 'G1',
      selected_id: 'G1',
    });
    expect(platformCommandScope(world, 'G2', 'CASE1', false)).toEqual({
      incident_id: 'CASE2',
      group_id: 'G2',
      selected_id: 'G2',
    });
    expect(platformCommandScope(world, 'G2', 'CASE1', true)).toEqual({
      incident_id: 'CASE1',
      selected_id: 'G2',
    });
    expect(platformCommandScope(world, 'deleted', 'deleted', true)).toEqual({});
    expect(platformCommandScope(world, 'FLIGHT-1', null, false)).toEqual({
      selected_id: 'FLIGHT-1',
    });
    expect(platformCommandScope({ ...world, mode: 'simulation' }, 'G1', null, false)).toEqual({
      group_id: 'G1',
      selected_id: 'G1',
    });
  });
  it('sends scope with typed commands and preserves its original snapshot during transport recovery', async () => {
    const fetch = vi
      .fn()
      .mockRejectedValueOnce(new Error('lost response'))
      .mockResolvedValueOnce(new Response(JSON.stringify({ world, applied: false })));
    vi.stubGlobal('fetch', fetch);
    const scope = platformCommandScope(world, 'G1', null, false);
    let failure: unknown;
    try {
      await api.command(world, 'Open its recovery case', 'prior context', scope);
    } catch (cause) {
      failure = cause;
    }
    expect(failure).toBeInstanceOf(TransportError);
    scope.group_id = 'G2';
    await (failure as TransportError).retry();
    const original = JSON.parse(fetch.mock.calls[0][1].body);
    expect(original).toMatchObject({
      expected_version: 7,
      scope: { group_id: 'G1', incident_id: 'CASE1', selected_id: 'G1' },
    });
    expect(fetch.mock.calls[1][1].body).toBe(fetch.mock.calls[0][1].body);
  });
});
