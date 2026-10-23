# pumbastudio prompting lessons

Mistakes the local model (LM Studio, `mcp__pumbastudio__openai_chat`) has made in this repository, and the prompt rule that prevents each one. Paste the **Prompt rules** block into the system message of every code request, and add a row whenever a review rejects part of an answer.

## Prompt rules

```text
- Never touch the real filesystem or environment in code that receives an injected runner or mapping: no Path.exists(), no os.environ, no Path.home(). Use only the injected callables and the given mapping.
- When a parameter defaults to None meaning "use the default", call the default function explicitly in that branch.
- Every name used in a finally block must be bound before the try.
- Tests: pytest functions with tmp_path; never write to /tmp directly; never patch a whole stdlib module (subprocess, os); monkeypatch a single attribute only when the test says so.
- Tests: import every name you use; assert exact values (==), not substrings, unless the task says "contains".
- Keep the test exactly as specified: same name, same inputs, same assertions; do not merge, drop or rename tests.
- Imports: collections.abc for Mapping/Callable, built-in generics (list[str], X | None); no typing.Optional, no `from __future__ import annotations`.
- No step-number comments (# 1., # 2.) and no comments that restate the code.
- subprocess.Popen: always call process.wait() (or communicate()) before reading returncode.
- An except block that maps some exception types must re-raise everything else; never let an exception fall through silently.
- A callback or timer that runs later captures the object it needs in a local variable; never read an attribute another thread may clear.
- Only valid Python: never `from typing import list` or other invented imports.
```

## Log

| Date | Task | What the model did | Rule added |
| :--- | :--- | :--- | :--- |
| 2026-10-23 | Linux installer T1, `setup/state.py` | Imported `Mapping` and `Optional` from `typing`; repeated a condition inside a walrus `if`; dropped assertions from an existing test and forgot an import | Imports rule; keep tests exactly as specified; import every name |
| 2026-10-23 | Linux installer T2, `setup/checks.py` | Checked `candidate.exists()` on the real disk although commands go through an injected runner, so the fake runner never saw the CUDA candidates; ignored `cuda_dirs=None` instead of using the default; used a variable in `finally` that could be unbound | Injected runner rule; default parameter rule; finally rule |
| 2026-10-23 | Linux installer T3, `setup/runner.py` | Wrote `from typing import list \| None`; read `returncode` without `wait()`, so every command looked failed; a broad `except Exception` mapped OSError and silently swallowed everything else; the SIGKILL timer read `self._process` after it had been cleared | Popen wait rule; re-raise rule; capture-in-local rule; valid-imports rule |
| 2026-10-23 | Linux installer T2, tests | Patched the whole `subprocess` module with `unittest.mock`, wrote to `/tmp/test`, and replaced the requested folder test with an unrelated requirement assertion | Tests rules |
