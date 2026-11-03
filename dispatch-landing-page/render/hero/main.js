import * as THREE from 'three';
import { ImprovedNoise } from 'three/addons/math/ImprovedNoise.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';

const params = new URLSearchParams(location.search);
const mode = params.get('mode') === 'mobile' ? 'mobile' : 'desktop';
const width = Number(params.get('w')) || (mode === 'mobile' ? 1200 : 2560);
const height = Number(params.get('h')) || (mode === 'mobile' ? 1800 : 1440);

const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2('#151d0f', 0.032);

const camera = new THREE.PerspectiveCamera(36, width / height, 0.1, 120);

const renderer = new THREE.WebGLRenderer({
  antialias: true,
  preserveDrawingBuffer: true,
  powerPreference: 'high-performance',
});
renderer.setPixelRatio(1);
renderer.setSize(width, height);
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.18;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFShadowMap;
document.body.appendChild(renderer.domElement);

const composer = new EffectComposer(renderer);
composer.setSize(width, height);
composer.addPass(new RenderPass(scene, camera));
const bloom = new UnrealBloomPass(new THREE.Vector2(width, height), 0.34, 0.85, 0.86);
composer.addPass(bloom);
composer.addPass(new OutputPass());

// Deep olive sky with a soft pistachio horizon
const sky = new THREE.Mesh(
  new THREE.SphereGeometry(70, 48, 32),
  new THREE.ShaderMaterial({
    side: THREE.BackSide,
    depthWrite: false,
    uniforms: {
      top: { value: new THREE.Color('#28331d') },
      bottom: { value: new THREE.Color('#0a0e07') },
      horizon: { value: new THREE.Color('#687a4b') },
    },
    vertexShader: `
      varying vec3 vPos;
      void main() {
        vPos = position;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      varying vec3 vPos;
      uniform vec3 top;
      uniform vec3 bottom;
      uniform vec3 horizon;
      void main() {
        float h = normalize(vPos).y;
        vec3 color = mix(bottom, horizon, smoothstep(-0.35, 0.08, h));
        color = mix(color, top, smoothstep(0.02, 0.75, h));
        gl_FragColor = vec4(color, 1.0);
      }
    `,
  })
);
scene.add(sky);

function makeCanvasTexture(size, paint) {
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');
  paint(ctx, size);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

const grainTexture = makeCanvasTexture(512, (ctx, size) => {
  const image = ctx.createImageData(size, size);
  const noise = new ImprovedNoise();
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const n =
        noise.noise(x * 0.035, y * 0.035, 0.5) * 0.55 +
        noise.noise(x * 0.11, y * 0.11, 2.3) * 0.3 +
        noise.noise(x * 0.4, y * 0.4, 5.1) * 0.15;
      const value = Math.round(128 + n * 120);
      const i = (y * size + x) * 4;
      image.data[i] = value;
      image.data[i + 1] = value;
      image.data[i + 2] = value;
      image.data[i + 3] = 255;
    }
  }
  ctx.putImageData(image, 0, 0);
});
grainTexture.wrapS = THREE.RepeatWrapping;
grainTexture.wrapT = THREE.RepeatWrapping;
grainTexture.repeat.set(3, 5);

// Ground
const ground = new THREE.Mesh(
  new THREE.PlaneGeometry(120, 120),
  new THREE.MeshStandardMaterial({
    color: '#202a17',
    roughness: 0.86,
    metalness: 0.05,
    bumpMap: grainTexture,
    bumpScale: 0.05,
  })
);
ground.rotation.x = -Math.PI / 2;
ground.position.y = -0.04;
ground.receiveShadow = true;
scene.add(ground);

const poolTexture = makeCanvasTexture(512, (ctx, size) => {
  const gradient = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  gradient.addColorStop(0, 'rgba(220, 234, 177, 0.66)');
  gradient.addColorStop(0.36, 'rgba(160, 180, 112, 0.26)');
  gradient.addColorStop(0.72, 'rgba(90, 108, 60, 0.06)');
  gradient.addColorStop(1, 'rgba(0, 0, 0, 0)');
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, size, size);
});
const pool = new THREE.Mesh(
  new THREE.PlaneGeometry(18, 18),
  new THREE.MeshBasicMaterial({
    map: poolTexture,
    transparent: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    opacity: 0.42,
  })
);
pool.rotation.x = -Math.PI / 2;
pool.position.set(3.0, 0.015, -0.2);
scene.add(pool);

