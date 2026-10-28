// Run from repo root after frontend npm ci:
// node docs/audits/omega-2026-10-28/frontend-probe.mjs
// Current-defect probe, not a passing acceptance test; no network used.
import { createServer } from '../../../frontend/node_modules/vite/dist/node/index.js';
const server = await createServer({ server: { middlewareMode: true } });
try {
  const bridge = await server.ssrLoadModule('/frontend/src/bridge.ts');
  globalThis.window = {
    location: { protocol: 'https:', hostname: 'appassets.androidplatform.net' },
    DispatchBridge: { getPcHost: () => 'https://192.168.1.10:8000' },
  };
  const actual = bridge.getApiBaseUrl();
  console.log('TLS configured=https://192.168.1.10:8000 actual=' + actual);
  if (actual !== 'http://192.168.1.10:8000') throw Error('Current behavior changed; update audit');
  console.log('Bridge.getAuthToken type=' + typeof bridge.Bridge.getAuthToken);
} finally {
  await server.close();
}
