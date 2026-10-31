/** Rebuild the authored geographic-overlay models: node src/scene/export-models.mjs */
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { stripTypeScriptTypes } from 'node:module';
import { createHash } from 'node:crypto';
import * as THREE from 'three';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { makeAircraft, makeBus, disposeGroup } from './geometry.ts';
import { gates } from './layout.ts';

// GLTFExporter uses the standard FileReader surface for its in-memory Blob.
// Node already supplies Blob; no browser, canvas, network or offscreen rendering is used.
class NodeFileReader {
  result = null;
  onloadend = null;
  onerror = null;
  readAsArrayBuffer(blob) {
    blob
      .arrayBuffer()
      .then((result) => {
        this.result = result;
        this.onloadend?.({ target: this });
      })
      .catch((error) => this.onerror?.(error));
  }
  readAsDataURL(blob) {
    blob
      .arrayBuffer()
      .then((result) => {
        this.result = `data:${blob.type || 'application/octet-stream'};base64,${Buffer.from(result).toString('base64')}`;
        this.onloadend?.({ target: this });
      })
      .catch((error) => this.onerror?.(error));
  }
}
globalThis.FileReader = NodeFileReader;

function coreMaterial(original, tint) {
  // Google accepts core glTF PBR only: explicitly remove physical-material extensions,
  // retaining tint, emission and opacity rather than exporting clearcoat/transmission.
  if (Object.values(original).some((value) => value instanceof THREE.Texture))
    throw new Error('Authored overlay assets must remain texture-free and self-contained.');
  const material = new THREE.MeshStandardMaterial({
    color: original.color?.clone() ?? new THREE.Color('white'),
    emissive: original.emissive?.clone() ?? new THREE.Color('black'),
    emissiveIntensity: 1,
    roughness: original.roughness ?? 0.8,
    metalness: original.metalness ?? 0,
    side: original.side,
    opacity: original.opacity,
    transparent: original.transparent,
    alphaTest: original.alphaTest,
    vertexColors: original.vertexColors,
  });
  if (tint) material.color.multiply(tint);
  material.emissive.multiplyScalar(original.emissiveIntensity ?? 1);
  return material;
}

async function terminalFactory() {
  // Reuse the actual native building recipe, rather than exporting the whole airport
  // (which would cover real map imagery with invented grass, roads and runways).
  const source = await readFile(new URL('./geometry.ts', import.meta.url), 'utf8');
  const builderStart = source.indexOf('class Builder {');
  const builderEnd = source.indexOf('\nfunction slab(', builderStart);
  const terminalStart = source.indexOf('// Terminal: structural frame');
  const terminalEnd = source.indexOf('// ATC tower and supporting buildings.', terminalStart);
  if ([builderStart, builderEnd, terminalStart, terminalEnd].some((index) => index < 0))
    throw new Error(
      'Native terminal recipe boundaries changed; review exporter before rebuilding.',
    );
  const builder = stripTypeScriptTypes(source.slice(builderStart, builderEnd), { mode: 'strip' });
  const recipe = source.slice(terminalStart, terminalEnd);
  const roofSlab = (group, width, depth, x, z, color, _map, y) => {
    if (y < 1) return new THREE.Object3D(); // Exclude the native pedestrian ground slab.
    const mesh = new THREE.Mesh(
      new THREE.PlaneGeometry(width, depth),
      new THREE.MeshStandardMaterial({ color, roughness: 0.58, metalness: 0.25 }),
    );
    mesh.rotation.x = -Math.PI / 2;
    mesh.position.set(x, y, z);
    group.add(mesh);
    return mesh;
  };
  // This compiles only the trusted local geometry recipe; no downloaded code is executed.
  const build = new Function(
    'THREE',
    'gates',
    'slab',
    `${builder}\nreturn () => { const b = new Builder(); const g = b.group; const roof = undefined; const concrete = undefined; ${recipe}\n return b.finish(); };`,
  )(THREE, gates, roofSlab);
  return () => {
    const model = build();
    model.position.z = -51;
    return model;
  };
}

