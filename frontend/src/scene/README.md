# Native operational scene

`Scene.tsx` receives the authoritative `World`, `mode`, `selectedId` and `onSelect`. The root application owns state changes and the geographic-map toggle.

- Local procedural geometry is an architectural illustration, not surveyed Craiova geometry or photogrammetry. Materials are bundled locally; sources and CC0 terms are listed in `public/scene/ASSET-LICENSES.md`.
- Airport mode shows representative stands, six ground vehicles and a representative crowd. Selecting any passenger group shows that group even if it is outside the initial crowd subset. Backend counts remain authoritative.
- Held itineraries have dashed amber lines. Confirmed itineraries have continuous mint lines. A confirmation does not advance a bus: vehicle movement is driven by `world.sim_time` and its departure/arrival interval.
- Unavailable resources become red. No AI or operational mutation is implemented inside the renderer.
- Region mode uses real airport coordinates from `World`. Elevation, road styling, city footprints and terrain are illustrative. External aircraft observations remain distinct from scenario trajectories and identify their source.
- Native Google geographic rendering lives separately under `src/maps/` and requires the public restricted browser key.
- Rendering uses demand frames, finite camera transitions, reduced-motion support, instanced airport structures/vegetation, bounded DPR and no external environment-map fetch.
- Airport surfaces use meter-scaled UVs, authored normal/roughness detail and restrained local photographic albedo. Concrete no longer stretches one high-contrast material patch across an apron-sized area. Outdoor sky reflections are generated locally; directional shadows have a tighter airport footprint.
- Projected labels share the application's DOM tree through an external store; there are no per-label React roots. Asset release defers until React StrictMode has had a chance to reattach.

Known visual limitation: the authored architectural model does not reproduce the photographic detail of the Imagegen reference mockups. The reference images are not used as backgrounds or textures.
