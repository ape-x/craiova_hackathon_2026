import * as THREE from 'three';
import {
  apronWearTexture,
  surfaceMaterial,
  surfaceMeters,
  surfaceTexture as texture,
  surfaceUV,
} from './surfaces.ts';
import { gates } from './layout.ts';
export { gates, busSpots } from './layout.ts';

// All geometry in this file is authored for the simulation, not a surveyed airport.
export const PALETTE = {
  cyan: '#5fd9ed',
  mint: '#89e3c1',
  amber: '#f5b75f',
  red: '#ff7368',
  glass: '#233d45',
  white: '#c6cdd1',
};
const rand = (n: number) => {
  const v = Math.sin(n * 127.1 + 311.7) * 43758.5453;
  return v - Math.floor(v);
};

function foliageGeometry() {
  const geometry = new THREE.IcosahedronGeometry(1, 2),
    p = geometry.attributes.position,
    colors = [];
  for (let i = 0; i < p.count; i++) {
    const x = p.getX(i),
      y = p.getY(i),
      z = p.getZ(i),
      rough = Math.sin(x * 14 + y * 9) * Math.sin(z * 12 - x * 8),
      factor = 0.9 + rough * 0.2 + Math.sin(y * 18 + z * 3) * 0.08;
    p.setXYZ(i, x * factor, y * factor, z * factor);
    const c = 0.78 + rough * 0.2;
    colors.push(c, c + 0.045, c - 0.02);
  }
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  // Icosahedra are non-indexed: recomputing normals would create the old faceted rock appearance.
  const normals = geometry.attributes.normal;
  const normal = new THREE.Vector3();
  for (let i = 0; i < p.count; i++) {
    normal.set(p.getX(i), p.getY(i) * 1.08, p.getZ(i)).normalize();
    normals.setXYZ(i, normal.x, normal.y, normal.z);
  }
  return geometry;
}

class Builder {
  group = new THREE.Group();
  batches = new Map<
    string,
    {
      geometry: THREE.BufferGeometry;
      material: THREE.MeshStandardMaterial;
      matrices: THREE.Matrix4[];
      colors: THREE.Color[];
    }
  >();
  dummy = new THREE.Object3D();
  box(
    x: number,
    y: number,
    z: number,
    sx: number,
    sy: number,
    sz: number,
    color: string,
    rotation = 0,
    emissive: boolean | 'glass' = false,
  ) {
    this.add('box', x, y, z, sx, sy, sz, color, rotation, emissive);
  }
  add(
    shape: string,
    x: number,
    y: number,
    z: number,
    sx: number,
    sy: number,
    sz: number,
    color: string,
    rotation = 0,
    emissive: boolean | 'glass' = false,
  ) {
    const key =
      shape +
      (emissive === true ? `emission-${color}` : emissive === 'glass' ? 'glass' : 'standard');
    let batch = this.batches.get(key);
    if (!batch) {
      batch = {
        geometry:
          shape === 'tree'
            ? foliageGeometry()
            : shape === 'sphere'
              ? new THREE.SphereGeometry(1, 8, 5)
              : shape === 'pole'
                ? new THREE.CylinderGeometry(0.5, 0.5, 1, 6)
                : new THREE.BoxGeometry(1, 1, 1),
        material:
          emissive === 'glass'
            ? new THREE.MeshPhysicalMaterial({
                color: 'white',
                roughness: 0.23,
                metalness: 0.22,
                clearcoat: 0.5,
                clearcoatRoughness: 0.18,
                envMapIntensity: 1.2,
                transparent: true,
                opacity: 0.58,
                depthWrite: false,
              })
            : new THREE.MeshStandardMaterial({
                color: 'white',
                vertexColors: shape === 'tree',
                roughness: emissive ? 0.25 : 0.83,
                metalness: emissive ? 0.6 : 0.12,
                ...(emissive === true ? { emissive: color, emissiveIntensity: 0.75 } : {}),
              }),
        matrices: [],
        colors: [],
      };
      this.batches.set(key, batch);
    }
    this.dummy.position.set(x, y, z);
    this.dummy.rotation.set(0, rotation, 0);
    this.dummy.scale.set(sx, sy, sz);
    this.dummy.updateMatrix();
    batch.matrices.push(this.dummy.matrix.clone());
    batch.colors.push(new THREE.Color(color));
  }
  finish() {
    for (const batch of this.batches.values()) {
      const mesh = new THREE.InstancedMesh(batch.geometry, batch.material, batch.matrices.length);
      batch.matrices.forEach((m, i) => {
        mesh.setMatrixAt(i, m);
        mesh.setColorAt(i, batch.colors[i]);
      });
      mesh.castShadow = !batch.material.transparent;
      mesh.receiveShadow = true;
      mesh.computeBoundingSphere();
      this.group.add(mesh);
    }
    return this.group;
  }
}