const glowTexture = makeCanvasTexture(512, (ctx, size) => {
  const gradient = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  gradient.addColorStop(0, 'rgba(224, 236, 186, 0.9)');
  gradient.addColorStop(0.26, 'rgba(156, 178, 108, 0.32)');
  gradient.addColorStop(0.62, 'rgba(78, 94, 50, 0.07)');
  gradient.addColorStop(1, 'rgba(0, 0, 0, 0)');
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, size, size);
});
const backdropGlow = new THREE.Sprite(
  new THREE.SpriteMaterial({
    map: glowTexture,
    color: '#c4d695',
    transparent: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    opacity: 0.38,
  })
);
backdropGlow.position.set(3.4, 3.4, -7.5);
backdropGlow.scale.set(15, 13, 1);
scene.add(backdropGlow);

// Lighting
const key = new THREE.DirectionalLight('#f4f1d6', 2.6);
key.position.set(7, 9, 7);
key.castShadow = true;
key.shadow.mapSize.set(2048, 2048);
key.shadow.camera.left = -9;
key.shadow.camera.right = 9;
key.shadow.camera.top = 11;
key.shadow.camera.bottom = -4;
key.shadow.camera.near = 1;
key.shadow.camera.far = 32;
key.shadow.bias = -0.0006;
key.shadow.radius = 7;
scene.add(key);

const rim = new THREE.SpotLight('#dce9b4', 26, 24, 0.62, 1, 1.3);
rim.position.set(6.5, 8, -7);
rim.target.position.set(3.0, 2.6, -1);
scene.add(rim, rim.target);

const fill = new THREE.PointLight('#a9c47a', 12, 18, 2);
fill.position.set(-4, 2.5, 6);
scene.add(fill);

const ambient = new THREE.HemisphereLight('#d4e2aa', '#070a05', 0.34);
scene.add(ambient);

// The hour stone — a tall, hand-formed standing form
const profile = [
  new THREE.Vector2(0.0, 0.0),
  new THREE.Vector2(0.86, 0.0),
  new THREE.Vector2(1.02, 0.28),
  new THREE.Vector2(1.0, 1.1),
  new THREE.Vector2(0.94, 2.2),
  new THREE.Vector2(0.82, 3.3),
  new THREE.Vector2(0.64, 4.25),
  new THREE.Vector2(0.38, 5.02),
  new THREE.Vector2(0.12, 5.34),
  new THREE.Vector2(0.0, 5.38),
];
const stoneGeometry = new THREE.LatheGeometry(profile, 120);
const positions = stoneGeometry.attributes.position;
const noise = new ImprovedNoise();
const vertex = new THREE.Vector3();
for (let i = 0; i < positions.count; i += 1) {
  vertex.fromBufferAttribute(positions, i);
  const n = noise.noise(vertex.x * 1.3, vertex.y * 1.1, vertex.z * 1.3);
  vertex.x += n * 0.055;
  vertex.z += n * 0.055;
  positions.setXYZ(i, vertex.x, vertex.y, vertex.z);
}
stoneGeometry.computeVertexNormals();

const stone = new THREE.Mesh(
  stoneGeometry,
  new THREE.MeshStandardMaterial({
    color: '#465334',
    roughness: 0.7,
    metalness: 0.04,
    bumpMap: grainTexture,
    bumpScale: 0.06,
  })
);
stone.scale.set(1.22, 1, 0.92);
stone.position.set(3.0, 0, -1.2);
stone.rotation.y = -0.2;
stone.castShadow = true;
stone.receiveShadow = true;
scene.add(stone);

// A smaller companion stone, for depth
const stoneBack = stone.clone();
stoneBack.scale.set(0.42, 0.82, 0.36);
stoneBack.position.set(5.55, 0, -3.8);
stoneBack.rotation.y = 0.62;
stoneBack.material = stone.material.clone();
stoneBack.material.color.set('#2c3620');
scene.add(stoneBack);

// The thread — a single pale line wrapping the stone and ending in the minute
const threadPoints = [];
const turns = 2.55;
const steps = 110;
for (let i = 0; i <= steps; i += 1) {
  const t = i / steps;
  const angle = -Math.PI * 0.72 + t * Math.PI * 2 * turns;
  const radiusX = 1.42 + Math.sin(t * Math.PI) * 0.08;
  const radiusZ = 1.1 + Math.sin(t * Math.PI) * 0.06;
  threadPoints.push(
    new THREE.Vector3(
      stone.position.x + Math.cos(angle) * radiusX,
      0.28 + t * 4.55,
      stone.position.z + Math.sin(angle) * radiusZ
    )
  );
}
threadPoints.push(new THREE.Vector3(2.85, 4.95, 1.05));
threadPoints.push(new THREE.Vector3(2.42, 4.18, 1.82));
threadPoints.push(new THREE.Vector3(2.08, 3.58, 2.18));
threadPoints.push(new THREE.Vector3(1.94, 3.28, 2.3));

