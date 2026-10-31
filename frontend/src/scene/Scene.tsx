import { Component, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { Canvas, useFrame, useLoader, useThree } from '@react-three/fiber';
import { Line, OrbitControls } from '@react-three/drei';
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib';
import { Compass, Focus, Minus, Plus, RotateCcw } from 'lucide-react';
import * as THREE from 'three';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import type { PassengerGroup, Resource, SceneMode, World } from '../types';
import { busSpots, gates, makeAircraft, makeAirport, makeBus, PALETTE } from './geometry';
import { groupPosition } from './layout';
import Region from './Region';
import Html, { LabelLayer, LabelOverlay, createLabelStore } from './ProjectedHtml';
import { useSceneAsset } from './lifecycle';
import './scene.css';

export interface SceneProps {
  world: World;
  mode: SceneMode;
  selectedId: string | null;
  onSelect: (id: string) => void;
  googleMapsKey?: string;
}
type Vec = [number, number, number];
const TONES = {
  waiting: PALETTE.amber,
  affected: PALETTE.red,
  exception: PALETTE.red,
  offered: PALETTE.amber,
  confirmed: PALETTE.mint,
  scheduled: '#b4c2cd',
};

function Tag({
  id,
  title,
  subtitle,
  color,
  selected,
  onSelect,
  position,
  compact = false,
}: {
  id: string;
  title: string;
  subtitle?: string;
  color: string;
  selected: boolean;
  onSelect: (id: string) => void;
  position: Vec;
  compact?: boolean;
}) {
  return (
    <Html
      position={position}
      center
      zIndexRange={[selected ? 26 : 18, 0]}
      style={{ pointerEvents: 'none' }}
    >
      <button
        className={`scene-tag ${selected ? 'selected' : ''} ${compact ? 'compact' : ''}`}
        style={{ '--tag-color': color, pointerEvents: 'auto' } as CSSProperties}
        onClick={(e) => {
          e.stopPropagation();
          onSelect(id);
        }}
        aria-label={`${title}${subtitle ? `, ${subtitle}` : ''}`}
      >
        <i />
        <strong>{title}</strong>
        {subtitle && <span>{subtitle}</span>}
      </button>
      <span className="scene-tag-stem" style={{ background: color }} />
    </Html>
  );
}

function BuildingSign() {
  const tex = useMemo(() => {
    const c = document.createElement('canvas');
    c.width = 1024;
    c.height = 64;
    const ctx = c.getContext('2d')!;
    ctx.fillStyle = '#d5e5e5';
    ctx.font = '500 35px Arial';
    ctx.textAlign = 'center';
    ctx.fillText('CRAIOVA  /  RECOVERY TERMINAL', 512, 44);
    return new THREE.CanvasTexture(c);
  }, []);
  useEffect(() => () => tex.dispose(), [tex]);
  return (
    <mesh position={[0, 13.5, 77.95]}>
      <planeGeometry args={[98, 6.1]} />
      <meshBasicMaterial map={tex} transparent depthWrite={false} />
    </mesh>
  );
}

function StaticAirport() {
  const materials = useLoader(THREE.TextureLoader, [
    '/scene/aerial_grass_rock.jpg',
    '/scene/concrete_floor_02.jpg',
    '/scene/asphalt_01.jpg',
  ]);
  const invalidate = useThree((s) => s.invalidate);
  const model = useMemo(() => makeAirport(invalidate, materials), [invalidate, materials]);
  useSceneAsset(model);
  return (
    <>
      <primitive object={model} />
      <BuildingSign />
      {[-72, 0, 72].map((x) => (
        <pointLight
          key={x}
          position={[x, 3.7, 88]}
          color="#f5d5a1"
          intensity={55}
          distance={30}
          decay={2}
        />
      ))}
    </>
  );
}

function SelectionRing({ color, radius = 9 }: { color: string; radius?: number }) {
  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.27, 0]}>
        <ringGeometry args={[radius - 0.2, radius, 60]} />
        <meshBasicMaterial color={color} transparent opacity={0.8} depthWrite={false} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.25, 0]}>
        <circleGeometry args={[radius, 60]} />
        <meshBasicMaterial color={color} transparent opacity={0.07} depthWrite={false} />
      </mesh>
    </group>
  );
}

