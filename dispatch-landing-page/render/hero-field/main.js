import * as THREE from 'three';

const params = new URLSearchParams(location.search);
const width = Number(params.get('w')) || 1920;
const height = Number(params.get('h')) || 1080;

const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(1);
renderer.setSize(width, height);
document.body.appendChild(renderer.domElement);

const scene = new THREE.Scene();
const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);

const uniforms = {
  uRes: { value: new THREE.Vector2(width, height) },
  uTime: { value: 0 },
};

/*
 * Concentric field: thin antialiased rings over a deep evergreen wash, with two
 * soft bodies drifting through them. Every oscillator is a function of the loop
 * phase, so frame 0 and the last frame meet without a seam.
 */
const material = new THREE.ShaderMaterial({
  uniforms,
  vertexShader: /* glsl */ `
    void main() { gl_Position = vec4(position, 1.0); }
  `,
  fragmentShader: /* glsl */ `
    precision highp float;

    uniform vec2 uRes;
    uniform float uTime;

    const float TAU = 6.28318530718;

    // Field Desk palette, sRGB, kept inside the site's olive/evergreen family.
    const vec3 INK   = vec3(0.039, 0.059, 0.047); // #0a0f0c  deep anchor
    const vec3 DEEP  = vec3(0.067, 0.110, 0.086); // #11 1c 16
    const vec3 BODY  = vec3(0.106, 0.188, 0.149); // #1b3026  evergreen mass
    const vec3 LIT   = vec3(0.290, 0.459, 0.318); // #4a7551  quiet pistachio
    const vec3 PALE  = vec3(0.784, 0.878, 0.651); // #c8e0a6  seam highlight

    float hash(vec2 p) {
      return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
    }

    // One antialiased ring: distance from the circle's edge, in pixels.
    float ring(float dist, float radius, float thickness) {
      float d = abs(dist - radius);
      float aa = max(fwidth(d), 0.6);
      return 1.0 - smoothstep(thickness - aa, thickness + aa, d);
    }

    void main() {
      vec2 uv = gl_FragCoord.xy / uRes;
      vec2 p = gl_FragCoord.xy;
      float phase = uTime * TAU;

      // Portrait and landscape want different light placement so the copy
      // always sits over the dark half of the field.
      float portrait = step(1.0, uRes.x / uRes.y);
      vec2 light = mix(vec2(uRes.x * 0.74, uRes.y * 0.72), vec2(uRes.x * 0.62, uRes.y * 0.80), portrait);
      vec2 ringC = mix(vec2(uRes.x * 0.68, uRes.y * 0.60), vec2(uRes.x * 0.55, uRes.y * 0.70), portrait);

      // Slow drift of the whole field; period is the loop itself.
      vec2 drift = vec2(cos(phase), sin(phase)) * uRes.x * 0.012;
      ringC += drift;
      light += drift * 0.6;

      float dl = length(p - light) / uRes.x;
      vec3 col = mix(BODY, DEEP, smoothstep(0.10, 0.78, dl));
      col += LIT * 0.62 * smoothstep(0.62, 0.0, dl);

      // A low horizon of light keeps the lower half of a tall frame from dying out.
      float horizon = smoothstep(0.40, 0.0, abs(uv.y - 0.38)) * 0.55;
      col += LIT * horizon * mix(0.5, 1.0, portrait);

      // Two soft bodies: one lifted, one sunk below the field.
      vec2 b1 = ringC + vec2(cos(phase + 2.1), sin(phase * 1.0 + 0.7)) * uRes.x * 0.09;
      float s1 = smoothstep(0.30, 0.0, length(p - b1) / uRes.x);
      col += LIT * 0.30 * s1;

      vec2 b2 = ringC + vec2(cos(phase + 4.4), sin(phase + 3.1)) * uRes.x * 0.13;
      float s2 = smoothstep(0.22, 0.0, length(p - b2) / uRes.x);
      col = mix(col, INK, s2 * 0.55);

      // Concentric rings, evenly spaced, expanding exactly one gap per loop so the
      // seam is mathematically continuous. Fade follows absolute radius, not index,
      // otherwise a ring would change brightness as it hands off to its neighbour.
      float spacing = uRes.y * 0.19;
      float r0 = spacing * uTime;
      float d0 = length(p - ringC);
      float t = (d0 - r0) / spacing;
      float edge = abs(t - floor(t + 0.5)) * spacing;
      float radius = r0 + floor(t + 0.5) * spacing;
      float fade = (1.0 - smoothstep(spacing * 1.2, spacing * 6.5, radius)) *
        smoothstep(spacing * 0.10, spacing * 0.34, abs(radius));
      float px = max(fwidth(d0), 0.6);
      float rings = (1.0 - smoothstep(1.15 - px, 1.15 + px, edge)) * fade;

      // The origin bead: the minute leaving the hour.
      col += PALE * smoothstep(spacing * 0.045, 0.0, d0) * 0.9;
      // A second, offset family so the geometry is not a perfect target.
      vec2 ringC2 = mix(vec2(uRes.x * 0.24, uRes.y * 0.28), vec2(uRes.x * 0.30, uRes.y * 0.22), portrait) - drift;
      float d1 = length(p - ringC2);
      rings += ring(d1, spacing * 2.15, 1.0) * 0.5;
      rings += ring(d1, spacing * 3.30, 1.0) * 0.34;

      col = mix(col, PALE, clamp(rings, 0.0, 1.0) * 0.38);

      // Vignette and a gentle luminance breath.
      float v = smoothstep(1.15, 0.25, length((uv - 0.5) * vec2(1.0 + 0.0, 1.0)) * 1.6);
      col *= 0.80 + 0.20 * v;
      col *= 1.0 + 0.045 * sin(phase);

      // Dither to stop the gradient from banding once it is h264-encoded.
      col += (hash(gl_FragCoord.xy) - 0.5) * 0.0035;

      gl_FragColor = vec4(max(col, vec3(0.0)), 1.0);
    }
  `,
});

scene.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), material));

function setTime(t) {
  uniforms.uTime.value = ((t % 1) + 1) % 1;
  renderer.render(scene, camera);
}

setTime(0);

window.__setTime = setTime;
window.__renderReady = true;