function slab(
  group: THREE.Group,
  w: number,
  d: number,
  x: number,
  z: number,
  color: string,
  map?: THREE.Texture,
  y = 0.04,
) {
  const geometry = new THREE.PlaneGeometry(w, d);
  surfaceUV(geometry, map);
  const m = new THREE.Mesh(geometry, surfaceMaterial(map, color));
  m.rotation.x = -Math.PI / 2;
  m.position.set(x, y, z);
  m.receiveShadow = true;
  group.add(m);
  return m;
}
export function pathMesh(
  points: [number, number, number][],
  color: string,
  _width = 0.4,
  dashed = false,
) {
  const geometry = new THREE.BufferGeometry().setFromPoints(
    points.map((p) => new THREE.Vector3(...p)),
  );
  const line = new THREE.Line(
    geometry,
    dashed
      ? new THREE.LineDashedMaterial({
          color,
          dashSize: 3,
          gapSize: 2,
          transparent: true,
          opacity: 0.85,
        })
      : new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.85 }),
  );
  line.computeLineDistances();
  return line;
}

function road(
  group: THREE.Group,
  points: [number, number, number][],
  width: number,
  map: THREE.Texture,
  color = '#a6afb1',
) {
  const curve = new THREE.CatmullRomCurve3(points.map((p) => new THREE.Vector3(...p)));
  const samples = curve.getPoints(90),
    verts = [],
    uvs = [],
    indices = [];
  for (let i = 0; i < samples.length; i++) {
    const p = samples[i],
      t = curve.getTangent(i / 90),
      n = new THREE.Vector3(-t.z, 0, t.x).normalize();
    for (const side of [-1, 1]) {
      verts.push(p.x + n.x * width * 0.5 * side, 0.22, p.z + n.z * width * 0.5 * side);
      uvs.push(
        (side < 0 ? 0 : width) / surfaceMeters(map),
        ((i / 90) * curve.getLength()) / surfaceMeters(map),
      );
    }
    if (i < 90) {
      const k = i * 2;
      indices.push(k, k + 2, k + 1, k + 1, k + 2, k + 3);
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(verts, 3));
  geo.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  const material = surfaceMaterial(map, color);
  material.side = THREE.DoubleSide;
  const mesh = new THREE.Mesh(geo, material);
  mesh.receiveShadow = true;
  group.add(mesh);
  const center = new THREE.Line(
    new THREE.BufferGeometry().setFromPoints(samples.map((p) => new THREE.Vector3(p.x, 0.28, p.z))),
    new THREE.LineDashedMaterial({ color: '#d9d8bd', dashSize: 4, gapSize: 4 }),
  );
  center.computeLineDistances();
  group.add(center);
  for (const side of [-1, 1]) {
    const edge = samples.map((p, i) => {
      const t = curve.getTangent(i / 90);
      return new THREE.Vector3(
        p.x - t.z * width * 0.47 * side,
        0.29,
        p.z + t.x * width * 0.47 * side,
      );
    });
    group.add(
      new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(edge),
        new THREE.LineBasicMaterial({ color: '#aeb4a1' }),
      ),
    );
  }
}

