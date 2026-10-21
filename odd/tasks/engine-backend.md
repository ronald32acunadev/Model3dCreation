# Engine backend (phase 2)

Objective: the local FastAPI engine with the two-stage dual-GPU pipeline, per `docs/superpowers/plans/2026-10-20-engine-backend.md`.
Branch: `feature/engine-foundation`. Delivery: ask-on-risk. Test runner: pytest via uv (`engine/`, `contracts/`).

## Specs

- S1 (L1): "si, commitea y pasemos a la fase 2"
- S2: implement `docs/architecture.md` sections 1, 2, 4, 5 and its paint runtime rules, as decomposed in the plan's Tasks 1-10 and Global Constraints.
- S3 (phase 1 review `review-38b7a0ed30ea86e6`, advisory): fix the three reliability warnings in `scripts/verify_gpus.py` (plan Task 2).

## Tasks

- [ ] T1 (S2) contracts package — route pending owner's execution choice
- [ ] T2 (S2, S3) engine package, settings, topology
- [ ] T3 (S2) job store and state machine
- [ ] T4 (S2) event bus
- [ ] T5 (S2) scheduler
- [ ] T6 (S2) REST API
- [ ] T7 (S2) WebSocket
- [ ] T8 (S2) process stage runner
- [ ] T9 (S2) Hunyuan3D stages (GPU)
- [ ] T10 (S2) entrypoint and docs

## Log

- L1 (2026-10-20): "si, commitea y pasemos a la fase 2". Phases 0-1 committed in six work units on `feature/engine-foundation` (350926f..97010b1).
- L2: native review of the full range stopped with `lens_context_budget_exceeded` (21 files, 3353 lines, mostly `uv.lock` and docs). Reduced candidate 25aa187..HEAD (verification script, 451 lines): owner granted, risk lens no findings, reliability lens 3 warnings + 2 suggestions (advisory), approved and acknowledged (authority burned).
- L3: plan written at `docs/superpowers/plans/2026-10-20-engine-backend.md`; forecast about 1,800 authored lines, so delivery will need chained slices.
- Next: owner reviews the plan and picks the execution method.
