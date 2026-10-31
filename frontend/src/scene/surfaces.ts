import * as THREE from 'three';

type SurfaceKind = 'grass' | 'concrete' | 'asphalt' | 'roof';
type SurfaceDetail = {
  kind: SurfaceKind;
  meters: number;
  normal: THREE.Texture;
  roughness: THREE.Texture;
};
const details = new WeakMap<THREE.Texture, SurfaceDetail>();
const noise = (x: number, y: number) => {
  const value = Math.sin(x * 127.1 + y * 311.7) * 43758.5453;
  return value - Math.floor(value);
};

/** Meter-scaled, locally authored material detail; the photographs supply subtle albedo only. */
export function surfaceTexture(kind: SurfaceKind, onLoad?: () => void, source?: THREE.Texture) {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 512;
  const ctx = canvas.getContext('2d')!;
  const base = {
    grass: [64, 80, 62],
    concrete: [149, 155, 153],
    asphalt: [62, 68, 70],
    roof: [103, 116, 123],
  }[kind];
  const pixels = ctx.createImageData(512, 512);
  for (let y = 0; y < 512; y++) {
    for (let x = 0; x < 512; x++) {
      const index = (y * 512 + x) * 4;
      const grain = (noise(x, y) - 0.5) * (kind === 'grass' ? 13 : 7);
      const variation = Math.sin(x * 0.039) * Math.sin(y * 0.027) * 2;
      for (let channel = 0; channel < 3; channel++)
        pixels.data[index + channel] = base[channel] + grain + variation;
      pixels.data[index + 3] = 255;
    }
  }
  ctx.putImageData(pixels, 0, 0);
  if (kind === 'roof') {
    for (let x = 0; x < 512; x += 51.2) {
      ctx.fillStyle = '#d6e0e124';
      ctx.fillRect(x, 0, 2, 512);
      ctx.fillStyle = '#172b3230';
      ctx.fillRect(x + 2, 0, 1, 512);
    }
  }
  const map = new THREE.CanvasTexture(canvas);
  map.colorSpace = THREE.SRGBColorSpace;
  map.wrapS = map.wrapT = THREE.RepeatWrapping;
  map.anisotropy = 8;
  const meters = { grass: 96, concrete: 6, asphalt: 8, roof: 6 }[kind];
  const size = 256;
  const normal = new Uint8Array(size * size * 4);
  const roughness = new Uint8Array(size * size * 4);
  const height = (x: number, y: number) => {
    const xx = (x + size) % size;
    const yy = (y + size) % size;
    const grain = noise(xx, yy) * (kind === 'asphalt' ? 0.004 : 0.002);
    return grain + (kind === 'roof' && xx % 25.6 < 1.1 ? 0.022 : 0);
  };
  const rough = { grass: 0.98, concrete: 0.88, asphalt: 0.91, roof: 0.56 }[kind];
  const n = new THREE.Vector3();
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const index = (y * size + x) * 4;
      n.set(
        ((height(x - 1, y) - height(x + 1, y)) * size) / (2 * meters),
        ((height(x, y - 1) - height(x, y + 1)) * size) / (2 * meters),
        1,
      ).normalize();
      normal.set(
        [(n.x * 0.5 + 0.5) * 255, (n.y * 0.5 + 0.5) * 255, (n.z * 0.5 + 0.5) * 255, 255],
        index,
      );
      const r = Math.round(255 * Math.min(1, rough + (noise(x + 8, y) - 0.5) * 0.06));
      roughness.set([r, r, r, 255], index);
    }
  }
  const dataTexture = (data: Uint8Array) => {
    const texture = new THREE.DataTexture(data, size, size);
    texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
    texture.magFilter = THREE.LinearFilter;
    texture.minFilter = THREE.LinearMipmapLinearFilter;
    texture.generateMipmaps = true;
    texture.anisotropy = 8;
    texture.needsUpdate = true;
    return texture;
  };
  details.set(map, {
    kind,
    meters,
    normal: dataTexture(normal),
    roughness: dataTexture(roughness),
  });
  const composite = (image: CanvasImageSource) => {
    ctx.save();
    ctx.filter = kind === 'grass' ? 'saturate(0.3)' : 'saturate(0.15)';
    ctx.globalAlpha = kind === 'grass' ? 0.32 : kind === 'concrete' ? 0.12 : 0.28;
    ctx.drawImage(image, 0, 0, 512, 512);
    ctx.restore();
    map.needsUpdate = true;
    onLoad?.();
  };
  if (source) composite(source.image as CanvasImageSource);
  else if (kind !== 'roof') {
    let disposed = false;
    map.addEventListener('dispose', () => {
      disposed = true;
    });
    const image = new Image();
    image.onload = () => {
      if (!disposed) composite(image);
    };
    image.src = `/scene/${{ grass: 'aerial_grass_rock', concrete: 'concrete_floor_02', asphalt: 'asphalt_01' }[kind]}.jpg`;
  }
  return map;
}