function Plane({
  resource,
  index,
  selected,
  onSelect,
  showLabel = true,
}: {
  resource: Resource;
  index: number;
  selected: boolean;
  onSelect: (id: string) => void;
  showLabel?: boolean;
}) {
  const model = useMemo(() => makeAircraft(index === 1 ? '#54627c' : '#276e83'), [index]);
  useSceneAsset(model);
  const [x, , z] = gates[index];
  const color = resource.available ? '#b2c1ca' : PALETTE.red;
  return (
    <group position={[x, 0, z]}>
      <group
        rotation={[0, Math.PI, 0]}
        onClick={(e) => {
          e.stopPropagation();
          onSelect(resource.id);
        }}
        onPointerOver={() => {
          document.body.style.cursor = 'pointer';
        }}
        onPointerOut={() => {
          document.body.style.cursor = 'auto';
        }}
      >
        <primitive object={model} />
      </group>
      {(selected || !resource.available) && <SelectionRing radius={22} color={color} />}
      {showLabel && (
        <Tag
          id={resource.id}
          title={resource.name}
          subtitle={
            resource.available
              ? `G0${index + 1} · SIM · ${resource.destination}`
              : 'ANULAT · simulare'
          }
          color={color}
          selected={selected}
          onSelect={onSelect}
          position={[0, 14, -5]}
        />
      )}
    </group>
  );
}

function Bus({
  resource,
  index,
  selected,
  onSelect,
  confirmed,
  simTime,
  reduced,
  showLabel = true,
}: {
  resource: Resource;
  index: number;
  selected: boolean;
  onSelect: (id: string) => void;
  confirmed: boolean;
  simTime: number;
  reduced: boolean;
  showLabel?: boolean;
}) {
  const color = !resource.available ? PALETTE.red : confirmed ? PALETTE.mint : '#9abec6';
  const model = useMemo(() => makeBus(color), [color]);
  useSceneAsset(model);
  const group = useRef<THREE.Group>(null);
  const invalidate = useThree((s) => s.invalidate);
  const original = busSpots[index % busSpots.length];
  // A reservation changes route styling; only simulator time advances vehicle position.
  const progressed =
    confirmed && resource.available
      ? THREE.MathUtils.clamp(
          (simTime - resource.departure) / Math.max(1, resource.arrival - resource.departure),
          0,
          1,
        )
      : 0;
  const target = useMemo(
    () =>
      new THREE.Vector3(original[0] + progressed * 160, original[1], original[2] + progressed * 38),
    [original, progressed],
  );
  useEffect(() => {
    invalidate();
  }, [target, invalidate]);
  useFrame((_, delta) => {
    if (!group.current) return;
    if (group.current.position.distanceTo(target) > 0.02) {
      group.current.position.lerp(target, reduced ? 1 : 1 - Math.exp(-delta * 4));
      invalidate();
    }
  });
  return (
    <group ref={group} position={original}>
      <group
        rotation={[0, index % 2 ? -0.6 : -Math.PI / 2, 0]}
        onClick={(e) => {
          e.stopPropagation();
          onSelect(resource.id);
        }}
      >
        <primitive object={model} />
      </group>
      {(selected || !resource.available) && <SelectionRing color={color} radius={11} />}
      {!resource.available && (
        <mesh position={[0, 0.18, 0]} rotation={[-Math.PI / 2, 0, 0]}>
          <circleGeometry args={[15, 48]} />
          <meshBasicMaterial color={color} transparent opacity={0.08} depthWrite={false} />
        </mesh>
      )}
      {showLabel && (
        <Tag
          id={resource.id}
          title={resource.id}
          subtitle={
            !resource.available
              ? 'INDISPONIBIL'
              : selected
                ? `${resource.capacity} locuri · ${resource.destination}`
                : undefined
          }
          color={color}
          selected={selected}
          onSelect={onSelect}
          position={[0, 9, 0]}
          compact={!selected && resource.available}
        />
      )}
    </group>
  );
}

