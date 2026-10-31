# Airport Recovery Live

Standalone FastAPI + React/TypeScript application. Do not edit the sibling Notariat project.
Product requirements: docs/product-spec.md. Visual references: docs/mockups/.

- External flight observations and simulated operations are separate. Never label synthetic data or deterministic decisions as live AI.
- Backend is authoritative. Allocation, family integrity, temporal continuity, budget, accessibility and all-resource reservations are validated server-side.
- API/provider secrets stay on the server. Browser Maps key is explicitly public and must use origin/API restrictions.
- API failures stay visible; no silent switch to scripted AI responses.
- Run meaningful solver, reservation, API and frontend checks. Verify rendered UI against mockups.
- Preserve concurrent contributors' work; components have explicit ownership during development.