export function makeAirport(onLoad?: () => void, sources?: THREE.Texture[]) {
  const b = new Builder();
  const g = b.group;
  const grass = texture('grass', onLoad, sources?.[0]),
    concrete = texture('concrete', onLoad, sources?.[1]),
    asphalt = texture('asphalt', onLoad, sources?.[2]),
    roof = texture('roof');
  slab(g, 1900, 1600, 0, 0, '#bbc7b6', grass, -0.2);
  // Long agricultural parcels, shelterbelts and the surrounding road network.
  for (let i = 0; i < 42; i++) {
    const x = (i % 7) * 230 - 700,
      z = Math.floor(i / 7) * 205 - 650;
    if (x > -340 && x < 340 && z > -340 && z < 340) continue;
    slab(
      g,
      222,
      198,
      x,
      z,
      ['#4c5942', '#687052', '#485d4b', '#636947', '#555b42'][i % 5],
      grass,
      -0.14,
    );
  }
  slab(g, 755, 73, 5, -245, '#737c77', asphalt);
  slab(g, 690, 40, 0, -245, '#707a7c', asphalt, 0.13);
  slab(g, 400, 180, 0, -49, '#c5cbc9', concrete, 0.1);
  const apronWear = new THREE.Mesh(
    new THREE.PlaneGeometry(400, 180),
    new THREE.MeshBasicMaterial({
      map: apronWearTexture(gates.map(([x]) => x)),
      transparent: true,
      depthWrite: false,
      toneMapped: false,
    }),
  );
  // Canvas top maps to north (negative Z) on the horizontal plane.
  apronWear.rotation.x = -Math.PI / 2;
  apronWear.position.set(0, 0.185, -49);
  g.add(apronWear);
  for (let x = -200; x <= 200; x += 12) b.box(x, 0.16, -49, 0.045, 0.025, 180, '#707a79');
  for (let z = -139; z <= 41; z += 12) b.box(0, 0.17, z, 400, 0.025, 0.045, '#707a79');
  slab(g, 690, 17, 0, -172, '#969c98', asphalt, 0.14);
  // Taxiway branches, shoulder lines, runway threshold and center markings.
  [-280, -130, 130, 280].forEach((x) => slab(g, 17, 73, x, -210, '#969c98', asphalt, 0.15));
  for (let x = -320; x <= 320; x += 24) b.box(x, 0.19, -245, 12, 0.04, 0.8, '#c3c8c1');
  for (const z of [-264, -226]) b.box(0, 0.2, z, 681, 0.04, 0.4, '#969e98');
  for (const x of [-313, 313])
    for (let j = -5; j <= 5; j++) b.box(x, 0.2, -245 + j * 2.7, 21, 0.05, 1.2, '#d7d9cc');
  for (const x of [-210, 210]) {
    b.box(x, 0.2, -233, 26, 0.05, 3, '#c7ccc2');
    b.box(x, 0.2, -257, 26, 0.05, 3, '#c7ccc2');
  }
  for (let x = -330; x <= 330; x += 15) {
    b.add('sphere', x, 0.48, -267, 0.2, 0.2, 0.2, '#f1dfaa', 0, true);
    b.add('sphere', x, 0.48, -223, 0.2, 0.2, 0.2, '#f1dfaa', 0, true);
    b.add('sphere', x, 0.37, -172, 0.13, 0.13, 0.13, '#88b5df', 0, true);
  }
  b.box(0, 0.21, -172, 675, 0.03, 0.32, '#b3a158');
  for (const x of [-280, -130, 130, 280]) b.box(x, 0.21, -210, 0.3, 0.03, 74, '#b3a158');
  for (const [x] of gates) {
    b.box(x, 0.22, -56, 0.3, 0.03, 65, '#d9c06c');
    b.box(x, 0.22, -31, 45, 0.03, 0.25, '#d9c06c');
    b.box(x, 0.22, -95, 43, 0.03, 0.25, '#d9c06c');
    b.box(x - 21.5, 0.22, -63, 0.25, 0.03, 65, '#d9c06c');
    b.box(x + 21.5, 0.22, -63, 0.25, 0.03, 65, '#d9c06c');
  }
  for (const [x] of gates) {
    const curve = new THREE.CatmullRomCurve3([
      new THREE.Vector3(x - 35, 0.3, -136),
      new THREE.Vector3(x - 9, 0.3, -125),
      new THREE.Vector3(x, 0.3, -100),
      new THREE.Vector3(x, 0.3, -76),
    ]);
    g.add(
      new THREE.Mesh(
        new THREE.TubeGeometry(curve, 36, 0.16, 4, false),
        new THREE.MeshStandardMaterial({ color: '#d4b55f' }),
      ),
    );
    for (const z of [-34, -29]) b.box(x + 1, 0.29, z, 14, 0.035, 0.22, '#c17d6d');
    for (let t = 0; t < 8; t++) b.box(x + 25 + t * 1.5, 0.29, -24, 1, 0.03, 9, '#bda87a', -0.5);
  }
  b.box(0, 0.26, -122, 390, 0.03, 0.25, '#b4bcae');
  for (let x = -190; x < 195; x += 11) b.box(x, 0.27, -127, 5, 0.035, 0.24, '#d0d1bf');
  // Terminal: structural frame, glazing, floor slabs, fins, parapet and roof services.
  // Recess the opaque core behind the curtain wall, including the short end façades.
  b.box(0, 7.7, 51, 196, 15.4, 47, '#35474e');
  b.box(0, 6.8, 75, 196, 12.8, 0.35, '#172e35');
  // Lighted interior sits behind the glazing and mullions instead of tinting opaque panes yellow.
  b.box(0, 2.6, 75.4, 194, 4.1, 0.12, '#9c8358', 0, true);
  for (let x = -92; x < 98; x += 18) {
    b.box(x, 2.4, 75.7, 0.24, 4.2, 1, '#34464a');
    b.box(x + 4, 1.2, 75.85, 7.5, 1.1, 0.32, '#394c50');
    for (const y of [7.9, 12.1]) b.box(x + 3, y, 75.45, 10.5, 0.13, 0.25, '#b9a57e', 0, true);
  }
  b.box(-99, 7.2, 51, 0.45, 13.6, 49, '#253c43');
  b.box(99, 7.2, 51, 0.45, 13.6, 49, '#253c43');
  for (let x = -97; x < 99; x += 4.5) {
    for (let floor = 0; floor < 3; floor++) {
      b.box(
        x,
        2.4 + floor * 4.25,
        76.24,
        3.7,
        3.5,
        0.12,
        floor === 0 ? '#719097' : '#496775',
        0,
        'glass',
      );
      b.box(x, 2.4 + floor * 4.25, 25.65, 3.7, 3.4, 0.12, '#45616a', 0, 'glass');
    }
    b.box(x - 2, 7, 76.65, 0.19, 14, 0.38, '#73828a');
  }
  for (let f = 0; f < 4; f++) {
    b.box(0, 0.4 + f * 4.3, 77, 202, 0.42, 1.2, '#829393');
  }
  for (let z = 29; z < 76; z += 4.5) {
    for (const x of [-99.4, 99.4]) {
      b.box(x, 7.1, z, 0.55, 14, 0.17, '#8c999b');
      for (let f = 0; f < 3; f++)
        b.box(
          x,
          2.4 + f * 4.25,
          z + 2.2,
          0.1,
          3.6,
          3.9,
          f === 0 ? '#607e84' : '#496775',
          0,
          'glass',
        );
    }
  }
  const rm = slab(g, 204, 54, 0, 51, '#b9c6cb', roof, 15.7);
  rm.castShadow = true;
  b.box(0, 15.2, 51, 204, 0.65, 54, '#65757c');
  b.box(0, 15.9, 24.3, 204, 0.7, 0.55, '#a1abb0');
  b.box(0, 15.9, 77.7, 204, 0.7, 0.55, '#a1abb0');
  for (let x = -91; x < 99; x += 10) {
    b.box(x, 16, 51, 0.2, 0.35, 52, '#7d9198');
  }
  for (let x = -68; x < 90; x += 34) {
    b.box(x, 16.2, 44, 17, 0.6, 7, '#293b43');
    for (let k = 0; k < 5; k++) b.box(x - 8 + k * 4, 16.65, 44, 0.16, 0.15, 7, '#9caaad');
  }
  for (let x = -82; x < 100; x += 28) {
    b.box(x, 16.8, 63, 9, 2.1, 6, '#657982');
    for (let j = 0; j < 3; j++)
      b.add('pole', x - 2.6 + j * 2.5, 18, 63, 1.65, 0.3, 1.65, '#293f49');
  }
  // The central roof lantern and projecting entrance break the long façade into volumes.
  b.box(-4, 18.1, 51, 46, 4.2, 26, '#3e5961');
  slab(g, 49, 29, -4, 51, '#bac7ca', roof, 20.3);
  for (let x = -26; x < 20; x += 4) {
    b.box(x, 18, 64.2, 0.19, 4.5, 0.4, '#9eadb1');
    b.box(x, 18, 37.8, 0.19, 4.5, 0.4, '#9eadb1');
  }
  b.box(-4, 8, 80.5, 40, 15, 8, '#324d55');
  for (let x = -22; x < 18; x += 3.5) {
    b.box(x, 7.5, 84.6, 3, 13, 0.12, '#365966', 0, 'glass');
    b.box(x, 2.4, 84.8, 3, 3.8, 0.1, '#bba06a', 0, true);
    b.box(x - 1.6, 7.5, 84.9, 0.19, 14, 0.5, '#8ca0a3');
  }
  b.box(-4, 15.8, 83, 44, 0.6, 14, '#bbc4bf');
  b.box(-4, 1, 88, 42, 0.3, 3, '#a6b1a8');
  b.box(-4, 0.55, 90, 45, 0.3, 3, '#a6b1a8');
  // Ribbed glazed canopy: its thin frame lets the façade read through the foreground roof.
  for (const z of [85.1, 98.9]) b.box(0, 4.3, z, 220, 0.48, 0.35, '#647b85');
  for (const z of [88.4, 92, 95.6]) b.box(0, 4.21, z, 219, 0.16, 0.16, '#647b85');
  for (let x = -105.3; x < 110; x += 9) {
    b.box(x, 4.42, 92, 8.65, 0.14, 13.4, '#7b969e', 0, 'glass');
    b.box(x - 4.45, 4.27, 92, 0.16, 0.35, 14, '#a2b0b0');
  }
  for (let x = -105; x < 110; x += 15) b.add('pole', x, 2, 96, 0.45, 4, 0.45, '#b8bfc0');
  for (let x = -99; x < 110; x += 11) b.box(x, 3.88, 92, 2.2, 0.1, 0.35, '#efd49d', 0, true);
  slab(g, 230, 31, 0, 93, '#b4b9af', concrete, 0.17);
  for (let x = -106; x < 110; x += 10) b.box(x, 0.25, 96, 0.07, 0.04, 27, '#616f6c');
  // Gate concourse and glazed telescopic jet bridges, with apron stairs and service vehicles.
  b.box(0, 5.1, 17, 204, 10, 16, '#7e8b8d');
  for (let x = -98; x < 102; x += 4) {
    b.box(x, 5.5, 8.8, 3.5, 7, 0.17, '#748c85', 0, true);
    b.box(x - 1.8, 5.3, 8.5, 0.13, 9, 0.4, '#bac0b9');
  }
  slab(g, 208, 19, 0, 17, '#afbfc2', roof, 10.3);
  for (const [x] of gates) {
    b.box(x + 13, 4.6, -1, 8, 5, 22, '#abb6b2');
    b.box(x + 13, 4.8, -2, 8.15, 3.3, 18, '#3c5259');
    for (let z = -10; z < 8; z += 3) b.box(x + 13, 4.8, z, 8.25, 3.9, 0.18, '#a6b5b6');
    b.box(x + 8, 4.5, -16, 17, 4.7, 7, '#9eaeb0');
    b.box(x + 8, 4.9, -19.6, 16, 2.8, 0.12, '#405b64');
    for (let k = 0; k < 5; k++) b.box(x + 2 + k * 3, 4.8, -19.9, 0.13, 3.3, 0.13, '#bac2c1');
    b.add('pole', x + 15, 2, -12, 1, 4, 1, '#69787b');
    b.box(x + 15, 0.6, -12, 6, 1, 4, '#4e6067');
    b.box(x + 31, 0.8, -45, 5.5, 1.6, 2.6, '#c4c8ba');
    b.box(x + 31, 1.7, -45, 2, 0.65, 2.4, '#17323f');
    b.box(x - 20, 0.65, -36, 2.5, 1.3, 5, '#c6c8bf');
    b.box(x - 20, 1.5, -35, 2.3, 1, 2, '#668991');
  }
  // ATC tower and supporting buildings.
  b.box(-157, 13, 29, 9, 26, 9, '#aab2ab');
  b.box(-157, 26.4, 29, 16, 4.1, 15, '#263f49');
  b.box(-157, 29, 29, 18, 0.6, 17, '#b5bebd');
  for (let x = -164; x < -149; x += 3) b.box(x, 26.5, 36.7, 0.18, 4, 0.2, '#b5c1bf');
  b.add('pole', -157, 33, 29, 0.3, 8, 0.3, '#9aa8ab');
  b.box(-193, 4, 26, 48, 8, 34, '#a6afa7');
  slab(g, 50, 36, -193, 26, '#87969b', roof, 8.2);
  for (let x = -211; x < -172; x += 9) {
    b.box(x, 3.7, 43.1, 7, 6.5, 0.2, '#485d64');
    for (let yy = 1; yy < 7; yy += 1) b.box(x, yy, 43.25, 7, 0.08, 0.1, '#8d9d9c');
  }
  b.box(172, 8.5, 77, 38, 17, 36, '#bbc1b7');
  slab(g, 40, 38, 172, 77, '#8b9ba0', roof, 17.2);
  for (let x = 156; x < 190; x += 5)
    for (let y = 3; y < 16; y += 4) b.box(x, y, 95.15, 2.5, 2, 0.1, '#b9a675', 0, true);
  // Landside dual carriageway and terminal loops.
  slab(g, 740, 27, 0, 199, '#a7b0b2', asphalt, 0.15);
  slab(g, 280, 17, 0, 124, '#a6b0b2', asphalt, 0.18);
  road(
    g,
    [
      [-140, 0, 124],
      [-159, 0, 136],
      [-168, 0, 161],
      [-170, 0, 199],
    ],
    18,
    asphalt,
  );
  road(
    g,
    [
      [120, 0, 124],
      [145, 0, 130],
      [164, 0, 150],
      [170, 0, 175],
      [190, 0, 199],
    ],
    18,
    asphalt,
  );
  const roundabout = new THREE.RingGeometry(17, 30, 80);
  surfaceUV(roundabout, asphalt);
  const circle = new THREE.Mesh(roundabout, surfaceMaterial(asphalt, '#a6afb1'));
  circle.rotation.x = -Math.PI / 2;
  circle.position.set(-170, 0.3, 199);
  circle.receiveShadow = true;
  g.add(circle);
  const islandGeometry = new THREE.CircleGeometry(17, 60);
  surfaceUV(islandGeometry, grass);
  const island = new THREE.Mesh(islandGeometry, surfaceMaterial(grass, '#ced5ba'));
  island.rotation.x = -Math.PI / 2;
  island.position.set(-170, 0.35, 199);
  island.receiveShadow = true;
  g.add(island);
  const border = new THREE.Mesh(
    new THREE.RingGeometry(16.6, 17.2, 60),
    new THREE.MeshStandardMaterial({ color: '#a4b0a9' }),
  );
  border.rotation.x = -Math.PI / 2;
  border.position.set(-170, 0.42, 199);
  g.add(border);
  for (let x = -355; x < 370; x += 13) {
    if (Math.abs(x + 170) < 31) continue;
    b.box(x, 0.23, 193, 6, 0.04, 0.25, '#c8c9b5');
    b.box(x, 0.23, 205, 6, 0.04, 0.25, '#c8c9b5');
  }
  for (let x = -139; x < 148; x += 14) b.box(x, 0.25, 124, 6, 0.03, 0.22, '#d4d2bf');
  for (let z = 113; z < 135; z += 2.4) {
    b.box(-72, 0.25, z, 10, 0.04, 1.1, '#c5c8b9');
    b.box(74, 0.25, z, 10, 0.04, 1.1, '#c5c8b9');
  }
  b.box(100, 0.33, 199, 530, 0.3, 1.2, '#5b694b');
  b.box(-280, 0.33, 199, 170, 0.3, 1.2, '#5b694b');
  // Passenger assembly forecourt: unobstructed family clusters and accessible circulation.
  slab(g, 84, 27, -48, 157, '#a6b4b6', concrete, 0.2);
  for (const z of [143.5, 170.5]) b.box(-48, 0.4, z, 84, 0.25, 0.35, '#a9b8b4');
  for (let x = -85; x < -7; x += 6) b.box(x, 0.25, 157, 0.06, 0.025, 27, '#657e7f');
  for (const x of [-78, -56, -34]) {
    b.box(x, 0.7, 168, 3.3, 0.3, 0.9, '#727a69');
    b.box(x, 1.2, 168.4, 3.3, 0.8, 0.15, '#727a69');
    b.add('pole', x - 1.3, 0.4, 168, 0.15, 0.8, 0.15, '#54717a');
    b.add('pole', x + 1.3, 0.4, 168, 0.15, 0.8, 0.15, '#54717a');
  }
  // Parking: hundreds of real 3D car silhouettes, lane markings and islands.
  slab(g, 193, 77, -228, 146, '#777f80', asphalt, 0.15);
  const carColors = ['#a8b2b6', '#d8d5c9', '#334550', '#737f88', '#50595c', '#2f4146', '#997d68'];
  for (let row = 0; row < 7; row++)
    for (let col = 0; col < 33; col++) {
      const x = -318 + col * 5.55,
        z = 115 + row * 10;
      b.box(x, 0.2, z, 2.8, 0.025, 0.12, '#c5c4ae');
      if (rand(row * 149 + col) < 0.18) continue;
      b.box(x, 0.7, z + 3, 2, 1.3, 4.2, carColors[(col + row) % 7]);
      b.box(x, 1.4, z + 2.9, 1.8, 0.7, 2.1, '#273e49');
      b.box(x, 1.85, z + 2.8, 1.75, 0.12, 1.4, carColors[(col + row) % 7]);
    }
  // Floodlight structures; emissive lamps provide sharp points without hundreds of lights.
  for (const [x, z] of [
    [-112, -65],
    [-35, -111],
    [42, -111],
    [118, -65],
    [-110, 100],
    [110, 100],
    [-181, 168],
    [169, 161],
  ]) {
    b.add('pole', x, 11, z, 0.42, 22, 0.42, '#8c999a');
    b.box(x, 22, z, 6, 0.4, 0.5, '#596b75');
    for (let j = -1; j <= 1; j++)
      b.box(x + j * 2, 21.85, z + 0.3, 1.3, 0.3, 0.9, '#eee0ba', 0, true);
  }
  // Tree canopies have layered facets, varying species, trunks and spacing.
  for (let i = 0; i < 1250; i++) {
    let x = (rand(i * 4 + 1) - 0.5) * 1450,
      z = (rand(i * 4 + 2) - 0.5) * 1250;
    if ((Math.abs(x) < 390 && z > -325 && z < 100) || (Math.abs(x) < 350 && z > 100 && z < 220))
      continue;
    const h = 4 + rand(i * 7) * 8;
    b.add('pole', x, h * 0.28, z, 0.5, h * 0.6, 0.5, '#4d4938');
    b.add(
      'tree',
      x,
      h * 0.7,
      z,
      h * 0.43,
      h * 0.55,
      h * 0.43,
      ['#243d35', '#334a39', '#3d5141', '#35472f'][i % 4],
      rand(i) * 6,
    );
    b.add(
      'tree',
      x + 1,
      h * 0.83,
      z + 0.4,
      h * 0.3,
      h * 0.38,
      h * 0.32,
      ['#40513d', '#334839', '#415744'][i % 3],
    );
  }
  for (let i = 0; i < 35; i++) {
    const x = -126 + i * 8,
      z = 108 + (i % 2) * 39;
    b.add('pole', x, 2, z, 0.3, 4, 0.3, '#504b37');
    b.add('tree', x, 4.3, z, 2.4, 3.5, 2.4, '#334b39');
  }
  for (let i = 0; i < 100; i++) {
    const x = i < 48 ? -350 + i * 14 : -320 + (i - 48) * 11,
      z = i < 48 ? 217 : 100 + Math.sin(i) * 5;
    const h = 4 + rand(i) * 3;
    b.add('pole', x, h * 0.4, z, 0.4, h * 0.8, 0.4, '#4d4f3b');
    b.add('tree', x, h, z, 3, h * 0.6, 3, ['#40573d', '#496244', '#354e3b'][i % 3]);
  }
  for (let i = 0; i < 9; i++) {
    const a = (i * Math.PI * 2) / 9,
      x = -170 + Math.cos(a) * 10,
      z = 199 + Math.sin(a) * 10;
    b.add('tree', x, 3, z, 2.5, 3.2, 2.5, '#41583d');
  }
  for (let i = 0; i < 25; i++) {
    const x = -112 + i * 9;
    b.box(x, 0.4, 110, 5, 0.5, 3, '#7c8a73');
    for (let j = 0; j < 3; j++) b.add('sphere', x + j - 1, 1.15, 110, 0.85, 0.85, 0.85, '#4e6648');
  }
  // Low urban clusters beyond the airport establish scale without invented map claims.
  for (let i = 0; i < 130; i++) {
    const x = (rand(i + 420) - 0.5) * 1500,
      z = 330 + rand(i + 230) * 300,
      w = 8 + rand(i) * 15,
      h = 3 + rand(i + 310) * 9;
    b.box(
      x,
      h / 2,
      z,
      w,
      h,
      8 + rand(i + 50) * 12,
      ['#68736e', '#879086', '#6b7776', '#858677'][i % 4],
    );
    b.box(x, h + 0.25, z, w + 0.5, 0.5, 9 + rand(i + 50) * 12, '#4e5f63');
  }
  // Perimeter edge is restrained, with physical fence posts.
  const perimeter: [number, number, number][] = [
    [-370, 0.4, 87],
    [-370, 0.4, -305],
    [370, 0.4, -305],
    [370, 0.4, 87],
    [223, 0.4, 87],
  ];
  g.add(pathMesh(perimeter, '#4e9eae'));
  for (let x = -370; x <= 370; x += 10) {
    b.add('pole', x, 1.4, -305, 0.12, 2.8, 0.12, '#849893');
  }
  return b.finish();
}

