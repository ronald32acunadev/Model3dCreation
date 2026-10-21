# Hardware-adaptive engine

Objective: the engine detects each person's NVIDIA GPUs and picks one of five run modes. Branch: `claude/peaceful-dirac-89df24`. Delivery: ask-on-risk. Test runner: `cd engine && uv run --no-sync pytest -q` (GPU: `-m gpu`).
Design: `docs/superpowers/specs/2026-10-21-hardware-adaptive-engine-design.md`. Plan: `docs/superpowers/plans/2026-10-21-hardware-adaptive-engine.md`.

## Specs

- S1: "Di que se adapte al hardware que tiene cada persona": detect GPUs at startup and choose a mode automatically (design Goals 1-2).
- S2: The five modes `dual_gpu`, `single_gpu_resident`, `single_gpu_swap`, `shape_only`, `unsupported`, chosen from measured VRAM with a 512 MiB margin (owner decision, L4), sized for `final` (design decisions 1, 11-14).
- S3: Health reports `mode` (`name`, `texture`, `reason`) and per-GPU free VRAM; `texture_unavailable` (400) and `mode_unavailable` (health error) (design decisions 5-7, 15-16).
- S4: `MODEL3D_MODE`, optional `MODEL3D_SHAPE_BUS`/`MODEL3D_PAINT_BUS` (both or neither) (design Goal 4, decision 8).
- S5: The owner's machine keeps `dual_gpu` and today's speed; in `dual_gpu`, paint goes to the GPU with the most free VRAM and shape to the next (owner decision, L5).
- S6: Docs describe modes, settings, codes, and health fields.

## Tasks

| ID | Specs | Route | Status | Commit |
| :--- | :--- | :--- | :--- | :--- |
| T1 measure VRAM | S2, S5 | inline | done | d1dc635 |
| T2 GPU probe | S1 | inline | done | be96fad |
| T3 mode selection | S2, S5 | inline | done | 366869b |
| T4 settings | S4 | inline | done | 3aad4bb |
| T5 swap scheduling | S2 | inline | done | 9d12099 |
| T6 wiring, health, API, docs | S1, S3, S6 | inline | done | c125477, ee3f770 |
| T7 GPU acceptance | S5 | inline | done | 53dad49 |

Route evidence: owner chose native (inline) execution; no Writer trigger (units depend on each other's interfaces).

## Log

- L1 (2026-10-21): "Di que se adapte al hardware que tiene cada persona" — after "Ok comencemos con la fase3" and "pienso compartir la app". Owner chose engine adaptation first ("1"), then "ok haz laS 2" (both: adaptive engine, then the desktop app), "TODO SE VE PERFECTO" on the five modes, "Si lo apruebo" on the design, native execution.
- L2: Local LLM providers unavailable at start (LM Studio not answering, Ollama with no model loaded); host model writes the code per AGENTS.md fallback.
- L3: Engram mirror pending (mem_save hook error: host session already ended); resync when available.
- L4 (2026-10-21): T1 measured paint peak 10168 MiB vs GPU1 idle free 11173 MiB, so a 1 GiB margin would move paint off the desktop card. Owner: "CReo que 512 esta bien". Margin is 512 MiB.
- L5 (2026-10-21): with desktop apps open, GPU1 fell below paint's threshold and paint moved to GPU0 (x2). Owner: "Utilizas las 2 no ?", "o la que este mas libre", "Correcto la gpu mas libre seria lo mejor". Dual placement rule changes from fastest link to most free VRAM (link breaks ties).
- L6 (2026-10-21): GPU acceptance 2/2 passed. dual_gpu (shape 29:00.0, paint 25:00.0): load 51.1 s, shape 79.5 s, paint 69.8 s. single_gpu_swap on 25:00.0: load 34.5 s, shape+swap 102.0 s, paint 70.5 s, textured GLB. Earlier run with old test: paint on x2 73.9 s vs ~60 s on x16.
- L7 (2026-10-21): final review. Code reviewer: with fixes. Native RDD review-14c124b33559c4e6 on 9170a92..53dad49 (risk, resilience, reliability): approved, acknowledged. Fix pass 8465bfa: swap double load failure now reports worker_unavailable in health and fails queued jobs; non-StageError load errors no longer kill the swap loop; Review Focus 4 test added; docs state the probe delays the port by a few seconds. RDD assess of 8465bfa: medium, under_budget (pending in slice). Unit suite 116 passed; GPU suite 2 passed. Deferred minors are listed in the ledger summary given to the owner. Next: owner decides merge/push; then phase 3 desktop app.
