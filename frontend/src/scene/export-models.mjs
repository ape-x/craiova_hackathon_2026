/** Rebuild the authored geographic-overlay models: node src/scene/export-models.mjs */
import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import * as THREE from 'three';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { makeAircraft, makeBus, disposeGroup } from './geometry.ts';

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

function flattenedModel(source, name) {
  source.updateMatrixWorld(true);
  const batches = new Map();
  const local = new THREE.Matrix4();
  const color = new THREE.Color();
  const append = (mesh, matrix, tint) => {
    const original = mesh.material;
    if (Array.isArray(original)) throw new Error('Export requires one material per authored mesh.');
    const material = original.clone();
    if (tint) material.color.multiply(tint);
    // Keep emissive lighting inside core glTF; avoid KHR_materials_emissive_strength.
    material.emissive.multiplyScalar(material.emissiveIntensity);
    material.emissiveIntensity = 1;
    const key = JSON.stringify([
      material.color.toArray(),
      material.emissive.toArray(),
      material.emissiveIntensity,
      material.roughness,
      material.metalness,
      material.side,
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
    role: 'visualization only; not an aircraft-specific technical model',
  };
  return group;
}

const output = fileURLToPath(new URL('../../public/scene/models/', import.meta.url));
await mkdir(output, { recursive: true });
const manifest = {
  generatedFrom: 'frontend/src/scene/geometry.ts',
  units: 'meters',
  upAxis: '+Y',
  forwardAxis: '-Z',
  models: {},
};
for (const [name, make] of [
  ['aircraft', makeAircraft],
  ['bus', makeBus],
]) {
  const source = make();
  const model = flattenedModel(source, name);
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
  await writeFile(`${output}${name}.glb`, bytes);
  manifest.models[name] = {
    url: `/scene/models/${name}.glb`,
    bytes: bytes.length,
    bounds: { min: bounds.min.toArray(), max: bounds.max.toArray() },
    dimensions: { width: size.x, height: size.y, length: size.z },
    meshCount: json.meshes?.length || 0,
    extensions: [],
  };
  disposeGroup(source);
  disposeGroup(model);
}
await writeFile(`${output}manifest.json`, JSON.stringify(manifest, null, 2) + '\n');
process.stdout.write(JSON.stringify(manifest, null, 2) + '\n');