function PassengerCluster({
  group,
  index,
  selected,
  onSelect,
}: {
  group: PassengerGroup;
  index: number;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const position = groupPosition(index);
  const color = TONES[group.status] || PALETTE.amber;
  return (
    <group
      position={position}
      onClick={(e) => {
        e.stopPropagation();
        onSelect(group.id);
      }}
    >
      {Array.from({ length: Math.min(group.size, 5) }, (_, i) => (
        <group key={i} position={[(i % 3) * 1.4, 0, Math.floor(i / 3) * 1.5]}>
          <mesh position={[0, 1.36, 0]} castShadow>
            <sphereGeometry args={[0.26, 8, 6]} />
            <meshStandardMaterial color="#c3b9a8" roughness={0.9} />
          </mesh>
          <mesh position={[0, 0.82, 0]} castShadow>
            <capsuleGeometry args={[0.23, 0.57, 3, 7]} />
            <meshStandardMaterial color={selected ? color : i % 2 ? '#a5b8be' : '#506b79'} />
          </mesh>
          <mesh position={[-0.13, 0.22, 0]}>
            <boxGeometry args={[0.16, 0.45, 0.2]} />
            <meshStandardMaterial color="#263c49" />
          </mesh>
          <mesh position={[0.13, 0.22, 0]}>
            <boxGeometry args={[0.16, 0.45, 0.2]} />
            <meshStandardMaterial color="#263c49" />
          </mesh>
          <mesh position={[0.52, 0.32, 0.1]} castShadow>
            <boxGeometry args={[0.33, 0.54, 0.26]} />
            <meshStandardMaterial color="#7d7a67" />
          </mesh>
        </group>
      ))}
      {selected && (
        <>
          <SelectionRing color={color} radius={5.5} />
          <Tag
            id={group.id}
            title={group.name}
            subtitle={`${group.size} persoane · ${group.accessible ? 'accesibilitate solicitată' : 'grup indivizibil'}`}
            color={color}
            selected
            onSelect={onSelect}
            position={[2, 6, 0]}
          />
        </>
      )}
    </group>
  );
}

function AirportWorld({
  world,
  mode,
  selectedId,
  onSelect,
  reduced,
}: SceneProps & { reduced: boolean }) {
  const buses = world.resources
    .filter((r) => r.kind === 'bus' && (r.origin === 'CRA' || r.destination === 'CRA'))
    .slice(0, 6);
  const flights = world.resources
    .filter((r) => r.kind === 'flight' && r.origin === 'CRA')
    .slice(0, 3);
  const active = world.offers.filter((o) => o.status === 'held' || o.status === 'confirmed');
  const confirmed = new Set(
    active
      .filter((o) => o.status === 'confirmed')
      .flatMap((o) => o.itinerary.legs.map((l) => l.resource_id)),
  );
  const selectedGroup = world.groups.find((g) => g.id === selectedId);
  const visibleGroups = world.groups.slice(0, 26);
  if (selectedGroup && !visibleGroups.some((g) => g.id === selectedGroup.id))
    visibleGroups[0] = selectedGroup;
  const selectedOffer = active.find((o) => o.group_id === selectedId);
  const routeOffers = selectedOffer ? [selectedOffer] : active;
  const routes = new Map<string, { confirmed: boolean; groupId: string }>();
  for (const offer of routeOffers)
    for (const leg of offer.itinerary.legs) {
      if (
        (leg.kind === 'bus' || leg.kind === 'flight') &&
        (!routes.has(leg.resource_id) || offer.status === 'confirmed')
      )
        routes.set(leg.resource_id, {
          confirmed: offer.status === 'confirmed',
          groupId: offer.group_id,
        });
    }
  return (
    <group>
      <StaticAirport />
      {flights.map((r, i) => (
        <Plane
          key={r.id}
          resource={r}
          index={i}
          selected={selectedId === r.id}
          onSelect={onSelect}
          showLabel={mode !== 'passenger'}
        />
      ))}
      {buses.map((r, i) => (
        <Bus
          key={r.id}
          resource={r}
          index={i}
          selected={selectedId === r.id}
          onSelect={onSelect}
          confirmed={confirmed.has(r.id)}
          simTime={world.sim_time}
          reduced={reduced}
          showLabel={
            mode !== 'passenger' ||
            Boolean(selectedOffer?.itinerary.legs.some((l) => l.resource_id === r.id))
          }
        />
      ))}
      {visibleGroups.map((g, i) => (
        <PassengerCluster
          key={g.id}
          group={g}
          index={i}
          selected={selectedId === g.id}
          onSelect={onSelect}
        />
      ))}
      {Array.from(routes).map(([resourceId, route]) => {
        const bi = buses.findIndex((b) => b.id === resourceId),
          fi = flights.findIndex((f) => f.id === resourceId);
        if (bi < 0 && fi < 0) return null;
        const gi = Math.max(
          0,
          visibleGroups.findIndex((g) => g.id === route.groupId),
        );
        const start = selectedGroup ? groupPosition(gi) : ([0, 0.3, 102] as Vec);
        const end = bi >= 0 ? busSpots[bi] : gates[fi];
        const curve = new THREE.CatmullRomCurve3([
          new THREE.Vector3(start[0], 0.8, start[2]),
          new THREE.Vector3(start[0], 0.8, bi >= 0 ? 115 : 0),
          new THREE.Vector3(end[0], 0.8, bi >= 0 ? 118 : -15),
          new THREE.Vector3(end[0], 0.8, end[2]),
        ]);
        return (
          <Line
            key={resourceId}
            points={curve.getPoints(45)}
            color={route.confirmed ? PALETTE.mint : PALETTE.amber}
            lineWidth={selectedOffer ? 4 : 3.2}
            dashed={!route.confirmed}
            dashSize={2.6}
            gapSize={1.8}
            transparent
            opacity={1}
            depthTest={false}
            renderOrder={5}
          />
        );
      })}
      <Html position={[-160, 35, 29]} center zIndexRange={[5, 0]}>
        <span className="scene-static-label">CONTROL TOWER</span>
      </Html>
      <Html position={[172, 22, 77]} center zIndexRange={[5, 0]}>
        <button className="scene-static-label scene-hotel-label" onClick={() => onSelect('H-CRA')}>
          HOTEL · CRA
        </button>
      </Html>
      <Html position={[260, 0.5, -246]} center zIndexRange={[2, 0]}>
        <span className="scene-runway-label">09 / 27</span>
      </Html>
    </group>
  );
}

function CameraRig({
  mode,
  selectedId,
  world,
  reset,
  reduced,
  zoom,
}: {
  mode: SceneMode;
  selectedId: string | null;
  world: World;
  reset: number;
  reduced: boolean;
  zoom: number;
}) {
  const controls = useRef<OrbitControlsImpl>(null);
  const { camera, invalidate, size } = useThree();
  const animation = useRef<{
    start: THREE.Vector3;
    end: THREE.Vector3;
    fromTarget: THREE.Vector3;
    toTarget: THREE.Vector3;
    startAt: number;
    duration: number;
  } | null>(null);
  const lastZoom = useRef(zoom);
  const lastReset = useRef(reset);
  useEffect(() => {
    const narrow = Math.max(1, 1.4 / (size.width / size.height));
    let target: Vec = [-8, 3, 12],
      position: Vec = [210 * narrow, 205 * narrow, 292 * narrow];
    if (mode === 'region') {
      target = [0, 0, -5];
      position = [25 * narrow, 300 * narrow, 300 * narrow];
    } else if (mode === 'passenger') {
      const index = Math.max(
        0,
        world.groups.findIndex((g) => g.id === selectedId),
      );
      const p = groupPosition(index < 26 ? index : 0);
      target = [p[0], 2, p[2]];
      position = [p[0] + 30, 26, p[2] + 42];
    } else if (reset === lastReset.current && selectedId) {
      const bi = world.resources
        .filter((r) => r.kind === 'bus' && (r.origin === 'CRA' || r.destination === 'CRA'))
        .slice(0, 6)
        .findIndex((r) => r.id === selectedId);
      if (bi >= 0) {
        const p = busSpots[bi];
        target = [p[0], 2, p[2] - 25];
        position = [p[0] + 96, 92, p[2] + 115];
      }
    }
    lastReset.current = reset;
    const now = performance.now();
    animation.current = {
      start: camera.position.clone(),
      end: new THREE.Vector3(...position),
      fromTarget: controls.current?.target.clone() || new THREE.Vector3(0, 0, -30),
      toTarget: new THREE.Vector3(...target),
      startAt: now,
      duration: reduced ? 0 : 1100,
    };
    invalidate();
  }, [mode, selectedId, reset, size.width, size.height, reduced]);
  useEffect(() => {
    if (zoom === lastZoom.current || !controls.current) return;
    const offset = camera.position
      .clone()
      .sub(controls.current.target)
      .multiplyScalar(zoom > lastZoom.current ? 0.8 : 1.25);
    camera.position.copy(controls.current.target).add(offset);
    controls.current.update();
    lastZoom.current = zoom;
    invalidate();
  }, [zoom, camera, invalidate]);
  useFrame(() => {
    const a = animation.current;
    if (!a || !controls.current) return;
    const t = a.duration === 0 ? 1 : Math.min(1, (performance.now() - a.startAt) / a.duration),
      ease = 1 - Math.pow(1 - t, 3);
    camera.position.lerpVectors(a.start, a.end, ease);
    controls.current.target.lerpVectors(a.fromTarget, a.toTarget, ease);
    controls.current.update();
    if (t < 1) invalidate();
    else animation.current = null;
  });
  return (
    <OrbitControls
      ref={controls}
      makeDefault
      enableDamping
      dampingFactor={0.12}
      minDistance={20}
      maxDistance={1500}
      maxPolarAngle={Math.PI * 0.475}
      minPolarAngle={0.12}
      target={[0, 0, -30]}
      onStart={() => {
        animation.current = null;
      }}
    />
  );
}

class SceneBoundary extends Component<{ children: ReactNode }, { error: string | null }> {
  state = { error: null as string | null };
  static getDerivedStateFromError(error: Error) {
    return { error: error.message };
  }
  render() {
    return this.state.error ? (
      <div className="scene-fallback">
        <strong>Scena 3D nu a putut porni</strong>
        <p>{this.state.error}</p>
        <span>
          Verifică accelerarea grafică a browserului. Starea operațională rămâne disponibilă în
          panouri.
        </span>
      </div>
    ) : (
      this.props.children
    );
  }
}

function ArchitecturalEnvironment({ region }: { region: boolean }) {
  const { gl, scene, invalidate } = useThree();
  useEffect(() => {
    const pmrem = new THREE.PMREMGenerator(gl);
    const room = region ? new RoomEnvironment() : null;
    let sky: THREE.CanvasTexture | undefined;
    if (!region) {
      // A local sky/ground reflection field, rather than indoor studio-box reflections.
      const canvas = document.createElement('canvas');
      canvas.width = 1024;
      canvas.height = 512;
      const ctx = canvas.getContext('2d')!;
      const gradient = ctx.createLinearGradient(0, 0, 0, 512);
      for (const [stop, color] of [
        [0, '#304e71'],
        [0.36, '#718b9e'],
        [0.47, '#b1b7b4'],
        [0.5, '#d4c2a6'],
        [0.57, '#4b5345'],
        [1, '#202b23'],
      ] as const)
        gradient.addColorStop(stop, color);
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, 1024, 512);
      const sun = ctx.createRadialGradient(210, 136, 1, 210, 136, 48);
      sun.addColorStop(0, '#ffe9c2');
      sun.addColorStop(0.15, '#e7d4b8c0');
      sun.addColorStop(1, '#e7d4b800');
      ctx.fillStyle = sun;
      ctx.fillRect(160, 86, 100, 100);
      sky = new THREE.CanvasTexture(canvas);
      sky.colorSpace = THREE.SRGBColorSpace;
      sky.mapping = THREE.EquirectangularReflectionMapping;
    }
    const result = room ? pmrem.fromScene(room, 0.04) : pmrem.fromEquirectangular(sky!);
    scene.environment = result.texture;
    scene.environmentIntensity = region ? 0.25 : 0.65;
    gl.toneMappingExposure = region ? 1.2 : 1.1;
    invalidate();
    return () => {
      scene.environment = null;
      result.dispose();
      pmrem.dispose();
      room?.dispose();
      sky?.dispose();
    };
  }, [gl, scene, invalidate, region]);
  return null;
}

