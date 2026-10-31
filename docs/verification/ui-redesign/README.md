# UI redesign verification · 31 October 2026

## Integration with latest main

Pulled `main` from `c14b844` to `39923b0` and reapplied the design changes. Resolved
overlaps in the application shell, regional labels, airport scene and stylesheet.
Kept the incoming weather restrictions, atomic reset behavior, passenger
decline/retry flows, dynamic airport context, voice fixes and geographic assets.
Weather panels, empty scenarios and the new passenger controls now use the shared
design system. The pre-pull stash remains as a recovery copy.

- Backend: 241 passed. An initial concurrent run hit the pre-existing dev-script
  startup timing race; the full suite passed when rerun independently.
- Frontend: 105 passed across 12 files; formatting, TypeScript and production build passed.
- No unresolved Git conflicts or whitespace errors.
- Rendered the actual merged workspace and an isolated component fixture for
  restricted/clear weather and passenger action styling. The temporary fixture
  was removed after review; it made no API mutations.
- [Merged workspace](merged-latest-desktop.png)
- [Weather and passenger control rendering](merged-weather-controls.png)

## Brand-reference refinement

The follow-up pass uses the public [OpenAI design guidelines](https://openai.com/brand/)
as a reference for clear hierarchy, generous spacing and precise, approachable
typography. It uses neutral interface surfaces, larger regular-weight headings,
pill-shaped actions and a cool blue-gray architectural scene. Recovery Live's
identity and the locally bundled Inter typeface remain in use.

The refined pass passed all 97 frontend tests and the production/TypeScript build.
Desktop and 390 × 844 phone renders were inspected, with no horizontal overflow
or browser console errors. Current screenshots:

- [Refined desktop workspace](brand-refinement-desktop.png)
- [Refined network](brand-refinement-network.png)
- [Refined mobile workspace](brand-refinement-mobile.png)

## Initial redesign

The operations workspace, architectural airport, regional network, inspectors,
dialogs, resource schedule, command composer, geographic-map controls and
passenger journey share a new warm, restrained design system. Styles are split
by surface under `frontend/src/styles/`; the airport is built from instanced
geometry in `frontend/src/scene/studioAirport.ts`.

## Executed checks

- Backend suite: 183 passed, including solver, reservation and API checks.
- Frontend suite: 97 passed across 11 files.
- Production build and TypeScript compilation: passed.
- Prettier and `git diff --check`: passed.
- Visual review: desktop 1492 × 788, tablet 900 × 800, phone 390 × 844.
- Browser flow: solver recovery → passenger group → offer page → accepted
  itinerary. Four synthetic passengers became confirmed and the operations
  summary synchronized to 116 offered / 4 confirmed.
- Resource search and selection, responsive inspector, condition editor,
  settings, keyboard dismissal and search focus verified in the browser.
- Airport and network scenes rendered; missing Google credentials produced the
  explicit provider error and a working return to the model.
- No browser console errors after the final clean desktop reload. Editing the
  application entry point during HMR had emitted duplicate-root warnings before
  that reload.

## Evidence

- [Desktop airport](operations-desktop.png)
- [Desktop network](network-desktop.png)
- [Mobile operations](operations-mobile.png)
- [Mobile passenger confirmation](passenger-mobile.png)

These captures use synthetic operational data. The local solver was used;
OpenAI, voice and Google Maps credentials were not configured in this local
session, so real-provider operation was not revalidated. Existing authored GLB
assets used by Google geographic rendering were preserved. The lazily loaded
Three.js scene still produces Vite's large-chunk advisory (about 969 kB before
gzip); the production build succeeds.

## Dark mode — 31 October 2026

Added a sun/moon toggle to the operations header, boarding pages and operator
login. The initial appearance follows the operating system; explicit choices
persist locally and synchronize between tabs. Storage-disabled browsers keep
an in-memory choice. A small head bootstrap applies the preference before the
application loads.

Shared semantic colors cover the resource rail, inspectors, dialogs, schedule,
weather, geographic-map controls, errors and passenger itinerary. The airport
has dark ground materials, adjusted glazing and cooler lighting; the regional
terrain, grid and labels follow the same theme. Camera and operational state
are independent of appearance. QR scan surfaces remain white.

Validation:

- 105 frontend tests passed; TypeScript and production build passed.
- Prettier and `git diff --check` passed.
- Verified light/dark switching, saved preference after reload and cross-tab
  synchronization between operations and boarding views.
- Reviewed desktop airport, regional network, connections dialog and passenger
  inspector, plus mobile operations and boarding at 390 × 844. Mobile content
  width matched the viewport with no horizontal overflow.
- No browser console errors during the checks. Existing synthetic state stayed
  at version 6; no offers or backend operations were changed.
- The existing Three.js bundle-size advisory remains (approximately 970 kB).
  Google provider imagery was not verified because no Maps key is configured.

Evidence: [desktop](dark-mode-desktop.png), [network](dark-mode-network.png),
[mobile](dark-mode-mobile.png), [passenger](dark-mode-passenger.png), and
[light comparison](dark-mode-light-check.png).
