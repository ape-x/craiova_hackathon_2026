import { useCallback, useEffect, useRef, useState } from 'react';
import { startWorldConnection } from './worldConnection';
import type { ConnectionState } from './worldConnection';
import type { AppConfig, World } from './types';

export function useWorld() {
  const [world, setWorld] = useState<World | null>(null);
  const [config, setConfig] = useState<AppConfig>({});
  const [connection, setConnection] = useState<ConnectionState>('connecting');
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
  const transport = useRef<ReturnType<typeof startWorldConnection> | null>(null);
  const refresh = useCallback(() => {
    if (!transport.current)
      return Promise.reject(new DOMException('Connection closed', 'AbortError'));
    return transport.current.refresh();
  }, []);
  useEffect(() => {
    const current = startWorldConnection({
      onConfig: setConfig,
      onWorld: receive,
      onConnection: setConnection,
      onError: setError,
      onAuthRequired: (required) => {
        setAuthRequired(required);
        if (required) {
          worldRef.current = null;
          setWorld(null);
        }
      },
    });
    transport.current = current;
    return () => {
      current.close();
      if (transport.current === current) transport.current = null;
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