export default function Scene(props: SceneProps) {
  const [reset, setReset] = useState(0),
    [zoom, setZoom] = useState(0);
  const labelStore = useMemo(createLabelStore, []);
  const [reduced, setReduced] = useState(
    () => window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  );
  useEffect(() => {
    const q = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setReduced(q.matches);
    q.addEventListener('change', update);
    return () => q.removeEventListener('change', update);
  }, []);
  const region = props.mode === 'region';
  return (
    <div className={`airport-scene ${region ? 'region-scene' : ''}`}>
      <SceneBoundary>
        <Suspense
          fallback={
            <div className="scene-fallback">
              <strong>Construim lumea 3D…</strong>
            </div>
          }
        >
          <Canvas
            frameloop="demand"
            shadows
            dpr={[1, 1.7]}
            camera={{ position: [210, 205, 292], fov: 40, near: 0.5, far: 2500 }}
            gl={{ antialias: true, alpha: false, powerPreference: 'high-performance' }}
            onCreated={({ gl }) => {
              gl.setClearColor('#17262d');
              gl.toneMapping = THREE.ACESFilmicToneMapping;
              gl.toneMappingExposure = 1.2;
              gl.shadowMap.type = THREE.PCFShadowMap;
            }}
          >
            <color attach="background" args={[region ? '#12262d' : '#26353b']} />
            <fog
              attach="fog"
              args={[region ? '#172f34' : '#26353b', region ? 550 : 650, region ? 1000 : 1600]}
            />
            <ambientLight intensity={region ? 0.8 : 0.17} color="#b3c5d0" />
            <hemisphereLight
              args={[
                region ? '#c1d0dc' : '#b5c9e1',
                region ? '#4e5f4e' : '#46503d',
                region ? 1.3 : 1,
              ]}
            />
            <directionalLight
              position={region ? [-170, 280, 180] : [-230, 210, 110]}
              color={region ? '#dbe5ef' : '#ffe5c4'}
              intensity={region ? 2 : 2.45}
              castShadow
              shadow-mapSize={region ? [2048, 2048] : [4096, 4096]}
              shadow-camera-left={region ? -420 : -330}
              shadow-camera-right={region ? 420 : 330}
              shadow-camera-top={region ? 330 : 280}
              shadow-camera-bottom={region ? -330 : -280}
              shadow-camera-near={1}
              shadow-camera-far={1000}
              shadow-bias={region ? -0.0002 : -0.00006}
              shadow-normalBias={region ? 0.3 : 0.06}
            />
            <directionalLight
              position={[100, 100, -100]}
              color="#779cb4"
              intensity={region ? 0.6 : 0.2}
            />
            <ArchitecturalEnvironment region={region} />
            <LabelLayer.Provider value={labelStore}>
              {region ? (
                <Region
                  world={props.world}
                  selectedId={props.selectedId}
                  onSelect={props.onSelect}
                />
              ) : (
                <AirportWorld {...props} reduced={reduced} />
              )}
            </LabelLayer.Provider>
            <CameraRig
              mode={props.mode}
              selectedId={props.selectedId}
              world={props.world}
              reset={reset}
              reduced={reduced}
              zoom={zoom}
            />
          </Canvas>
        </Suspense>
      </SceneBoundary>
      <LabelOverlay store={labelStore} />
      <div className="scene-vignette" />
      <div className="scene-tools" aria-label="Controale cameră">
        <button
          onClick={() => setReset((r) => r + 1)}
          title="Revenire la vedere inițială"
          aria-label="Revenire la vedere inițială"
        >
          <Focus size={19} />
        </button>
        <button onClick={() => setZoom((v) => v + 1)} title="Apropie" aria-label="Apropie">
          <Plus size={19} />
        </button>
        <button onClick={() => setZoom((v) => v - 1)} title="Depărtează" aria-label="Depărtează">
          <Minus size={19} />
        </button>
        <span />
        <button
          onClick={() => setReset((r) => r + 1)}
          title="Resetează camera"
          aria-label="Resetează camera"
        >
          <RotateCcw size={16} />
        </button>
      </div>
      <div className="scene-compass">
        <Compass size={27} strokeWidth={1.2} />
        <b>N</b>
      </div>
    </div>
  );
}
