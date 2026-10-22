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

function clear() {
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
  load(url) {
    clear();
    return loader.loadAsync(url).then((gltf) => {
      model = gltf.scene;
      model.traverse((node) => { if (node.isMesh) originals.set(node, node.material); });
      applyMaterials();
      scene.add(model);
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
};

window.addEventListener('resize', () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

renderer.setAnimationLoop(() => {
  controls.update();
  renderer.render(scene, camera);
});
