# Use both GPUs' memory as one pool

Status: approved 2026-11-12 ("Approve, all stories"). US-01 and US-03's bake overflow done; US-02 and the retry-on-other-card path pending; US-04 done. Branch: `feat/public-api-voice`. Builds on `docs/userstories/sharper-body-texture/` (US-05: "high" at 1536 with 500 000 faces).

## Context

Requests (verbatim, 2026-11-12): "Quiero decir son 24 gb , las gpus pueden intercambiarse informacion , como si fueran rams en una pc , una pc puede tener 4 slots de ram utilizados y esto se comparte", then "AL finnnnnnnnnnnnn, asi no vamos a llegar tener vram overfload  , no importa si dura mas si tenemos la potencia para ejecutarlo si  estoy tratando de ejecutar algo en una gpu que solo me da 12gb  me quedo sin 12 gb que puede utilizar mas aunque vaya mas lento se esta desaprovechando mucho".

### Facts checked (2026-11-12)

- Two RTX 5070, 12 227 MiB each. `nvidia-smi topo -m`: the cards connect through the CPU's host bridge (PHB); direct GPU-to-GPU access (P2P) is "CNS", not supported by the chipset. RTX 5070 has no NVLink. Data between the cards goes through system RAM.
- PCIe links: bus `29:00.0` up to 3.0 x16; bus `25:00.0` up to 2.0 x16 (its slot or riser caps it), several times slower than the other card's.
- The machine has 82 GB of RAM.
- TRELLIS.2 runs with `low_vram`: each sub-model moves from RAM to the card for its step and back. At 1536, 150 000 to 500 000 faces and 4096 px it peaks at 5.6 GB (generation card) and 6.6 GB (decoding card). 500 000 or 1 000 000 faces at 8192 px ran out of memory on the decoding card while baking (11.1 of 12 GB), and the job failed although the other card and the RAM had room.

### Goal

No job fails for lack of one card's memory while the other card or the RAM has room: work spreads over both cards, and a step that does not fit moves what it can elsewhere and goes on, slower if it must. Out of scope: GPU-to-GPU direct memory (the chipset does not allow it), new hardware.

## US-01: Find where the memory goes (spike)

As the owner, I want to know which step needs the memory and why, so that the fix moves the right things.

- US-01 / AC-01: For 1536 with 1 000 000 faces and 8192 px, the peak of each step on each card (generation, decoding, remeshing, baking) is recorded here, with what holds the memory at the failing step.
- US-01 / AC-02: Whether that step can run in pieces (for example the texture baked in quadrants) or with its data in RAM is checked in TRELLIS.2's code (`o_voxel`, `cumesh`) and recorded here with the chosen way.

## US-02: Keep TRELLIS.2's sub-models resident across both cards

As a user, I want both cards to hold TRELLIS.2 at once, so that their 24 GB work together instead of reloading pieces at every step.

- US-02 / AC-01: The worker places the sub-models across the two cards so that each stays loaded between jobs; between steps only intermediate data crosses from one card to the other.
- US-02 / AC-02: The time and the peak of each card for a "high" job are recorded here against today's (95 to 112 s, 5.6 and 6.6 GB).

## US-03: Overflow instead of failing

As a user, I want a step that does not fit on one card to go on with the other card or the RAM, so that the best quality finishes even if it takes longer.

- US-03 / AC-01: When a step runs out of memory, the worker frees that card (moving other sub-models to the other card or to RAM) and retries it, then runs it in pieces (US-01's way) if it still does not fit; the job fails only when none of these fits, with the reason.
- US-03 / AC-02: 1536 with 1 000 000 faces and 8192 px, which fails today, finishes; its time and peaks are recorded here.
- US-03 / AC-03: A test with a stub step that runs out of memory once checks the retry path and the final error path.

## US-04: Best quality by default

As a user, I want "high" to use what the pool now allows, so that I always get the best my machine can do.

- US-04 / AC-01: "High" uses the largest faces and texture size US-03 finishes, with the evaluation set's sharpness and renders compared with today's; the owner confirms it.

## Results (2026-11-12)

- US-01: the failing allocation at 8192 px was exactly 1024 MiB, one 8192 x 8192 float4 buffer: `o_voxel.postprocess.to_glb` rasterizes the whole texture at once and holds several full-size float buffers on the card (rasterization 1 GiB plus a 1 GiB copy per face chunk, 3D positions 0.8 GiB, attributes 1.6 GiB) beside the high-resolution mesh, its BVH and the color volume. The bake can run in pieces: each texel's color depends only on its own triangle, so tiles of the texture are independent.
- US-03 (bake): `engine/src/model3d_engine/stages/trellis_bake.py` is TRELLIS.2's `to_glb` with the bake done in tiles of at most 4096 px (`bake_tiles.py`), each tile's texels moved to RAM before the next. The worker uses it for every job.

| `1536_cascade` with | Before (one-piece bake) | Tiled bake |
| --- | --- | --- |
| 500 000 faces, 8192 px | out of memory (11.1 GB) | done in 175 s, 486 228 faces, 115 MB; peaks 5.4 / 6.6 GB |
| 1 000 000 faces, 8192 px | out of memory (11.1 GB) | done in 234 s, 975 401 faces, 137 MB; peaks 5.4 / 6.6 GB |

- Face close-ups against 500 000 faces at 4096 px: 8192 px shows no visible gain (the colors come from TRELLIS.2's 1536-cell color grid, which 4096 px already holds); 1 000 000 faces sharpens the hair and ears a little.

- US-04 (2026-11-12): the owner chose "1M faces, 8192 px". Split across two cards, "high" now asks for `1536_cascade`, 1 000 000 faces and an 8192 px texture (`stages/trellis.py`: `SPLIT_HIGH_FACES`, `SPLIT_HIGH_TEXTURE`). UniRig rigged the 975 401-face model (textured meshes are not decimated) in 183 s with a 5.5 GB peak, keeping 975 320 faces.

## Dependencies

US-01 → US-02 → US-03 → US-04.

## Verification mapping

| Story / criterion | Check | Observed result |
| --- | --- | --- |
| US-01 / AC-01..AC-02 | failing allocation size; code reading | Passed (Results) |
| US-02 / AC-01..AC-02 | worker test with stub sub-models; GPU run times and peaks | Pending |
| US-03 / AC-01..AC-03 | `engine/tests/test_bake_tiles.py` (tiles land every texel where the whole bake would); GPU runs at 500 000 and 1 000 000 faces, 8192 px | Bake overflow passed (engine suite 414 passed); the retry-on-other-card path pending |
| US-04 / AC-01 | face close-ups at 4096 / 8192 px and 500 000 / 1 000 000 faces; owner's choice; `engine/tests/test_trellis_stage.py::test_high_quality_keeps_1m_faces_across_two_cards`, `::test_high_quality_bakes_8192_px_across_two_cards`; UniRig on 1 000 000 faces | Passed (engine suite 417 passed); a run in the app pending |