function mesh(geometry: THREE.BufferGeometry, color: string, roughness = 0.65, metalness = 0.2) {
  const m = new THREE.Mesh(
    geometry,
    new THREE.MeshStandardMaterial({ color, roughness, metalness }),
  );
  m.castShadow = true;
  m.receiveShadow = true;
  return m;
}
export function makeAircraft(color = '#376f83') {
  const group = new THREE.Group();
  const profile = [
    [0, -19],
    [0.5, -18],
    [1.25, -16],
    [1.55, -12],
    [1.62, -7],
    [1.62, 7],
    [1.5, 12],
    [1.1, 15],
    [0.5, 17],
    [0, 17.8],
  ].map(([r, y]) => new THREE.Vector2(r, y));
  const body = mesh(new THREE.LatheGeometry(profile, 24), '#c8cdd0', 0.4, 0.3);
  body.rotation.x = Math.PI / 2;
  body.position.y = 3.2;
  group.add(body);
  const wingShape = new THREE.Shape();
  wingShape.moveTo(0, 6);
  wingShape.lineTo(16, -3);
  wingShape.lineTo(17, -7);
  wingShape.lineTo(2, -2);
  wingShape.lineTo(0, -6);
  wingShape.closePath();
  const wingG = new THREE.ExtrudeGeometry(wingShape, {
    depth: 0.23,
    bevelEnabled: true,
    bevelSegments: 1,
    bevelSize: 0.1,
    bevelThickness: 0.1,
  });
  for (const sign of [-1, 1]) {
    const wing = mesh(wingG, '#adb9bd');
    wing.rotation.x = -Math.PI / 2;
    wing.scale.x = sign;
    wing.position.set(0, 2.6, -1);
    group.add(wing);
    const eng = mesh(new THREE.CylinderGeometry(1.05, 0.87, 4.6, 18), '#b5c1c6', 0.3, 0.4);
    eng.rotation.x = Math.PI / 2;
    eng.position.set(sign * 6, 1.7, -2);
    group.add(eng);
    const inlet = mesh(new THREE.CylinderGeometry(0.78, 0.78, 0.12, 18), '#182c34');
    inlet.rotation.x = Math.PI / 2;
    inlet.position.set(sign * 6, 1.7, -4.35);
    group.add(inlet);
    const stab = mesh(new THREE.BoxGeometry(11, 0.18, 3.8), '#b8c3c7');
    stab.position.set(0, 4, 14);
    stab.rotation.y = -sign * 0.08;
    group.add(stab);
    for (let i = 0; i < 24; i++) {
      const win = mesh(new THREE.PlaneGeometry(0.33, 0.4), '#244655');
      win.rotation.y = (sign * Math.PI) / 2;
      win.position.set(sign * 1.6, 3.7, -11 + i * 0.94);
      group.add(win);
    }
    const nav = mesh(new THREE.SphereGeometry(0.14, 7, 5), sign < 0 ? '#df695c' : '#8dc6b0');
    nav.position.set(sign * 16.7, 2.85, 6);
    group.add(nav);
  }
  const finShape = new THREE.Shape();
  finShape.moveTo(0, 0);
  finShape.lineTo(0, 7.4);
  finShape.lineTo(3, 9);
  finShape.lineTo(8, 0);
  finShape.closePath();
  const fin = mesh(new THREE.ExtrudeGeometry(finShape, { depth: 0.2, bevelEnabled: false }), color);
  fin.rotation.y = Math.PI / 2;
  fin.position.set(-0.1, 4.2, 10);
  group.add(fin);
  const stripe = mesh(new THREE.BoxGeometry(0.25, 5.5, 2.3), '#bac9ca');
  stripe.rotation.x = -0.25;
  stripe.position.set(0.02, 7.3, 13);
  group.add(stripe);
  const cockpit = mesh(new THREE.SphereGeometry(1, 12, 8), '#244351', 0.2, 0.5);
  cockpit.scale.set(1.1, 0.6, 1.5);
  cockpit.position.set(0, 3.65, -15.4);
  group.add(cockpit);
  for (const [x, z] of [
    [0, -12],
    [-2, 3],
    [2, 3],
  ]) {
    const leg = mesh(new THREE.CylinderGeometry(0.12, 0.12, 1.6, 6), '#6d808b');
    leg.position.set(x, 1.4, z);
    group.add(leg);
    const wheel = mesh(new THREE.CylinderGeometry(0.45, 0.45, 0.65, 10), '#1d292e');
    wheel.rotation.z = Math.PI / 2;
    wheel.position.set(x, 0.55, z);
    group.add(wheel);
  }
  return group;
}

