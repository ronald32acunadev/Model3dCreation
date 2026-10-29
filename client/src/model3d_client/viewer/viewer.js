// Three.js viewer driven from Python through window.viewer.
// Errors are reported as console messages prefixed with "model3d-viewer-error: ".
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

let renderer;
try {
  renderer = new THREE.WebGLRenderer({ antialias: true });
} catch (error) {
  console.error('model3d-viewer-error: webgl_unavailable');
  throw error;
}
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.outputColorSpace = THREE.SRGBColorSpace;
document.body.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x2b2d31);
scene.add(new THREE.HemisphereLight(0xffffff, 0x444444, 2.0));
const sun = new THREE.DirectionalLight(0xffffff, 2.0);
sun.position.set(3, 5, 4);
scene.add(sun);

const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.01, 1000);
camera.position.set(0, 0, 3);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

const loader = new GLTFLoader();
const grey = new THREE.MeshStandardMaterial({ color: 0xb0b0b0, roughness: 0.8, metalness: 0.0 });
let model = null;
let originals = new Map();  // mesh -> material it was loaded with
let textured = true;
let mixer = null;          // AnimationMixer for the current model, null when it has no animations
let action = null;         // the first clip, looping
let animationNames = [];   // clip names of the current model
let playing = true;
const clock = new THREE.Clock();

function frame() {
  if (!model) {
    camera.position.set(0, 0, 3);
    controls.target.set(0, 0, 0);
    controls.update();
    return;
  }
  const box = new THREE.Box3().setFromObject(model);
  const center = box.getCenter(new THREE.Vector3());
  const radius = box.getSize(new THREE.Vector3()).length() / 2 || 1;
  const distance = radius / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2));
  camera.near = distance / 100;
  camera.far = distance * 100;
  camera.updateProjectionMatrix();
  camera.position.copy(center).add(new THREE.Vector3(0.6, 0.4, 1).normalize().multiplyScalar(distance));
  controls.target.copy(center);
  controls.update();
}

function applyMaterials() {
  for (const [mesh, material] of originals) {
    mesh.material = textured ? material : grey;
  }
}

function updateToggle() {
  const toggle = document.getElementById('animation-toggle');
  toggle.hidden = animationNames.length === 0;
  // Symbols, not words: the page has no translation catalog.
  toggle.textContent = playing ? '⏸' : '▶';
}

function stopAnimation() {
  if (mixer) {
    mixer.stopAllAction();
    if (model) mixer.uncacheRoot(model);
  }
  mixer = null;
  action = null;
  animationNames = [];
  playing = true;
  updateToggle();
}

function clear() {
  stopAnimation();
  if (model) {
    scene.remove(model);
    model.traverse((node) => node.geometry && node.geometry.dispose());
  }
  model = null;
  originals = new Map();
  window.viewer.loaded = false;
  frame();
}

window.viewer = {
  loaded: false,
  get animations() {
    return animationNames.slice();
  },
  get playing() {
    return playing;
  },
  load(url) {
    clear();
    return loader.loadAsync(url).then((gltf) => {
      model = gltf.scene;
      model.traverse((node) => { if (node.isMesh) originals.set(node, node.material); });
      applyMaterials();
      scene.add(model);
      if (gltf.animations.length > 0) {
        mixer = new THREE.AnimationMixer(model);
        action = mixer.clipAction(gltf.animations[0]);
        action.setLoop(THREE.LoopRepeat, Infinity);
        action.play();
        animationNames = gltf.animations.map((clip) => clip.name);
      }
      updateToggle();
      frame();
      window.viewer.loaded = true;
    }).catch((error) => {
      console.error('model3d-viewer-error: ' + (error && error.message ? error.message : error));
    });
  },
  clear,
  setTextured(value) {
    textured = value;
    applyMaterials();
  },
  resetCamera: frame,
  setPlaying(value) {
    playing = Boolean(value);
    if (action) action.paused = !playing;
    updateToggle();
  },
  animationTime() {
    return action ? action.time : 0;
  },
};

document.getElementById('animation-toggle').addEventListener('click', () => {
  window.viewer.setPlaying(!playing);
});
updateToggle();

window.addEventListener('resize', () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

renderer.setAnimationLoop(() => {
  const delta = clock.getDelta();
  if (mixer) mixer.update(delta);
  controls.update();
  renderer.render(scene, camera);
});