const threadCurve = new THREE.CatmullRomCurve3(threadPoints, false, 'catmullrom', 0.35);
const thread = new THREE.Mesh(
  new THREE.TubeGeometry(threadCurve, 900, 0.011, 8, false),
  new THREE.MeshStandardMaterial({
    color: '#edf2d4',
    emissive: '#c9db9b',
    emissiveIntensity: 1.65,
    roughness: 0.4,
    metalness: 0,
  })
);
scene.add(thread);

// The minute — one small, bright bead
const bead = new THREE.Mesh(
  new THREE.SphereGeometry(0.13, 64, 64),
  new THREE.MeshBasicMaterial({ color: '#f6f9e4' })
);
bead.position.set(1.94, 3.24, 2.3);
scene.add(bead);

const beadLight = new THREE.PointLight('#eaf3c4', 7, 5.5, 2);
beadLight.position.copy(bead.position);
scene.add(beadLight);

const beadGlow = new THREE.Sprite(
  new THREE.SpriteMaterial({
    map: glowTexture,
    color: '#edf5c8',
    transparent: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    opacity: 0.72,
  })
);
beadGlow.position.copy(bead.position);
beadGlow.scale.set(1.5, 1.5, 1);
scene.add(beadGlow);

// A small human, for scale and tenderness
const figure = new THREE.Group();
const dark = new THREE.MeshStandardMaterial({ color: '#0a0d07', roughness: 0.94, metalness: 0 });
const coat = new THREE.Mesh(new THREE.CylinderGeometry(0.16, 0.3, 0.92, 24), dark);
coat.position.y = 0.86;
coat.castShadow = true;
figure.add(coat);
const shoulders = new THREE.Mesh(new THREE.SphereGeometry(0.24, 32, 24), dark);
shoulders.scale.set(1, 0.55, 0.8);
shoulders.position.y = 1.3;
shoulders.castShadow = true;
figure.add(shoulders);
const head = new THREE.Mesh(new THREE.SphereGeometry(0.14, 32, 32), dark);
head.position.y = 1.55;
head.castShadow = true;
figure.add(head);
for (const side of [-1, 1]) {
  const boot = new THREE.Mesh(new THREE.CapsuleGeometry(0.07, 0.08, 4, 10), dark);
  boot.rotation.x = Math.PI / 2;
  boot.position.set(side * 0.1, 0.08, 0.05);
  boot.castShadow = true;
  figure.add(boot);
}
figure.position.set(4.92, 0, 2.15);
figure.rotation.y = -0.48;
scene.add(figure);

// Dust suspended in the light
const dustCount = 420;
const dustPositions = new Float32Array(dustCount * 3);
for (let i = 0; i < dustCount; i += 1) {
  dustPositions[i * 3] = 3.0 + (Math.random() - 0.5) * 8;
  dustPositions[i * 3 + 1] = Math.random() * 6.4;
  dustPositions[i * 3 + 2] = -0.8 + (Math.random() - 0.5) * 8;
}
const dustGeometry = new THREE.BufferGeometry();
dustGeometry.setAttribute('position', new THREE.BufferAttribute(dustPositions, 3));
const dust = new THREE.Points(
  dustGeometry,
  new THREE.PointsMaterial({
    color: '#dbe8b0',
    size: 0.026,
    transparent: true,
    opacity: 0.38,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    sizeAttenuation: true,
  })
);
scene.add(dust);

if (mode === 'mobile') {
  camera.fov = 42;
  camera.position.set(2.1, 3.4, 14.8);
  camera.lookAt(3.1, 3.0, 0);
  figure.position.set(5.35, 0, 2.3);
  figure.rotation.y = -0.55;
} else {
  camera.position.set(0.6, 3.25, 13.4);
  camera.lookAt(3.15, 2.8, 0);
}

camera.updateProjectionMatrix();

let frame = 0;
function render() {
  frame += 1;
  const t = frame * 0.01;
  beadGlow.scale.setScalar(1.46 + Math.sin(t * 1.2) * 0.05);
  beadLight.intensity = 6.8 + Math.sin(t * 1.05) * 0.35;
  dust.rotation.y = Math.sin(t * 0.06) * 0.015;
  composer.render();
  requestAnimationFrame(render);
}
render();

window.__renderReady = true;