function makePassengers(count) {
  const group = new THREE.Group();
  const add = (parent, geometry, color, position, roughness = 0.85) => {
    const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ color, roughness }));
    mesh.position.set(...position);
    parent.add(mesh);
  };
  for (let index = 0; index < count; index++) {
    const person = new THREE.Group();
    person.name = `passenger_${index + 1}`;
    person.position.set((index % 3) * 1.4, 0.005, Math.floor(index / 3) * 1.5);
    add(person, new THREE.SphereGeometry(0.26, 8, 6), '#c3b9a8', [0, 1.36, 0]);
    add(
      person,
      new THREE.CapsuleGeometry(0.23, 0.57, 3, 7),
      index % 2 ? '#a5b8be' : '#506b79',
      [0, 0.82, 0],
    );
    for (const x of [-0.13, 0.13])
      add(person, new THREE.BoxGeometry(0.16, 0.45, 0.2), '#263c49', [x, 0.22, 0]);
    add(person, new THREE.BoxGeometry(0.33, 0.54, 0.26), '#7d7a67', [0.52, 0.32, 0.1]);
    group.add(person);
  }
  return group;
}

function flattenedModel(source, name, metadata) {
  source.updateMatrixWorld(true);
  const batches = new Map();
  const local = new THREE.Matrix4();
  const color = new THREE.Color();
  const append = (mesh, matrix, tint) => {
    const original = mesh.material;
    if (Array.isArray(original)) throw new Error('Export requires one material per authored mesh.');
    if (name === 'terminal') {
      const bounds = new THREE.Box3()
        .setFromBufferAttribute(mesh.geometry.attributes.position)
        .applyMatrix4(matrix);
      // Remove forecourt paint and independent service vehicles from this architectural asset.
      if (bounds.max.y < 0.5 || bounds.max.z < -81) return;
    }
    const material = coreMaterial(original, tint);
    const key = JSON.stringify([
      material.color.toArray(),
      material.emissive.toArray(),
      material.emissiveIntensity,
      material.roughness,
      material.metalness,
      material.side,
      material.opacity,
      material.transparent,
      material.alphaTest,
      material.vertexColors,
    ]);
    let batch = batches.get(key);
    if (!batch) {
      batch = { material, geometries: [] };
      batches.set(key, batch);
    } else material.dispose();
    let geometry = mesh.geometry.clone();
    geometry.applyMatrix4(matrix);
    // Reflection transforms need reversed triangle winding after baking transforms.
    if (matrix.determinant() < 0) {
      if (geometry.index) {
        const index = geometry.index;
        for (let i = 0; i < index.count; i += 3) {
          const a = index.getX(i + 1);
          index.setX(i + 1, index.getX(i + 2));
          index.setX(i + 2, a);
        }
      } else {
        for (const attribute of Object.values(geometry.attributes)) {
          for (let i = 0; i < attribute.count; i += 3)
            for (let component = 0; component < attribute.itemSize; component++) {
              const a = (i + 1) * attribute.itemSize + component,
                b = (i + 2) * attribute.itemSize + component;
              const saved = attribute.array[a];
              attribute.array[a] = attribute.array[b];
              attribute.array[b] = saved;
            }
        }
      }
    }
    // Mixed primitive geometries can be indexed or unindexed. Normalize for merging.
    if (geometry.index) {
      const unindexed = geometry.toNonIndexed();
      geometry.dispose();
      geometry = unindexed;
    }
    batch.geometries.push(geometry);
  };
  source.traverse((object) => {
    if (object.isInstancedMesh) {
      for (let i = 0; i < object.count; i++) {
        object.getMatrixAt(i, local);
        const matrix = object.matrixWorld.clone().multiply(local);
        object.getColorAt(i, color);
        append(object, matrix, color);
      }
    } else if (object.isMesh) append(object, object.matrixWorld, null);
  });
  const group = new THREE.Group();
  group.name = name;
  for (const batch of batches.values()) {
    const geometry = mergeGeometries(batch.geometries, false);
    if (!geometry) throw new Error('Failed to merge authored geometry.');
    const mesh = new THREE.Mesh(geometry, batch.material);
    mesh.name = `${name}_surface_${group.children.length}`;
    group.add(mesh);
    batch.geometries.forEach((geometry) => geometry.dispose());
  }
  group.userData = {
    provenance: 'Original Airport Recovery Live procedural geometry',
    units: 'meters',
    upAxis: '+Y',
    forwardAxis: '-Z',
    role: 'Authored simulation visualization; not a surveyed or operational technical model',
    ...metadata,
  };
  return group;
}

