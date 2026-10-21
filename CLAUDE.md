# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

## What this repository is

It started as a template holding agent rule files and is becoming the local AI 3D-generation engine described in `Fases/` (see "Project Context" in `AGENTS.md`). The verified design, which overrides `Fases/` where they differ, is `docs/architecture.md`; environment setup is `docs/setup.md`. Phase progress is tracked in `odd/tasks/`.

## Commands

The engine is a uv project in `engine/` (Python 3.11, PyTorch 2.9.1+cu130). Hunyuan3D 2.1 is a git-ignored clone in `vendor/`, and weights live in `~/.local/share/model3d/hf` (set `HF_HOME` to it).

```bash
cd engine && uv run pytest                         # unit tests, no GPU
cd engine && uv run pytest -m gpu                  # needs both GPUs and the compiled extensions
cd engine && uv run pytest tests/test_verify_gpus.py::test_parse_nvcc_version   # single test
uv run --project engine python scripts/verify_gpus.py   # full environment check, from the repo root
```

`uv sync` rebuilds `custom_rasterizer` from `vendor/`, so it needs `CUDA_HOME=/usr/local/cuda-13.1` (see `docs/setup.md`). Before GPU work, check that LM Studio or Ollama is not holding VRAM.

## Rule files and the sync invariant

- `AGENTS.md` is canonical and agent-neutral (Antigravity, Claude Code, Codex, Cursor, OpenCode). Claude-specific guidance belongs here.
- `.agents/rules/AGENTS.md` is a byte-identical copy, and `README.md` summarizes the rules. Any rule change touches all three in the same commit. Verify with:

```bash
git diff --no-index AGENTS.md .agents/rules/AGENTS.md
```

No output means they are in sync.

- Do not hardcode model names in the rules: the dynamic model policy is deliberate (commit `efabf70`).
- `Agents/readme-generator.md` is a template subagent. It lives outside `.claude/agents/`, so Claude Code does not load it from here.

## Code delegation in Claude Code

The delegation rule in `AGENTS.md` covers source code, tests, and scripts; docs, config, and commit messages are written by the agent. In Claude Code the tools are `mcp__pumbastudio__openai_chat` (primary) and `mcp__pumbalama__chat_completion` (secondary).
