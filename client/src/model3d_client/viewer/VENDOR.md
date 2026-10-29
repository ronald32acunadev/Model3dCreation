# Vendored Three.js

The viewer must work offline, so the Three.js files it needs are copied here instead of loaded from a CDN.

- Package: `three@0.186.1` (MIT, see `vendor/LICENSE`)
- Source: `https://registry.npmjs.org/three/-/three-0.186.1.tgz`
- Tarball sha256: `8cd068708ea44f2c73c944b1cead2ba2f0d5c15c8fc194e5700f4e4f4a033fe7` (sha1 `6d50f70c2c437f844179bbb56d6f5b774e1ca38a`, matches the registry's `shasum`)

Files copied, unchanged:

| From the package | To `vendor/` |
| :--- | :--- |
| `build/three.module.js`, `build/three.core.js`, `LICENSE` | `.` |
| `examples/jsm/controls/OrbitControls.js` | `addons/controls/` |
| `examples/jsm/loaders/GLTFLoader.js` | `addons/loaders/` |
| `examples/jsm/utils/BufferGeometryUtils.js`, `examples/jsm/utils/SkeletonUtils.js` | `addons/utils/` |

`GLTFLoader.js` imports the two utils files. `index.html` maps `three` and `three/addons/` to these files with an import map.

To upgrade, download the new tarball, check its `shasum` against `https://registry.npmjs.org/three/<version>`, copy the same files, update this table, and run `QT_QPA_PLATFORM=wayland uv run pytest -m webengine`.
