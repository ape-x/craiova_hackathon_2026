import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMapInitialization, overlayFailureMessage } from './initialization';
import type { MapInitializationState } from './initialization';

function mapFixture() {
  const changes: MapInitializationState[] = [];
  const initialization = createMapInitialization((value) => changes.push(value));
  const map = new EventTarget();
  const createOverlay = vi.fn();
  const moveCamera = vi.fn();
  const effects = () =>
    initialization.runIfReady(() => {
      createOverlay();
      moveCamera();
    });
  const steady = (isSteady = true) =>
    map.dispatchEvent(Object.assign(new Event('gmp-steadychange'), { isSteady }));
  return { changes, initialization, map, createOverlay, moveCamera, effects, steady };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
});

describe('Google map initialization ordering without provider requests', () => {
  it('does not construct overlays or move the camera during library load or initial rendering', () => {
    const fixture = mapFixture();
    fixture.effects();
    fixture.initialization.watch(fixture.map);
    fixture.effects();
    fixture.steady(false);
    fixture.effects();
    expect(fixture.createOverlay).not.toHaveBeenCalled();
    expect(fixture.moveCamera).not.toHaveBeenCalled();
    fixture.steady();
    fixture.effects();
    expect(fixture.createOverlay).toHaveBeenCalledOnce();
    expect(fixture.moveCamera).toHaveBeenCalledOnce();
    expect(fixture.initialization.ready).toBe(true);
    fixture.initialization.dispose();
  });

  it('preserves an authentication failure arriving before the libraries finish loading', () => {
    const fixture = mapFixture();
    fixture.initialization.fail('authentication');
    const failure = fixture.initialization.state;
    fixture.initialization.watch(fixture.map);
    fixture.steady();
    fixture.initialization.fail('load');
    fixture.effects();
    expect(fixture.initialization.state).toBe(failure);
    expect(fixture.changes).toHaveLength(1);
    expect(failure.error).toContain('browser console');
    expect(failure.error).toContain('project billing');
    expect(failure.error).not.toContain('BillingNotEnabledMapError');
    expect(fixture.createOverlay).not.toHaveBeenCalled();
    expect(fixture.moveCamera).not.toHaveBeenCalled();
  });

  it('keeps initialization errors terminal despite late ready, loader failure and timeout events', () => {
    const fixture = mapFixture();
    fixture.initialization.watch(fixture.map);
    fixture.map.dispatchEvent(new Event('gmp-error'));
    const failure = fixture.initialization.state;
    fixture.steady();
    fixture.initialization.fail('load');
    vi.advanceTimersByTime(30000);
    fixture.effects();
    expect(fixture.initialization.state).toBe(failure);
    expect(failure.error).toContain('could not initialize');
    expect(fixture.changes.map((change) => change.state)).toEqual(['rendering', 'error']);
    expect(fixture.createOverlay).not.toHaveBeenCalled();
    expect(fixture.moveCamera).not.toHaveBeenCalled();
    fixture.initialization.dispose();
  });

  it('also makes provider failures terminal after a previously ready map', () => {
    const fixture = mapFixture();
    fixture.initialization.watch(fixture.map);
    fixture.steady();
    fixture.effects();
    fixture.initialization.fail('authentication');
    const failure = fixture.initialization.state;
    fixture.steady(false);
    fixture.steady();
    fixture.effects();
    expect(fixture.initialization.state).toBe(failure);
    expect(fixture.initialization.ready).toBe(false);
    expect(fixture.createOverlay).toHaveBeenCalledOnce();
    expect(fixture.moveCamera).toHaveBeenCalledOnce();
    fixture.initialization.dispose();
  });

  it('handles readiness emitted synchronously when the map is attached without arming a later false timeout', () => {
    const fixture = mapFixture();
    const appendMap = () => fixture.steady();
    fixture.initialization.watch(fixture.map);
    appendMap();
    vi.advanceTimersByTime(60000);
    expect(fixture.initialization.state).toEqual({ state: 'ready', error: '' });
    expect(fixture.changes.map((change) => change.state)).toEqual(['rendering', 'ready']);
    expect(vi.getTimerCount()).toBe(0);
    fixture.initialization.dispose();
  });

  it('reports a rendering timeout without asserting a specific account cause and rejects late readiness', () => {
    const fixture = mapFixture();
    fixture.initialization.watch(fixture.map);
    vi.advanceTimersByTime(30000);
    fixture.steady();
    fixture.effects();
    expect(fixture.initialization.state.state).toBe('error');
    expect(fixture.initialization.state.error).toContain('has not confirmed');
    expect(fixture.initialization.state.error).not.toContain('billing is disabled');
    expect(fixture.createOverlay).not.toHaveBeenCalled();
    fixture.initialization.dispose();
  });

  it('detaches events and suppresses late callbacks/effects after unmount', () => {
    const fixture = mapFixture();
    const removed = vi.spyOn(fixture.map, 'removeEventListener');
    fixture.initialization.watch(fixture.map);
    fixture.initialization.dispose();
    const count = fixture.changes.length;
    fixture.steady();
    fixture.map.dispatchEvent(new Event('gmp-error'));
    fixture.initialization.fail('authentication');
    vi.advanceTimersByTime(30000);
    fixture.effects();
    expect(fixture.changes).toHaveLength(count);
    expect(removed).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
    expect(fixture.createOverlay).not.toHaveBeenCalled();
  });

  it('uses only safe local stage/type diagnostics for overlay exceptions', () => {
    const cause = new TypeError('https://maps.example/error?key=private-test-sentinel');
    cause.name = 'private-test-sentinel';
    const message = overlayFailureMessage('create marker pin', cause);
    expect(message).toContain('create marker pin; TypeError');
    expect(message).not.toContain('private-test-sentinel');
    expect(message).not.toContain('https://');
    expect(
      overlayFailureMessage('create route', {
        message: 'private-test-sentinel',
        name: 'BillingNotEnabledMapError',
      }),
    ).toContain('create route; Error');
    expect(
      overlayFailureMessage('create route', new Error('BillingNotEnabledMapError')),
    ).not.toContain('BillingNotEnabledMapError');
  });
});
