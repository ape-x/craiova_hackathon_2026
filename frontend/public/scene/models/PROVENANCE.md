# Authored overlay models

These are original Airport Recovery Live procedural assets. Aircraft, bus and terminal geometry comes from `frontend/src/scene/geometry.ts`; passenger figures match the geometry and spacing of `PassengerCluster` in `Scene.tsx`. They contain no downloaded meshes, textures, airline branding or external assets. They inherit the application's license if one is specified. No separate third-party model license applies.

Rebuild from the frontend directory with Node 22.18+ (native TypeScript stripping):

```sh
node src/scene/export-models.mjs
```

Coordinates are meters, **Y up**, **negative Z forward** (aircraft nose / bus windscreen). Ground-contact wheels reach approximately Y=0. The GLB files use glTF 2.0 core PBR materials, baked transforms, and no extensions. Physical-material clearcoat is reduced to core PBR while base color, roughness, metalness, emissive color and opacity are retained. The generated manifest records exact bounds, byte sizes, SHA-256 hashes and anchors.

| Asset              | Dimensions: width × height × length | Native placement / meaning                                                     |
| ------------------ | ----------------------------------- | ------------------------------------------------------------------------------ |
| `aircraft.glb`     | 34.192 × 13.078 × 36.800 m          | Original vehicle origin; nose −Z                                               |
| `bus.glb`          | 3.533 × 3.505 × 12.810 m            | Original vehicle origin; front −Z                                              |
| `terminal.glb`     | 220.000 × 20.300 × 118.965 m        | Place at native `[0, 0, 51]`; source translated by `[0, 0, −51]` before baking |
| `passengers-1.glb` | 0.945 × 1.625 × 0.520 m             | One figure; first person's ground origin                                       |
| `passengers-2.glb` | 2.345 × 1.625 × 0.520 m             | Two figures, same origin                                                       |
| `passengers-3.glb` | 3.745 × 1.625 × 0.520 m             | Three figures, same origin                                                     |
| `passengers-4.glb` | 3.745 × 1.625 × 2.020 m             | Four figures in two rows, same origin                                          |
| `passengers-5.glb` | 3.745 × 1.625 × 2.020 m             | Five figures in two rows, same origin                                          |

The terminal is the existing authored building, glazed concourse, jet bridges, roof equipment and entrance canopy. Its public entrance faces +Z and its airside faces −Z. It excludes the native terrain, apron, pedestrian ground slab, road network, runway, ground markings and independent service vehicles. It must remain labeled an authored simulation overlay, not a reconstruction of the real terminal. No geographic imagery is included or replaced.

Passenger assets retain the native arrangement: X spacing 1.4 m, second row at Z=1.5 m. Choose `passengers-${Math.min(size, 5)}.glb` for a positive group size, preserving the native five-figure cap; larger groups still need their actual count in the UI. A 5 mm vertical correction places the feet at Y=0. There is no full-scene/group-position offset baked into these files.

Google's model documentation requires `.glb` and core glTF PBR without extensions. Asset axes above describe the file; the map's heading/tilt/roll must separately align it with Earth's local axes. The Google adapter's orientation and actual GPU rendering require browser verification; a successful local parse is not proof of provider rendering. Sources: [models guide](https://developers.google.com/maps/documentation/javascript/3d/models), [Model3DElement reference](https://developers.google.com/maps/documentation/javascript/reference/3d-map-draw#Model3DElement), [Orientation3D reference](https://developers.google.com/maps/documentation/javascript/reference/coordinates#Orientation3D).

The exporter reuses the terminal recipe between explicit native-source markers and stops if those markers change. It uses a local Node `FileReader` shim, no browser/canvas/offscreen renderer, and no network. Each asset must pass GLB v2/header, core-PBR/no-extension, self-contained, under-2 MB, finite-vertex and `GLTFLoader` bounds-roundtrip checks before the export is considered successful.

These are representative visualization models, not surveyed structures, reproductions of a named aircraft type, or operationally accurate vehicle geometry.
