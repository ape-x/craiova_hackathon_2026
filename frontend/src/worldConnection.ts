import { api, ApiError } from './api';
import type { AppConfig, World } from './types';

export type ConnectionState = 'connecting' | 'connected' | 'reconnecting' | 'offline';

interface Callbacks {
  onConfig: (configuration: AppConfig) => void;
  onWorld: (world: World) => void;
  onConnection: (state: ConnectionState) => void;
  onError: (message: string | null) => void;
  onAuthRequired: (required: boolean) => void;
}

/** One transport owner per mounted hook. Runtime provider flags must follow a
 * restarted server just as the simulation does; they are not startup constants. */
export function startWorldConnection(callbacks: Callbacks) {
  const controller = new AbortController();
  let stopped = false;
  let stream: EventSource | undefined;
  let poll: ReturnType<typeof setInterval> | undefined;
  let pending: Promise<World> | undefined;

  const refresh = (): Promise<World> => {
    if (stopped) return Promise.reject(new DOMException('Connection closed', 'AbortError'));
    if (pending) return pending;
    // Poll, stream reconnect and manual refresh share these two bounded reads.
    pending = Promise.allSettled([api.config(controller.signal), api.world(controller.signal)])
      .then(([configuration, snapshot]) => {
        if (stopped) throw new DOMException('Connection closed', 'AbortError');
        if (configuration.status === 'fulfilled') callbacks.onConfig(configuration.value);
        // A temporary /config failure must not discard a valid world snapshot.
        if (snapshot.status === 'rejected') {
          if (snapshot.reason instanceof ApiError && snapshot.reason.status === 401) {
            callbacks.onAuthRequired(true);
            stream?.close();
          }
          throw snapshot.reason;
        }
        callbacks.onWorld(snapshot.value);
        callbacks.onError(null);
        callbacks.onAuthRequired(false);
        return snapshot.value;
      })
      .finally(() => {
        pending = undefined;
      });
    return pending;
  };

  const onWorld = (event: MessageEvent) => {
    if (stopped) return;
    try {
      const data = JSON.parse(event.data);
      if (data.world) callbacks.onWorld(data.world);
      else if (data.scenario_id) callbacks.onWorld(data);
    } catch {
      /* Transport reconnects on interrupted frames. */
    }
  };

  callbacks.onConnection('connecting');
  void refresh()
    .then(() => {
      if (stopped) return;
      stream = new EventSource('/api/stream', { withCredentials: true });
      stream.onopen = () => {
        if (stopped) return;
        callbacks.onConnection('connected');
        // Includes the first open: the server may have restarted between the
        // initial HTTP read and establishing the stream.
        void refresh().catch(() => undefined);
      };
      stream.onmessage = onWorld;
      stream.addEventListener('world', onWorld);
      stream.onerror = () => {
        if (!stopped) callbacks.onConnection('reconnecting');
      };
      // Also refresh config when SSE cannot reconnect but HTTP polling works.
      poll = setInterval(() => {
        if (!document.hidden) void refresh().catch(() => undefined);
      }, 15000);
    })
    .catch((cause: unknown) => {
      if (stopped) return;
      callbacks.onConnection('offline');
      if (!(cause instanceof ApiError && cause.status === 401))
        callbacks.onError(cause instanceof Error ? cause.message : 'Connection failed');
    });

  return {
    refresh,
    close() {
      stopped = true;
      controller.abort();
      if (poll) clearInterval(poll);
      if (stream) {
        stream.onopen = stream.onmessage = stream.onerror = null;
        stream.removeEventListener('world', onWorld);
        stream.close();
      }
    },
  };
}
