# App default generator

Status: **complete**. Branch: `fix/dark-trellis-models`.

Approved 2026-11-04 in chat ("Yes, fix it here"), in answer to "Should the app always start the engine with TRELLIS.2, without needing the variable?". There is one story, implemented directly.

On 2026-11-04 a model looked much worse than one made on 2026-11-02 from the same image. Its job had run on Hunyuan3D: `EngineSupervisor` passes only `MODEL3D_HOME`, so an engine started by the app falls back to the engine default `hunyuan` unless the user exports `MODEL3D_GENERATOR=trellis2`.

## US-01: Start the engine with TRELLIS.2 when it is installed

As a user, I want the app to start the engine with TRELLIS.2 whenever TRELLIS.2 is installed, so that I get its models without remembering an environment variable.

- Scope: `client/src/model3d_client/paths.py` (path of TRELLIS.2's Python) and `client/src/model3d_client/services/supervisor.py` (`_spawn`).
- Out of scope:
  - The engine's own default (`hunyuan`) and its config validation.
  - An engine that is already running, which the app uses as it is.
- **US-01 / AC-01:** Given `<repo>/vendor/TRELLIS.2/.venv/bin/python` exists and `MODEL3D_GENERATOR` is not set, when the app starts the engine, then the engine process gets `MODEL3D_GENERATOR=trellis2`.
- **US-01 / AC-02:** Given `MODEL3D_GENERATOR` is set (for example `hunyuan`), when the app starts the engine, then the engine gets that value unchanged.
- **US-01 / AC-03:** Given TRELLIS.2 is not installed and `MODEL3D_GENERATOR` is not set, when the app starts the engine, then `MODEL3D_GENERATOR` is not set for it, so the engine uses `hunyuan`.

## Verification mapping

| Story / criterion | Check | Observed result |
| --- | --- | --- |
| US-01 / AC-01 | `client/tests/test_supervisor.py::test_engine_uses_trellis_when_installed` | Passed. RED: `'GENERATOR=trellis2' in 'GENERATOR=unset'` failed; GREEN after the change. |
| US-01 / AC-02 | `client/tests/test_supervisor.py::test_engine_keeps_explicit_generator` | Passed before and after (guards existing behavior). |
| US-01 / AC-03 | `client/tests/test_supervisor.py::test_engine_without_trellis_gets_no_generator` | Passed before and after (guards existing behavior). |
| Regression: `MODEL3D_HOME` still passed | `test_engine_gets_model3d_home` and the default suite | Passed: 284 passed. Not yet run end to end with plain `client/launch.sh`, because a job was running. |
