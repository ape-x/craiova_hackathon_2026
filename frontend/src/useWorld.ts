import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from './api';
import type { AppConfig, World } from './types';

export function useWorld() {
  const [world, setWorld] = useState<World | null>(null);
  const [config, setConfig] = useState<AppConfig>({});
  const [connection, setConnection] = useState<
    'connecting' | 'connected' | 'reconnecting' | 'offline'
  >('connecting');
  const [error, setError] = useState<string | null>(null);
  const [authRequired, setAuthRequired] = useState(false);
  const [generation, setGeneration] = useState(0);
  const worldRef = useRef(world);
  const receive = useCallback((next: World) => {
    // Versions are monotonic across resets as well as scenario mutations.
    if (worldRef.current && worldRef.current.version > next.version) return;
    worldRef.current = next;
    setWorld(next);
  }, []);
  const refresh = useCallback(async () => {
    const next = await api.world();
    receive(next);
    return next;
  }, [receive]);
  useEffect(() => {
    let stream: EventSource | undefined;
    let stopped = false;
    let poll: ReturnType<typeof setInterval> | undefined;
    const controller = new AbortController();
    Promise.all([api.config(controller.signal), api.world(controller.signal)])
      .then(([configuration, next]) => {
        if (stopped) return;
        setConfig(configuration);
        receive(next);
        setError(null);
        setAuthRequired(false);
        stream = new EventSource('/api/stream', { withCredentials: true });
        stream.onopen = () => {
          if (!stopped) setConnection('connected');
        };
        const onWorld = (event: MessageEvent) => {
          if (stopped) return;
          try {
            const data = JSON.parse(event.data);
            if (data.world) receive(data.world);
            else if (data.scenario_id) receive(data);
          } catch {
            /* Transport will reconnect on interrupted frames. */
          }
        };
        stream.onmessage = onWorld;
        stream.addEventListener('world', onWorld);
        stream.onerror = () => {
          if (!stopped) setConnection('reconnecting');
        };
        // Refresh covers offer expiry and resynchronizes after dropped SSE frames.
        poll = setInterval(() => {
          if (!document.hidden)
            api
              .world(controller.signal)
              .then((next) => {
                if (!stopped) receive(next);
              })
              .catch((cause: unknown) => {
                if (!stopped && cause instanceof ApiError && cause.status === 401) {
                  setAuthRequired(true);
                  stream?.close();
                }
              });
        }, 15000);
      })
      .catch((cause: unknown) => {
        if (stopped) return;
        setConnection('offline');
        if (cause instanceof ApiError && cause.status === 401) setAuthRequired(true);
        else setError(cause instanceof Error ? cause.message : 'Connection failed');
      });
    return () => {
      stopped = true;
      controller.abort();
      stream?.close();
      if (poll) clearInterval(poll);
    };
  }, [generation, receive]);
  return {
    world,
    worldRef,
    config,
    receive,
    refresh,
    connection,
    error,
    authRequired,
    reconnect: () => setGeneration((value) => value + 1),
  };
}
