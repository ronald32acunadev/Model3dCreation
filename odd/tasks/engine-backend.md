# Engine backend (phase 2)

Objective: the local FastAPI engine with the two-stage dual-GPU pipeline, per `docs/superpowers/plans/2026-10-20-engine-backend.md`.
Branch: `feature/engine-foundation`. Delivery: ask-on-risk. Test runner: pytest via uv (`engine/`, `contracts/`).

## Specs

- S1 (L1): "si, commitea y pasemos a la fase 2"
- S2: implement `docs/architecture.md` sections 1, 2, 4, 5 and its paint runtime rules, as decomposed in the plan's Tasks 1-10 and Global Constraints.
- S3 (phase 1 review `review-38b7a0ed30ea86e6`, advisory): fix the three reliability warnings in `scripts/verify_gpus.py` (plan Task 2).

## Tasks

- [x] T1 (S2) contracts package — inline — e949f6f
- [x] T2 (S2, S3) engine package, settings, topology, review warnings — inline — 63200c2
- [x] T3 (S2) job store and state machine — inline — 4d76cfd
- [x] T4 (S2) event bus — inline — 384d84e
- [x] T5 (S2) scheduler — inline — 4c3c8b1
- [x] T6 (S2) REST API — inline — c7cc34f
- [x] T7 (S2) WebSocket — inline — 6be5425
- [x] T8 (S2) process stage runner — inline — d2a5da3
- [x] T9 (S2) Hunyuan3D stages (GPU) — inline — ef7db3d
- [x] T10 (S2) entrypoint and docs — inline — bffb9c8
- [x] Final review fixes — inline — f2f37c5

## Log

- L1 (2026-10-20): "si, commitea y pasemos a la fase 2". Phases 0-1 committed in six work units on `feature/engine-foundation` (350926f..97010b1).
- L2: native review of the full range stopped with `lens_context_budget_exceeded` (21 files, 3353 lines, mostly `uv.lock` and docs). Reduced candidate 25aa187..HEAD (verification script, 451 lines): owner granted, risk lens no findings, reliability lens 3 warnings + 2 suggestions (advisory), approved and acknowledged (authority burned).
- L3: plan written at `docs/superpowers/plans/2026-10-20-engine-backend.md`; forecast about 1,800 authored lines, so delivery will need chained slices.
- L4 (2026-10-21): owner chose native execution ("si, vamos con nativo"). Code went to pumbalama first (pumbastudio had no model loaded); several answers needed agent rewrites, recorded per task in the plan ledger.
- L5: Task 9 first end-to-end run hit GPU0 OOM (shape worker holds 8.74 GiB; paint helpers needed 2.69 GiB). DINOv2 moved to the CPU, VAE decode stays on the paint GPU with slicing; architecture.md updated. GPU test passed twice: load 50.8 s, shape 77.4 s, paint 60.5 s.
- L6: real API run: curl job succeeded in 2 min 20 s, result 200 model/gltf-binary; render docs/acceptance/2026-10-21-engine-api.png.
- L7: owner skipped the native whole-branch reviews (too large for the reviewer budget). Independent opus review: 1 Critical (job behind a crash failed engine_not_ready), 3 Important (startup 503 unreachable, failed reload silent, blocking health), all fixed with RED→GREEN tests in f2f37c5; suite 73 engine + 12 contracts.
- Deferred minors: cancelling a shape-done job waiting for paint is delayed; EventBus keeps final events forever; closed WebSocket noticed only at next send; shutdown mid-task leaves job.json in a running state.
- Next: owner decides delivery of feature/engine-foundation (merge, PR, or keep) and phase 3.
