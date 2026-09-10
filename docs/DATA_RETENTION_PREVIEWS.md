# Capacity planning and retention previews

This milestone adds observation and audit, **not automatic retention or deletion**.
Migration `c319f5d8b642` adds `retention_preview` only; no existing sample is modified.

## API and permissions

- `GET /api/v1/data-lifecycle/capacity`: admin only; reads PostgreSQL database and sample relation/index sizes, statistics-estimated live rows, and last analyze time. Daily growth uses a caller-supplied planning rate, not observed throughput. Unknown per-row estimates remain null. Windows host free space is deliberately not inferred from container storage.
- `POST /api/v1/data-lifecycle/previews`: admin or authorized engineer; select one plant, one source (`opcua` or `file`), and 1–3650 days. Scope uses tag → device → line → plant. `file` means imported samples, not source files.
- `GET /api/v1/data-lifecycle/previews?plant_id=...` and `/previews/{id}`: authorized readers, including observers. List returns the latest 20 audits for that plant.

## Semantics and cost boundaries

The cutoff uses UTC source sampling time, not received/upload time. A scope-specific maximum ID bounds the observation against subsequently allocated IDs. Rows are selected by ascending ID, up to 100,001 candidates: at most 100,000 is an exact count; 100,001 means a lower bound. Distinct tag count and min/max timestamps cover the scanned candidates only, not the whole remaining population. Large source backfills can therefore be old even when received today.

Each preview SQL statement has a transaction-local five-second timeout. The candidate limit bounds returned/aggregated rows, **not all database work**; finding candidates or the high-water ID can still hit the timeout. Do not promise constant-time queries or a five-second whole-request SLA. Query cancellation returns 503 and rolls back the audit transaction. Permissions and active account status are checked again before committing the audit.

This is not an immutable archive manifest: concurrent changes, asset reassignment, or later backfills require renewed scope validation before any future archive/cleanup operation. There is no delete endpoint. A saved preview does not authorize deletion.

## UI

The panel is below the import workflow so it does not interrupt upload/mapping. Capacity is global and admin-only. Preview history and mutations are keyed by the selected plant; switching plants remounts local planning state. Time labels explicitly use Beijing time. The UI distinguishes estimates, lower bounds, and preview-only results.

## Verification and remaining work

Backend tests cover source/time/plant isolation, no sample mutation, count caps, empty scope, audit persistence, role checks, late permission revocation, input validation and cancellation. The browser regression covers persistence, invalid retention input and plant switching.

Still required for the broader lifecycle goal: archive format and checksum verification, recoverable restore, bounded cleanup with explicit scope confirmation and fresh authorization, enforcement/scheduling and capacity alerts. Do not report this preview milestone as a complete retention service or as freed disk space.
