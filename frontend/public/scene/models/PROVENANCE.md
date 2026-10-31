# Authored overlay models

`aircraft.glb` and `bus.glb` are original Airport Recovery Live procedural geometry, generated from `frontend/src/scene/geometry.ts`. They contain no downloaded meshes, textures, airline branding or external assets. The project authors can use and modify them with this application; they inherit the application's license if one is specified. No separate third-party model license applies.

Rebuild from the frontend directory with Node 22.18+ (native TypeScript stripping):

```sh
node src/scene/export-models.mjs
```

Coordinates are meters, **Y up**, **negative Z forward** (aircraft nose / bus windscreen). Ground-contact wheels reach approximately Y=0. The GLB files use glTF 2.0 core PBR materials, with baked transforms and no extensions. A generated manifest gives exact bounds and byte sizes.

These are representative visualization models, not reproductions of a named aircraft type or operationally accurate vehicle geometry.
