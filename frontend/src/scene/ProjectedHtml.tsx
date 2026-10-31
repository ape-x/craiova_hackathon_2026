import { createContext, useContext, useEffect, useId, useRef, useSyncExternalStore } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { useFrame, useThree } from '@react-three/fiber';
import * as THREE from 'three';

interface Label {
  id: string;
  children: ReactNode;
  style: CSSProperties;
}
export function createLabelStore() {
  const listeners = new Set<() => void>();
  const records = new Map<string, Label>();
  let snapshot: Label[] = [];
  return {
    elements: new Map<string, HTMLDivElement>(),
    subscribe: (fn: () => void) => {
      listeners.add(fn);
      return () => {
        listeners.delete(fn);
      };
    },
    getSnapshot: () => snapshot,
    update: (id: string, label: Label | null) => {
      if (label) records.set(id, label);
      else records.delete(id);
      snapshot = Array.from(records.values());
      listeners.forEach((fn) => fn());
    },
  };
}
type LabelStore = ReturnType<typeof createLabelStore>;
export const LabelLayer = createContext<LabelStore | null>(null);
export function LabelOverlay({ store }: { store: LabelStore }) {
  const labels = useSyncExternalStore(store.subscribe, store.getSnapshot);
  return (
    <div className="scene-label-layer">
      {labels.map((label) => (
        <div
          key={label.id}
          className="scene-projected-label"
          style={label.style}
          ref={(el) => {
            if (el) store.elements.set(label.id, el);
            else store.elements.delete(label.id);
          }}
        >
          {label.children}
        </div>
      ))}
    </div>
  );
}

// A shared React portal avoids a separate React root for every 3D label.
export default function ProjectedHtml({
  position,
  children,
  zIndexRange = [10, 0],
  style,
}: {
  position: [number, number, number];
  children: ReactNode;
  center?: boolean;
  distanceFactor?: number;
  zIndexRange?: [number, number];
  style?: CSSProperties;
}) {
  const store = useContext(LabelLayer),
    anchor = useRef<THREE.Group>(null),
    id = useId();
  const point = useRef(new THREE.Vector3());
  const invalidate = useThree((s) => s.invalidate);
  useEffect(() => {
    store?.update(id, { id, children, style: { ...style, zIndex: zIndexRange[0] } });
    invalidate();
    return () => store?.update(id, null);
  }, [store, id, children, zIndexRange[0], invalidate]);
  useFrame(({ camera, size }) => {
    const element = store?.elements.get(id);
    if (!anchor.current || !element) return;
    anchor.current.getWorldPosition(point.current);
    point.current.project(camera);
    const p = point.current;
    const visible = p.z > -1 && p.z < 1 && Math.abs(p.x) < 1.2 && Math.abs(p.y) < 1.2;
    element.style.display = visible ? 'block' : 'none';
    element.style.transform = `translate(${(p.x * 0.5 + 0.5) * size.width}px,${(-p.y * 0.5 + 0.5) * size.height}px) translate(-50%, -100%)`;
  });
  return <group ref={anchor} position={position} />;
}
