# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

## What this repository is

A template repository, not an application. It holds the agent rule files that get copied into new projects (modeled after the sibling `llmtuner` project). There is no source code, package manifest, build, linter, or test suite, so there are no build/lint/test commands to run. Every tracked file is Markdown.

## Layout and the sync invariant

- `AGENTS.md` — the canonical agent rules (language rule, local-LLM delegation hierarchy, workflow, MCP setup reference). Distributed via "Option A" in the README.
- `.agents/rules/AGENTS.md` — a byte-identical copy of the root `AGENTS.md`, distributed via "Option B" (the `.agents/` customization folder).
- `README.md` — summarizes the same rules and documents the two copy options.

The two `AGENTS.md` files must stay identical, and the README summary must stay consistent with them. Any rule change touches all three files in the same commit. Verify with:

```bash
git diff --no-index AGENTS.md .agents/rules/AGENTS.md
```

No output means they are in sync.

## Working in this repository

- The delegation rule in `AGENTS.md` targets source code, tests, and scripts. Documentation, configuration, and commit messages are written by the agent itself, which covers everything currently in this repository. If executable code is ever added, the delegation hierarchy applies; in Claude Code the tools are `mcp__pumbastudio__openai_chat` (primary) and `mcp__pumbalama__chat_completion` (secondary).
- Rules written here are consumed by several agents (Antigravity, Claude Code, Codex, Cursor, OpenCode). Keep `AGENTS.md` agent-neutral; Claude-specific guidance belongs in this file.
- Do not hardcode model names in the rules: the dynamic model policy is deliberate (see commit `efabf70`).
- Commits follow Conventional Commits (`feat:`, `docs:`, ...).

## Untracked directories

- `Agents/readme-generator.md` — a subagent definition (Claude Code frontmatter: `name`, `description`, `model`, `color`). It is not under `.claude/agents/`, so Claude Code does not load it from here; it is a template asset and is not yet committed or mentioned in the README.
- `.atl/` and `.codegraph/` — locally generated tooling state (skill registry, code index). There is no `.gitignore` yet, so do not stage them with a blanket `git add`.
