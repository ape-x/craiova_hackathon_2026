import { useEffect, useMemo } from 'react';
import { useLoader } from '@react-three/fiber';
import { Line } from '@react-three/drei';
import * as THREE from 'three';
import type { World } from '../types';
import { makeAircraft, PALETTE } from './geometry';
import Html from './ProjectedHtml';
import { useSceneAsset } from './lifecycle';
import { buildNetworkProjection, inIllustratedRegion, regionalPoint } from './networkProjection';
import { airportWeatherState } from '../weather';

// Real geographic coordinates; the elevation artwork is deliberately illustrative.
export const geoPoint = regionalPoint;
const cities = [
  ['Craiova', 44.3302, 23.7949],
  ['București', 44.4268, 26.1025],
  ['Sibiu', 45.7983, 24.1256],
  ['Pitești', 44.8565, 24.8692],
  ['Râmnicu Vâlcea', 45.0997, 24.3693],
  ['Slatina', 44.4302, 24.3717],
  ['Brașov', 45.6579, 25.6012],
] as const;

function noise(x: number, z: number) {
  const hash = (a: number, b: number) => {
    const f = Math.sin(a * 127.1 + b * 311.7) * 43758.5453;
    return f - Math.floor(f);
  };
  const ix = Math.floor(x),
    iz = Math.floor(z),
    fx = x - ix,
    fz = z - iz,
    u = fx * fx * (3 - 2 * fx),
    v = fz * fz * (3 - 2 * fz);
  return THREE.MathUtils.lerp(
    THREE.MathUtils.lerp(hash(ix, iz), hash(ix + 1, iz), u),
    THREE.MathUtils.lerp(hash(ix, iz + 1), hash(ix + 1, iz + 1), u),
    v,
  );
}
function terrainHeight(x: number, z: number) {
  const ridge = Math.exp(-Math.pow((z + 44 + Math.sin(x * 0.018) * 15) / 24, 2));
  const detail =
    noise(x * 0.065, z * 0.065) * 0.55 +
    noise(x * 0.15, z * 0.15) * 0.28 +
    noise(x * 0.31, z * 0.31) * 0.17;
  return 0.3 + ridge * (4 + detail * 35) + noise(x * 0.025, z * 0.025) * 1.3;
}
function makeTerrain() {
  const geo = new THREE.PlaneGeometry(700, 600, 230, 200);
  geo.rotateX(-Math.PI / 2);
  const p = geo.attributes.position,
    colors = [];
  for (let i = 0; i < p.count; i++) {
    const x = p.getX(i),
      z = p.getZ(i),
      y = terrainHeight(x, z);
    p.setY(i, y);
    const forest = noise(x * 0.04, z * 0.04);
    const c = new THREE.Color().setRGB(
      0.075 + y * 0.006 + forest * 0.035,
      0.12 + y * 0.005 + forest * 0.035,
      0.1 + y * 0.006 + forest * 0.03,
    );
    colors.push(c.r, c.g, c.b);
  }
  geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  geo.computeVertexNormals();
  return geo;
}

function makeContours() {
  const vertices: number[] = [];
  for (const height of [1, 2, 4, 7, 10, 14, 18, 22, 26])
    for (let x = -220; x < 220; x += 3)
      for (let z = -155; z < 140; z += 3) {
        const corners = [
            [x, z],
            [x + 3, z],
            [x + 3, z + 3],
            [x, z + 3],
          ],
          crossings: THREE.Vector3[] = [];
        for (let e = 0; e < 4; e++) {
          const a = corners[e],
            b = corners[(e + 1) % 4],
            ya = terrainHeight(a[0], a[1]),
            yb = terrainHeight(b[0], b[1]);
          if (ya < height === yb < height) continue;
          const t = (height - ya) / (yb - ya);
          crossings.push(
            new THREE.Vector3(
              THREE.MathUtils.lerp(a[0], b[0], t),
              height + 0.18,
              THREE.MathUtils.lerp(a[1], b[1], t),
            ),
          );
        }
        if (crossings.length >= 2)
          vertices.push(...crossings[0].toArray(), ...crossings[1].toArray());
      }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
  return geo;
}

