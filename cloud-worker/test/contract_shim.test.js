import test from "node:test";
import assert from "node:assert/strict";
// OQ7: the Worker contract is an import-share of the canonical frontend
// mapper. These assertions pin the shim to real behavior so a broken path,
// bundling regression, or accidental re-copy fails loudly.
import {
  TIMELINE_CONTRACT_VERSION,
  sourceToProxy,
  proxyToSource,
} from "../src/timelineContract.ts";

test("timelineContract import-shares the canonical mapper (v1)", () => {
  assert.equal(TIMELINE_CONTRACT_VERSION, 1);
});

test("sourceToProxy maps window-relative coordinates with clamping", () => {
  const inside = sourceToProxy(130.0, 100.0, 400.0, { clamp: true });
  assert.equal(inside.kind, "ok");
  assert.equal(inside.time, 30.0);
  assert.equal(inside.clamped, false);

  const before = sourceToProxy(40.0, 100.0, 400.0, { clamp: true });
  assert.equal(before.kind, "ok");
  assert.equal(before.time, 0.0);
  assert.equal(before.clamped, true);

  const after = sourceToProxy(999.0, 100.0, 400.0, { clamp: true });
  assert.equal(after.kind, "ok");
  assert.equal(after.time, 300.0);
  assert.equal(after.clamped, true);
});

test("proxyToSource inverts sourceToProxy exactly", () => {
  for (const src of [100.0, 137.5, 250.0, 400.0]) {
    const fwd = sourceToProxy(src, 100.0, 400.0, { clamp: true });
    const back = proxyToSource(fwd.time, 100.0, 400.0, { clamp: true });
    assert.equal(back.kind, "ok");
    assert.ok(Math.abs(back.time - src) < 1e-6, `round-trip ${src}`);
  }
});
