import { useEffect } from 'react';
import type { Group } from 'three';
import { disposeGroup } from './geometry';

const retainCounts = new WeakMap<Group, number>();
export function useSceneAsset(group: Group) {
  useEffect(() => {
    retainCounts.set(group, (retainCounts.get(group) || 0) + 1);
    return () => {
      retainCounts.set(group, (retainCounts.get(group) || 1) - 1);
      // StrictMode reattaches the same asset before this microtask runs.
      queueMicrotask(() => {
        if (retainCounts.get(group) === 0) {
          disposeGroup(group);
          retainCounts.delete(group);
        }
      });
    };
  }, [group]);
}