/** Restrained, authored stand wear at meter scale; no imagery or operational routes. */
export function apronWearTexture(standXs: number[]) {
  const canvas = document.createElement('canvas');
  canvas.width = 1600;
  canvas.height = 720;
  const ctx = canvas.getContext('2d')!;
  const px = (x: number) => (x + 200) * 4;
  const py = (z: number) => (z + 139) * 4;

  // Different pours weather independently. Keep the physical twelve-meter joints readable.
  for (let x = -200; x < 200; x += 12) {
    for (let z = -139; z < 41; z += 12) {
      const variation = noise(x + 300, z + 200);
      ctx.fillStyle = variation > 0.5 ? '#f6eee5' : '#24353c';
      ctx.globalAlpha = Math.abs(variation - 0.5) * 0.075;
      ctx.fillRect(px(x) + 1, py(z) + 1, 46, 46);
    }
  }

  // Repeated pushback paths and parking positions, not freshly painted giant tire stripes.
  for (const x of standXs) {
    for (const offset of [-1.25, 1.25]) {
      ctx.beginPath();
      ctx.moveTo(px(x - 35 + offset), py(-136));
      ctx.bezierCurveTo(
        px(x - 4 + offset),
        py(-132),
        px(x + offset),
        py(-108),
        px(x + offset),
        py(-76),
      );
      ctx.lineTo(px(x + offset), py(-38));
      ctx.strokeStyle = '#253237';
      ctx.globalAlpha = 0.085;
      ctx.lineWidth = 2.8;
      ctx.stroke();
    }
    const stain = ctx.createRadialGradient(px(x), py(-48), 1, px(x), py(-48), 45);
    stain.addColorStop(0, '#26343d24');
    stain.addColorStop(0.45, '#26343d0f');
    stain.addColorStop(1, '#26343d00');
    ctx.globalAlpha = 1;
    ctx.fillStyle = stain;
    ctx.fillRect(px(x) - 45, py(-48) - 45, 90, 90);
  }
  const map = new THREE.CanvasTexture(canvas);
  map.colorSpace = THREE.SRGBColorSpace;
  map.anisotropy = 8;
  return map;
}

export function surfaceMeters(map: THREE.Texture) {
  return details.get(map)?.meters ?? 8;
}

export function surfaceMaterial(map: THREE.Texture | undefined, color: string) {
  const detail = map ? details.get(map) : undefined;
  return new THREE.MeshStandardMaterial({
    color,
    map,
    normalMap: detail?.normal,
    roughnessMap: detail?.roughness,
    roughness: detail ? 1 : 0.94,
    metalness: detail?.kind === 'roof' ? 0.32 : 0,
    envMapIntensity: detail?.kind === 'roof' ? 0.65 : 0.3,
  });
}

export function surfaceUV(geometry: THREE.BufferGeometry, map?: THREE.Texture) {
  if (!map) return;
  const positions = geometry.getAttribute('position');
  const uv = geometry.getAttribute('uv');
  const meters = surfaceMeters(map);
  for (let i = 0; i < uv.count; i++)
    uv.setXY(i, positions.getX(i) / meters, positions.getY(i) / meters);
  uv.needsUpdate = true;
}