const output = fileURLToPath(new URL('../../public/scene/models/', import.meta.url));
await mkdir(output, { recursive: true });
const manifest = {
  generatedFrom: [
    'frontend/src/scene/geometry.ts',
    'frontend/src/scene/Scene.tsx',
    'frontend/src/scene/export-models.mjs',
  ],
  units: 'meters',
  upAxis: '+Y',
  forwardAxis: '-Z',
  models: {},
};
const makeTerminal = await terminalFactory();
const definitions = [
  ['aircraft', makeAircraft, { nativeAnchor: [0, 0, 0] }],
  ['bus', makeBus, { nativeAnchor: [0, 0, 0] }],
  [
    'terminal',
    makeTerminal,
    {
      nativeAnchor: [0, 0, 51],
      airsideAxis: '-Z',
      publicEntranceAxis: '+Z',
      containsGround: false,
    },
  ],
  ...Array.from({ length: 5 }, (_, index) => [
    `passengers-${index + 1}`,
    () => makePassengers(index + 1),
    { nativeAnchor: [0, 0, 0], figureCount: index + 1, containsGround: false },
  ]),
];
for (const [name, make, metadata] of definitions) {
  const source = make();
  const model = flattenedModel(source, name, metadata);
  const bounds = new THREE.Box3().setFromObject(model),
    size = bounds.getSize(new THREE.Vector3());
  const binary = await new GLTFExporter().parseAsync(model, { binary: true, onlyVisible: true });
  const bytes = Buffer.from(binary);
  // Validate the JSON chunk and reject optional glTF extensions for Google core-PBR support.
  if (bytes.readUInt32LE(0) !== 0x46546c67 || bytes.readUInt32LE(4) !== 2)
    throw new Error('Invalid GLB v2 header');
  const jsonLength = bytes.readUInt32LE(12),
    json = JSON.parse(
      bytes
        .subarray(20, 20 + jsonLength)
        .toString('utf8')
        .trim(),
    );
  if (json.extensionsUsed?.length || json.extensionsRequired?.length)
    throw new Error(`Unexpected glTF extensions: ${json.extensionsUsed}`);
  if (bytes.length > 2_000_000) throw new Error(`${name} exceeds the map loader's 2 MB limit.`);
  if (json.buffers?.some((buffer) => buffer.uri) || json.images?.length || json.textures?.length)
    throw new Error(`${name} unexpectedly depends on an external or textured asset.`);
  const parsed = await new GLTFLoader().parseAsync(
    bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength),
    '',
  );
  const reloadedBounds = new THREE.Box3().setFromObject(parsed.scene);
  if (
    reloadedBounds.min.distanceTo(bounds.min) > 0.0001 ||
    reloadedBounds.max.distanceTo(bounds.max) > 0.0001
  )
    throw new Error(`${name} bounds changed after a GLTFLoader round trip.`);
  parsed.scene.traverse((object) => {
    if (!object.isMesh) return;
    for (const value of object.geometry.attributes.position.array)
      if (!Number.isFinite(value)) throw new Error(`${name} contains non-finite geometry.`);
  });
  disposeGroup(parsed.scene);
  await writeFile(`${output}${name}.glb`, bytes);
  manifest.models[name] = {
    url: `/scene/models/${name}.glb`,
    bytes: bytes.length,
    sha256: createHash('sha256').update(bytes).digest('hex'),
    bounds: { min: bounds.min.toArray(), max: bounds.max.toArray() },
    dimensions: { width: size.x, height: size.y, length: size.z },
    meshCount: json.meshes?.length || 0,
    extensions: [],
    ...metadata,
    validation: 'GLB2/core-PBR/self-contained/under-2MB/GLTFLoader-roundtrip passed',
  };
  disposeGroup(source);
  disposeGroup(model);
}
await writeFile(`${output}manifest.json`, JSON.stringify(manifest, null, 2) + '\n');
process.stdout.write(JSON.stringify(manifest, null, 2) + '\n');
