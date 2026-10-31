import * as THREE from 'three';
import { gates } from './layout';

/** A purpose-built architectural maquette, not a surveyed or live airport model. */
export function makeStudioAirport(dark = false) {
  const nightSurfaces: Record<string, string> = {
    '#dfe8ef': '#1b2734', // surrounding ground
    '#cedbe5': '#2c3b4b', // airport footprint
    '#697d8d': '#16202c', // runway
    '#b5c6d2': '#455666', // taxiways
    '#e0e7eb': '#344454', // apron
    '#d1dce3': '#405060', // apron grid
    '#b9c9d4': '#253443', // roads
    '#c7d5df': '#304151', // parking
    '#96b2a1': '#3c6059', // planting
    '#b9c5bc': '#344b45',
    '#5d7c8d': '#456579', // terminal glazing
    '#7b929d': '#354e62',
    '#8fa9b7': '#8a8871', // hotel windows
  };
  const surfaceColor = (color: string) =>
    dark
      ? new THREE.Color(nightSurfaces[color] ?? color).lerp(
          new THREE.Color('#1b232d'),
          nightSurfaces[color] ? 0 : 0.55,
        )
      : new THREE.Color(color);
  const group = new THREE.Group();
  const boxGeometry = new THREE.BoxGeometry(1, 1, 1);
  const foliageGeometry = new THREE.SphereGeometry(1, 12, 9);
  const trunkGeometry = new THREE.CylinderGeometry(0.45, 0.65, 1, 7);
  const batches = new Map<
    string,
    {
      geometry: THREE.BufferGeometry;
      material: THREE.MeshStandardMaterial;
      transforms: THREE.Matrix4[];
    }
  >();
  const transform = new THREE.Object3D();
  const add = (
    shape: string,
    color: string,
    x: number,
    y: number,
    z: number,
    w: number,
    h: number,
    d: number,
    rotation = 0,
  ) => {
    const key = `${shape}:${color}`;
    let batch = batches.get(key);
    if (!batch) {
      batch = {
        geometry:
          shape === 'tree' ? foliageGeometry : shape === 'trunk' ? trunkGeometry : boxGeometry,
        material: new THREE.MeshStandardMaterial({
          color: surfaceColor(color),
          emissive: dark && shape === 'glass' ? '#9eadae' : '#000000',
          emissiveIntensity: dark && shape === 'glass' ? 0.12 : 0,
          roughness: shape === 'glass' ? 0.28 : 0.88,
          metalness: shape === 'glass' ? 0.12 : 0,
        }),
        transforms: [],
      };
      batches.set(key, batch);
    }
    transform.position.set(x, y, z);
    transform.rotation.set(0, rotation, 0);
    transform.scale.set(w, h, d);
    transform.updateMatrix();
    batch.transforms.push(transform.matrix.clone());
  };
  const box = (
    x: number,
    y: number,
    z: number,
    w: number,
    h: number,
    d: number,
    color = '#f3f1e8',
    rotation = 0,
  ) => add('box', color, x, y, z, w, h, d, rotation);
  const tree = (x: number, z: number, scale = 1) => {
    add('trunk', '#b9c5bc', x, 2 * scale, z, 0.55, 4 * scale, 0.55);
    add('tree', '#96b2a1', x, 5 * scale, z, 3 * scale, 4 * scale, 3 * scale);
  };
  const line = (points: THREE.Vector3[], color = '#c4ccba', dashed = false) => {
    const object = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(points),
      dashed
        ? new THREE.LineDashedMaterial({ color: surfaceColor(color), dashSize: 5, gapSize: 5 })
        : new THREE.LineBasicMaterial({ color: surfaceColor(color) }),
    );
    object.computeLineDistances();
    group.add(object);
  };
  // A continuous pale ground makes the infrastructure and live selection easy to read.
  box(0, -2, 0, 2200, 3.5, 2000, '#dfe8ef');
  box(0, -0.25, -48, 785, 0.8, 560, '#cedbe5');
  // Runway and taxiway use blue-gray ink, with honest illustrative scale.
  box(0, 0.25, -245, 690, 0.5, 40, '#697d8d');
  box(0, 0.22, -172, 690, 0.3, 16, '#b5c6d2');
  for (const x of [-280, -130, 130, 280]) box(x, 0.24, -210, 16, 0.3, 73, '#b5c6d2');
  for (let x = -320; x <= 320; x += 26) box(x, 0.54, -245, 12, 0.05, 0.7, '#edf1e6');
  for (const z of [-264, -226]) box(0, 0.55, z, 680, 0.06, 0.35, '#e4eadc');
  for (const x of [-316, 316])
    for (let j = -5; j <= 5; j++) box(x, 0.55, -245 + j * 2.6, 20, 0.06, 1.2, '#f1f3eb');
  box(0, 0.45, -172, 670, 0.04, 0.35, '#e5d8b0');
  box(0, 0.1, -49, 400, 0.3, 180, '#e0e7eb');
  for (let x = -200; x <= 200; x += 20) box(x, 0.28, -49, 0.055, 0.025, 180, '#d1dce3');
  for (let z = -139; z <= 41; z += 20) box(0, 0.28, z, 400, 0.025, 0.055, '#d1dce3');
  for (const [x] of gates) {
    box(x, 0.3, -61, 0.45, 0.03, 71, '#cbbb8e');
    box(x, 0.3, -96, 44, 0.03, 0.3, '#cbbb8e');
    for (const edge of [-22, 22]) box(x + edge, 0.3, -60, 0.3, 0.03, 72, '#cbbb8e');
  }
  // White structural slabs, recessed glazing, thin mullions and planted roofs.
  box(0, 7.5, 51, 198, 15, 49, '#e8e9df');
  box(0, 7, 76, 196, 12, 0.5, '#7b929d');
  box(0, 7, 26, 196, 12, 0.5, '#7b929d');
  for (let x = -96; x < 99; x += 5) {
    for (const z of [76.4, 25.6]) add('glass', '#5d7c8d', x, 7, z, 4.5, 11, 0.25);
    for (const z of [76.8, 25.2]) box(x - 2.4, 7, z, 0.28, 14, 0.7, '#f8f7f0');
  }
  for (const y of [0.7, 5, 9.3, 14.5]) box(0, y, 51, 201, 0.45, 51, '#f5f4ec');
  box(0, 15.4, 51, 206, 1.1, 56, '#faf8f0');
  for (let x = -95; x <= 95; x += 19) box(x, 16.05, 51, 0.14, 0.15, 54, '#e4e7dc');
  box(-4, 17.3, 51, 47, 3.3, 27, '#a3b8c3');
  box(-4, 19.2, 51, 50, 0.7, 30, '#faf8f0');
  for (let x = -23; x < 21; x += 5) box(x, 17.3, 65, 0.18, 3.7, 0.3, '#f8f7f0');
  for (const x of [-73, 52, 80]) {
    box(x, 16.3, 48, 15, 1.2, 22, '#bfcaaa');
    for (let k = 0; k < 4; k++) box(x - 5 + k * 3.3, 17.1, 48, 2.5, 0.3, 16, '#ccd6bb');
  }
  box(0, 4.7, 17, 202, 9.4, 17, '#9eb4bf');
  box(0, 9.9, 17, 207, 0.8, 20, '#f8f7ed');
  for (let x = -98; x < 101; x += 5) box(x, 4.7, 8.3, 0.24, 9, 0.5, '#f4f3eb');
  for (const [x] of gates) {
    box(x + 13, 4.4, -1, 8, 4.4, 22, '#b8c8d0');
    box(x + 13, 6.9, -1, 8.7, 0.5, 23, '#f6f5ec');
    box(x + 8, 4.4, -16, 17, 4.5, 7, '#b6c6cf');
    box(x + 8, 6.9, -16, 18, 0.5, 8, '#f6f5ec');
    for (let z = -10; z < 9; z += 3) box(x + 13, 4.4, z, 8.2, 4.3, 0.18, '#eef0e6');
    box(x + 15, 1.5, -12, 1.2, 3, 1.2, '#b0bba5');
  }
  // Arrival canopy and pedestrian forecourt.
  box(0, 0.2, 94, 232, 0.4, 31, '#eeeee4');
  box(0, 4.5, 92, 220, 0.7, 15, '#f8f7ee');
  for (let x = -105; x <= 105; x += 15) box(x, 2, 96, 0.4, 4, 0.4, '#c4cdbb');
  box(-4, 8, 81, 40, 15, 9, '#a1b8c6');
  box(-4, 15.8, 83, 44, 0.6, 14, '#faf8f1');
  // ATC, accommodation and a small hangar remain tied to the existing selection anchors.
  box(-157, 13, 29, 9, 26, 9, '#f3f1e7');
  box(-157, 26.5, 29, 16, 4, 15, '#8fa9ba');
  box(-157, 29, 29, 18, 0.7, 17, '#faf8ef');
  box(-157, 32, 29, 0.35, 6, 0.35, '#96a68c');
  box(-193, 4, 26, 46, 8, 34, '#e9eade');
  box(-193, 8.5, 26, 49, 1, 36, '#f8f6ed');
  box(172, 8.5, 77, 38, 17, 36, '#f3f1e6');
  box(172, 17.5, 77, 40, 1, 38, '#f9f8f0');
  for (let x = 157; x < 190; x += 5)
    for (let y = 3; y < 16; y += 4) add('glass', '#8fa9b7', x, y, 95.1, 2.6, 2.1, 0.2);
  // Circulation is quieter than the operational overlays.
  box(0, 0.14, 199, 740, 0.3, 25, '#b9c9d4');
  box(0, 0.16, 124, 280, 0.3, 17, '#b9c9d4');
  const road = (points: [number, number, number][]) => {
    const curve = new THREE.CatmullRomCurve3(points.map((p) => new THREE.Vector3(...p)));
    const samples = curve.getPoints(64);
    const vertices: number[] = [];
    const indices: number[] = [];
    samples.forEach((p, i) => {
      const t = curve.getTangent(i / 64);
      for (const side of [-1, 1]) vertices.push(p.x - t.z * 8 * side, 0.34, p.z + t.x * 8 * side);
      if (i < 64) {
        const k = i * 2;
        indices.push(k, k + 2, k + 1, k + 1, k + 2, k + 3);
      }
    });
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
    geometry.setIndex(indices);
    geometry.computeVertexNormals();
    const mesh = new THREE.Mesh(
      geometry,
      new THREE.MeshStandardMaterial({
        color: surfaceColor('#b9c9d4'),
        roughness: 1,
        side: THREE.DoubleSide,
      }),
    );
    mesh.receiveShadow = true;
    group.add(mesh);
    line(
      samples.map((p) => new THREE.Vector3(p.x, 0.4, p.z)),
      '#ecf0e5',
      true,
    );
  };
  road([
    [-140, 0, 124],
    [-160, 0, 135],
    [-172, 0, 170],
    [-172, 0, 199],
  ]);
  road([
    [130, 0, 124],
    [160, 0, 137],
    [175, 0, 175],
    [192, 0, 199],
  ]);
  for (let x = -350; x < 360; x += 20) box(x, 0.35, 199, 9, 0.03, 0.3, '#eff2e8');
  for (let x = -130; x < 135; x += 16) box(x, 0.36, 124, 7, 0.03, 0.3, '#eff2e8');
  box(-48, 0.14, 157, 86, 0.3, 28, '#f0f0e6');
  for (let x = -85; x < -7; x += 11) box(x, 0.32, 157, 0.05, 0.02, 27, '#c8d5de');
  // Small parking field, just enough to give a sense of scale.
  box(-235, 0.05, 145, 150, 0.3, 70, '#c7d5df');
  for (let row = 0; row < 4; row++)
    for (let col = 0; col < 18; col++) {
      const x = -303 + col * 7.7,
        z = 120 + row * 16;
      box(x, 0.25, z, 3, 0.03, 6, '#e6eadf');
      if ((col + row) % 4 === 0) continue;
      box(x, 1, z, 2.7, 1.5, 5.5, (col + row) % 3 ? '#eff0e7' : '#a9bbb7');
      box(x, 1.95, z, 2.3, 0.65, 3.2, '#c6d4d6');
    }
  // Ordered landscape instead of texture noise and hundreds of distant props.
  for (let x = -330; x <= 330; x += 22) {
    tree(x, 226, 1.1);
    if (Math.abs(x) > 210) tree(x, -120, 0.85);
  }
  for (let x = -125; x <= 130; x += 17) tree(x, 108, 0.75);
  for (let x = -140; x <= 145; x += 24) {
    if (x < -95 || x > 5) tree(x, 177, 0.9);
  }
  for (let i = 0; i < 20; i++) {
    tree(-365 + (i % 2) * 19, -175 + i * 19, 1 + (i % 3) * 0.12);
    tree(330 + (i % 2) * 24, -145 + i * 18, 0.9 + (i % 3) * 0.12);
  }
  // Thin perimeter and pale landscaping plates make the footprint legible from above.
  line(
    [
      new THREE.Vector3(-392, 0.4, -328),
      new THREE.Vector3(392, 0.4, -328),
      new THREE.Vector3(392, 0.4, 232),
      new THREE.Vector3(-392, 0.4, 232),
      new THREE.Vector3(-392, 0.4, -328),
    ],
    '#b8cad7',
  );
  for (const batch of batches.values()) {
    const mesh = new THREE.InstancedMesh(batch.geometry, batch.material, batch.transforms.length);
    batch.transforms.forEach((matrix, i) => mesh.setMatrixAt(i, matrix));
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    mesh.computeBoundingSphere();
    group.add(mesh);
  }
  return group;
}
