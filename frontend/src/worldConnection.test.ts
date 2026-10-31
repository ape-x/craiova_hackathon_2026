import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError } from './api';
import { startWorldConnection } from './worldConnection';
import type { AppConfig, World } from './types';

class FakeEventSource extends EventTarget {
  static instances: FakeEventSource[] = [];
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  close = vi.fn();
  constructor() {
    super();
    FakeEventSource.instances.push(this);
  }
  open() {
    this.onopen?.(new Event('open'));
  }
  fail() {
    this.onerror?.(new Event('error'));
  }
}

const world = (version: number) => ({ scenario_id: 'scenario-test', version }) as World;
const configuration = (enabled: boolean): AppConfig => ({
  ai_configured: enabled,
  voice_configured: enabled,
});
function callbacks() {
  return {
    onConfig: vi.fn(),
    onWorld: vi.fn(),
    onConnection: vi.fn(),
    onError: vi.fn(),
    onAuthRequired: vi.fn(),
  };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}
const flush = () => vi.advanceTimersByTimeAsync(0);

beforeEach(() => {
  vi.useFakeTimers();
  FakeEventSource.instances = [];
  vi.stubGlobal('EventSource', FakeEventSource);
  vi.stubGlobal('document', { hidden: false });
  vi.spyOn(api, 'config').mockResolvedValue(configuration(false));
  vi.spyOn(api, 'world').mockResolvedValue(world(1));
});
afterEach(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('runtime configuration follows the live server', () => {
  it('updates AI and voice flags after reconnecting to a configured server', async () => {
    const events = callbacks();
    const connection = startWorldConnection(events);
    await flush();
    expect(events.onConfig).toHaveBeenLastCalledWith(configuration(false));
    const stream = FakeEventSource.instances[0];
    stream.open();
    await flush();
    stream.fail();
    vi.mocked(api.config).mockResolvedValue(configuration(true));
    vi.mocked(api.world).mockResolvedValue(world(2));
    stream.open();
    await flush();
    expect(events.onConnection).toHaveBeenLastCalledWith('connected');
    expect(events.onConfig).toHaveBeenLastCalledWith(configuration(true));
    expect(events.onWorld).toHaveBeenLastCalledWith(world(2));
    connection.close();
  });

  it('does not retain enabled provider flags after the replacement server disables them', async () => {
    vi.mocked(api.config).mockResolvedValue(configuration(true));
    const events = callbacks();
    const connection = startWorldConnection(events);
    await flush();
    vi.mocked(api.config).mockResolvedValue(configuration(false));
    await connection.refresh();
    expect(events.onConfig).toHaveBeenLastCalledWith(configuration(false));
    connection.close();
  });

  it('refreshes flags during HTTP polling when the SSE stream cannot reconnect', async () => {
    const events = callbacks();
    const connection = startWorldConnection(events);
    await flush();
    FakeEventSource.instances[0].fail();
    vi.mocked(api.config).mockResolvedValue(configuration(true));
    await vi.advanceTimersByTimeAsync(15000);
    expect(events.onConfig).toHaveBeenLastCalledWith(configuration(true));
    expect(api.config).toHaveBeenCalledTimes(2);
    connection.close();
  });

  it('coalesces stream reopen, polling and manual refresh into one request pair', async () => {
    const events = callbacks();
    const connection = startWorldConnection(events);
    await flush();
    const configResult = deferred<AppConfig>();
    const worldResult = deferred<World>();
    vi.mocked(api.config).mockReturnValue(configResult.promise);
    vi.mocked(api.world).mockReturnValue(worldResult.promise);
    FakeEventSource.instances[0].open();
    const first = connection.refresh();
    const second = connection.refresh();
    expect(first).toBe(second);
    await vi.advanceTimersByTimeAsync(45000);
    expect(api.config).toHaveBeenCalledTimes(2);
    expect(api.world).toHaveBeenCalledTimes(2);
    configResult.resolve(configuration(true));
    worldResult.resolve(world(2));
    await expect(first).resolves.toEqual(world(2));
    expect(events.onConfig).toHaveBeenLastCalledWith(configuration(true));
    connection.close();
  });

  it('ignores late results and stream events after close and aborts both reads', async () => {
    const events = callbacks();
    const connection = startWorldConnection(events);
    await flush();
    const stream = FakeEventSource.instances[0];
    const configResult = deferred<AppConfig>();
    const worldResult = deferred<World>();
    vi.mocked(api.config).mockReturnValue(configResult.promise);
    vi.mocked(api.world).mockReturnValue(worldResult.promise);
    const pending = connection.refresh();
    const check = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    const signal = vi.mocked(api.config).mock.calls.at(-1)![0]!;
    connection.close();
    expect(signal.aborted).toBe(true);
    expect(stream.close).toHaveBeenCalledOnce();
    configResult.resolve(configuration(true));
    worldResult.resolve(world(3));
    stream.dispatchEvent(new MessageEvent('world', { data: JSON.stringify(world(4)) }));
    await check;
    await vi.advanceTimersByTimeAsync(45000);
    expect(events.onConfig).toHaveBeenCalledTimes(1);
    expect(events.onWorld).toHaveBeenCalledTimes(1);
    expect(api.config).toHaveBeenCalledTimes(2);
    await expect(connection.refresh()).rejects.toMatchObject({ name: 'AbortError' });
  });

  it('keeps fresh world data when configuration temporarily fails, then recovers configuration', async () => {
    const events = callbacks();
    const connection = startWorldConnection(events);
    await flush();
    vi.mocked(api.config).mockRejectedValueOnce(new Error('temporary config failure'));
    vi.mocked(api.world).mockResolvedValue(world(2));
    await expect(connection.refresh()).resolves.toEqual(world(2));
    expect(events.onWorld).toHaveBeenLastCalledWith(world(2));
    expect(events.onConfig).toHaveBeenCalledTimes(1);
    vi.mocked(api.config).mockResolvedValue(configuration(true));
    await connection.refresh();
    expect(events.onConfig).toHaveBeenLastCalledWith(configuration(true));
    connection.close();
  });

  it('retains authentication handling when manual refresh discovers an expired login', async () => {
    const events = callbacks();
    const connection = startWorldConnection(events);
    await flush();
    vi.mocked(api.world).mockRejectedValue(new ApiError(401, 'Sign in again'));
    await expect(connection.refresh()).rejects.toMatchObject({ status: 401 });
    expect(events.onAuthRequired).toHaveBeenLastCalledWith(true);
    expect(FakeEventSource.instances[0].close).toHaveBeenCalledOnce();
    connection.close();
  });

  it('does not poll hidden tabs and reports initial HTTP failures without opening a stream', async () => {
    vi.stubGlobal('document', { hidden: true });
    const events = callbacks();
    const connection = startWorldConnection(events);
    await flush();
    await vi.advanceTimersByTimeAsync(45000);
    expect(api.config).toHaveBeenCalledTimes(1);
    connection.close();
    vi.mocked(api.world).mockRejectedValue(new Error('Server unavailable'));
    const failed = startWorldConnection(events);
    await flush();
    expect(events.onConnection).toHaveBeenLastCalledWith('offline');
    expect(events.onError).toHaveBeenLastCalledWith('Server unavailable');
    expect(FakeEventSource.instances).toHaveLength(1);
    failed.close();
  });
});