export function makeBus(color = '#7dc7ba') {
  const b = new Builder();
  b.box(0, 1.6, 0, 3, 2.9, 12.4, '#bac5c2');
  b.box(0, 2.1, 0, 3.04, 1.3, 10.9, '#213e4a');
  b.box(0, 0.85, 0, 3.1, 0.7, 12.45, color);
  b.box(0, 3.12, 0, 3.05, 0.25, 12.5, '#d1d7d3');
  for (let z = -4.8; z < 5; z += 1.5) {
    b.box(0, 2.1, z, 3.12, 1.4, 0.1, '#a6b8b9');
  }
  b.box(0, 2, -6.3, 2.7, 1.65, 0.12, '#315261');
  b.box(0, 2, 6.3, 2.7, 1.4, 0.1, '#2c4b59');
  b.box(0, 3.3, 0.7, 1.65, 0.35, 3.4, '#7b8f95');
  for (const x of [-1.5, 1.5])
    for (const z of [-3.9, 3.9]) b.add('sphere', x, 0.62, z, 0.28, 0.65, 0.65, '#182b32');
  b.box(-1, 1, -6.4, 0.45, 0.24, 0.12, '#e5e8cf', 0, true);
  b.box(1, 1, -6.4, 0.45, 0.24, 0.12, '#e5e8cf', 0, true);
  return b.finish();
}

export function disposeGroup(group: THREE.Group) {
  const geometries = new Set<THREE.BufferGeometry>(),
    materials = new Set<THREE.Material>(),
    textures = new Set<THREE.Texture>();
  group.traverse((o) => {
    const m = o as THREE.Mesh;
    if (m.geometry) geometries.add(m.geometry);
    if (m.material)
      for (const mat of Array.isArray(m.material) ? m.material : [m.material]) {
        materials.add(mat);
        for (const value of Object.values(mat))
          if (value instanceof THREE.Texture) textures.add(value);
      }
  });
  geometries.forEach((x) => x.dispose());
  materials.forEach((x) => x.dispose());
  textures.forEach((x) => x.dispose());
}