function makeUrbanRoads() {
  const pts: number[] = [];
  for (const [name, lat, lon] of cities) {
    const [x, , z] = geoPoint(lat, lon),
      n = name === 'București' ? 14 : 5;
    for (let i = -n; i <= n; i++) {
      const offset = i * 0.5;
      for (const orientation of [0, 1]) {
        const a = orientation ? [x + offset, z - n * 0.5] : [x - n * 0.5, z + offset],
          b = orientation ? [x + offset, z + n * 0.5] : [x + n * 0.5, z + offset];
        pts.push(
          a[0],
          terrainHeight(a[0], a[1]) + 0.35,
          a[1],
          b[0],
          terrainHeight(b[0], b[1]) + 0.35,
          b[1],
        );
      }
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
  return geo;
}

function regionalCurve(a: [number, number, number], b: [number, number, number], arc = 35) {
  return new THREE.QuadraticBezierCurve3(
    new THREE.Vector3(...a),
    new THREE.Vector3((a[0] + b[0]) / 2, arc, (a[2] + b[2]) / 2),
    new THREE.Vector3(...b),
  ).getPoints(70);
}

export default function Region({
  world,
  selectedId,
  onSelect,
}: {
  world: World;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const terrain = useMemo(makeTerrain, []);
  const contours = useMemo(makeContours, []),
    urban = useMemo(makeUrbanRoads, []);
  const sourceTexture = useLoader(THREE.TextureLoader, '/scene/aerial_grass_rock.jpg');
  const terrainTexture = useMemo(() => {
    const t = sourceTexture.clone();
    t.colorSpace = THREE.SRGBColorSpace;
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    t.repeat.set(14, 12);
    t.anisotropy = 8;
    return t;
  }, [sourceTexture]);
  const plane = useMemo(() => makeAircraft(PALETTE.cyan), []);
  useEffect(
    () => () => {
      terrain.dispose();
      terrainTexture.dispose();
      contours.dispose();
      urban.dispose();
    },
    [terrain, terrainTexture, contours, urban],
  );
  useSceneAsset(plane);
  const projection = useMemo(() => buildNetworkProjection(world.airports), [world.airports]);
  const airports = world.airports.filter((a) => projection.points.has(a.id));
  const byCode = new Map<string, [number, number, number]>();
  for (const airport of airports) {
    const source = projection.points.get(airport.id)!;
    const p: [number, number, number] = [...source];
    p[1] = projection.regional ? terrainHeight(p[0], p[2]) + 2 : 2;
    byCode.set(airport.id, p);
    byCode.set(airport.code, p);
  }
  const activeOffers = world.offers.filter((o) => ['held', 'confirmed'].includes(o.status));
  const routeStates = new Map<string, 'held' | 'confirmed' | 'scheduled' | 'unavailable'>();
  for (const resource of world.resources) {
    if (
      resource.kind !== 'bus' ||
      resource.origin === resource.destination ||
      !byCode.has(resource.origin) ||
      !byCode.has(resource.destination)
    )
      continue;
    const key = JSON.stringify([resource.origin, resource.destination].sort());
    if (!routeStates.has(key) || resource.available) {
      routeStates.set(key, resource.available ? 'scheduled' : 'unavailable');
    }
  }
  for (const offer of activeOffers)
    for (const leg of offer.itinerary.legs) {
      if (
        leg.kind === 'bus' &&
        leg.origin !== leg.destination &&
        byCode.has(leg.origin) &&
        byCode.has(leg.destination)
      ) {
        const key = JSON.stringify([leg.origin, leg.destination].sort());
        if (routeStates.get(key) !== 'confirmed')
          routeStates.set(key, offer.status as 'held' | 'confirmed');
      }
    }
  const regionFlights = world.resources.filter(
    (r) => r.kind === 'flight' && byCode.has(r.origin) && byCode.has(r.destination),
  );
  return (
    <group>
      {projection.regional ? (
        <>
          <mesh geometry={terrain} receiveShadow>
            <meshStandardMaterial
              vertexColors
              map={terrainTexture}
              roughness={1}
              envMapIntensity={0}
            />
          </mesh>
          <lineSegments geometry={contours}>
            <lineBasicMaterial color="#94b7a8" transparent opacity={0.14} />
          </lineSegments>
          <lineSegments geometry={urban}>
            <lineBasicMaterial color="#b4b8a3" transparent opacity={0.36} />
          </lineSegments>
          {cities.map(([name, lat, lon]) => {
            const [x, , z] = geoPoint(lat, lon);
            return (
              <group key={name} position={[x, terrainHeight(x, z) + 0.3, z]}>
                <mesh rotation={[-Math.PI / 2, 0, 0]}>
                  <circleGeometry args={[name === 'București' ? 7 : 3, 32]} />
                  <meshBasicMaterial color="#b4af91" transparent opacity={0.2} />
                </mesh>
                <Html position={[0, 1, 0]} center distanceFactor={190} zIndexRange={[8, 0]}>
                  <span className="scene-city">{name}</span>
                </Html>
              </group>
            );
          })}
          {[
            [23.8, 44.33, 24.37, 44.43, 24.87, 44.85, 26.1, 44.43],
            [24.37, 44.43, 24.37, 45.1, 24.12, 45.8],
            [24.12, 45.8, 25.6, 45.65],
          ].map((road, i) => {
            const pts: THREE.Vector3[] = [];
            for (let j = 0; j < road.length; j += 2) {
              const [x, , z] = geoPoint(road[j + 1], road[j]);
              pts.push(new THREE.Vector3(x, terrainHeight(x, z) + 0.35, z));
            }
            const smooth = new THREE.CatmullRomCurve3(pts)
              .getPoints(90)
              .map((p) => [p.x, terrainHeight(p.x, p.z) + 0.4, p.z] as [number, number, number]);
            return (
              <Line
                key={i}
                points={smooth}
                color="#7d8678"
                opacity={0.6}
                transparent
                lineWidth={1}
              />
            );
          })}
        </>
      ) : (
        <>
          <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.4, 0]}>
            <planeGeometry args={[650, 520]} />
            <meshStandardMaterial color="#122a31" roughness={1} />
          </mesh>
          <gridHelper args={[520, 26, '#35535b', '#244047']} position={[0, 0, 0]} />
        </>
      )}
      <Html position={[-80, 2, -138]} center zIndexRange={[8, 0]}>
        <span className="scene-city">
          {projection.regional
            ? 'Regiune ilustrată · Conexiuni externe la margine'
            : 'Rețea schematică · Poziții adaptate pentru lizibilitate'}
        </span>
      </Html>
      {airports.map((a) => {
        const pos = byCode.get(a.id)!;
        const weather = airportWeatherState(a);
        const color = weather.restricted ? PALETTE.red : PALETTE.mint;
        return (
          <group
            key={a.id}
            position={pos}
            onClick={(e) => {
              e.stopPropagation();
              onSelect(a.id);
            }}
          >
            <mesh rotation={[-Math.PI / 2, 0, 0]}>
              <ringGeometry args={[3.8, 4.05, 48]} />
              <meshBasicMaterial color={color} />
            </mesh>
            <mesh rotation={[-Math.PI / 2, 0, 0]}>
              <circleGeometry args={[3.8, 48]} />
              <meshBasicMaterial color={color} transparent opacity={0.14} />
            </mesh>
            <mesh position={[0, 1, 0]} rotation={[0, 0.4, 0]}>
              <boxGeometry args={[0.9, 0.2, 5]} />
              <meshBasicMaterial color={color} />
            </mesh>
            <mesh position={[0, 1.05, 0]}>
              <boxGeometry args={[4, 0.2, 0.65]} />
              <meshBasicMaterial color={color} />
            </mesh>
            <Html position={[8, 2, 0]} center zIndexRange={[12, 0]}>
              <button
                className={`scene-tag region-tag ${selectedId === a.id ? 'selected' : ''}`}
                style={{ '--tag-color': color } as React.CSSProperties}
                onClick={() => onSelect(a.id)}
              >
                <strong>{a.code}</strong>
                <span>
                  {projection.regional && !inIllustratedRegion(a.lat, a.lon)
                    ? 'Conexiune externă · '
                    : ''}
                  {!a.available
                    ? 'Indisponibil'
                    : !weather.landingAllowed
                      ? 'Aterizări blocate'
                      : !weather.departureAllowed
                        ? 'Decolări blocate'
                        : 'Disponibil'}
                  {weather.condition ? ` · ${weather.condition}` : ''}
                </span>
              </button>
            </Html>
          </group>
        );
      })}
      {Array.from(routeStates).map(([key, status]) => {
        const [a, b] = JSON.parse(key) as [string, string];
        const p1 = byCode.get(a)!,
          p2 = byCode.get(b)!;
        const p = regionalCurve(p1, p2, 12).map(
          (v) =>
            new THREE.Vector3(v.x, projection.regional ? terrainHeight(v.x, v.z) + 1.3 : 1.3, v.z),
        );
        return (
          <Line
            key={key}
            points={p}
            color={
              status === 'confirmed'
                ? PALETTE.mint
                : status === 'unavailable'
                  ? PALETTE.red
                  : status === 'scheduled'
                    ? '#648b96'
                    : PALETTE.amber
            }
            lineWidth={status === 'scheduled' ? 1.5 : 2.8}
            dashed={status !== 'confirmed'}
            dashSize={3}
            gapSize={2}
            depthTest={false}
            renderOrder={5}
          />
        );
      })}
      {regionFlights.map((flight, i) => {
        const end = byCode.get(flight.destination)!;
        const start = byCode.get(flight.origin)!;
        const pts = regionalCurve(start, end, 32 + (i % 6) * 7);
        const airborne =
          flight.departure <= world.sim_time &&
          world.sim_time < flight.arrival &&
          (flight.available || flight.status === 'needs_diversion');
        const progress = Math.max(
          0,
          Math.min(
            1,
            (world.sim_time - flight.departure) / Math.max(1, flight.arrival - flight.departure),
          ),
        );
        const marker = pts[Math.round((airborne ? progress : 0.5) * (pts.length - 1))];
        const selected = selectedId === flight.id;
        const color = flight.available ? PALETTE.amber : PALETTE.red;
        return (
          <group key={flight.id}>
            <Line
              points={pts}
              color={color}
              lineWidth={selected ? 3 : airborne ? 2 : 1}
              dashed
              dashSize={3}
              gapSize={2}
              depthTest={false}
              renderOrder={6}
              onClick={(event) => {
                event.stopPropagation();
                onSelect(flight.id);
              }}
            />
            {airborne && (
              <group
                position={marker}
                rotation={[0, Math.atan2(end[0] - start[0], end[2] - start[2]), 0]}
                scale={0.29}
                onClick={(e) => {
                  e.stopPropagation();
                  onSelect(flight.id);
                }}
              >
                <primitive object={plane.clone()} />
              </group>
            )}
            {(airborne || selected) && (
              <Html
                position={[
                  marker.x - 6,
                  marker.y + (airborne ? 25 : 6),
                  marker.z + (airborne ? -8 : 5),
                ]}
                zIndexRange={[14, 0]}
              >
                <button
                  className={`scene-tag region-tag region-flight-tag ${selected ? 'selected' : ''}`}
                  style={{ '--tag-color': color } as React.CSSProperties}
                  onClick={() => onSelect(flight.id)}
                  title={`${flight.name} · ${flight.origin} → ${flight.destination}`}
                  aria-label={`Inspect ${flight.name}, ${flight.origin} to ${flight.destination}`}
                >
                  <strong>{flight.id}</strong>
                  <span>
                    {flight.origin} → {flight.destination} · Simulare
                  </span>
                </button>
              </Html>
            )}
          </group>
        );
      })}
      {world.observations.map((o, i) => {
        const lat = Number(o.lat ?? o.latitude),
          lon = Number(o.lon ?? o.longitude);
        if (
          !Number.isFinite(lat) ||
          !Number.isFinite(lon) ||
          Math.abs(lat) > 90 ||
          Math.abs(lon) > 180 ||
          (projection.regional && !inIllustratedRegion(lat, lon))
        )
          return null;
        const pos = projection.project(
          lat,
          lon,
          27 + Math.min(25, Number(o.altitude ?? o.alt ?? 0) / 1500),
        );
        if (!projection.regional && (Math.abs(pos[0]) > 190 || Math.abs(pos[2]) > 120)) return null;
        const id = String(o.id ?? o.fr24_id ?? `observation-${i}`);
        return (
          <group key={id} position={pos}>
            <group
              scale={0.26}
              rotation={[0, (-Number(o.heading ?? o.track ?? 0) * Math.PI) / 180, 0]}
            >
              <primitive object={plane.clone()} />
            </group>
            <Html position={[6, 7, 0]} zIndexRange={[16, 0]}>
              <button
                className="scene-tag region-tag"
                style={{ '--tag-color': PALETTE.cyan } as React.CSSProperties}
                onClick={() => onSelect(id)}
              >
                <strong>{o.callsign || id}</strong>
                <span>Observație · {o.source || 'FR24'}</span>
              </button>
            </Html>
          </group>
        );
      })}
    </group>
  );
}
