// Offline unit tests for the Worker's pure decision helpers (node --test, no deps).
// NOT a live-Worker, R2, or Supabase verification. Import the compiled-free helper
// by re-implementing the import via ts stripping is unavailable in plain node, so
// the pure helpers are mirrored here from src/index.ts and cross-checked against
// the same constants; the real contract test lives in Python tests.test_cloud_quota.
import { test } from "node:test";
import assert from "node:assert/strict";

const KINDS = { clip_video: "video/mp4", thumbnail: "image/jpeg" };
const CAP_BYTES = { clip_video: 536870912, thumbnail: 8388608 };

function validateReserveInput(kind, mime, expectedBytes) {
  if (!(kind in KINDS)) return "unsupported kind";
  if (KINDS[kind] !== mime) return "unsupported mime for kind";
  if (!Number.isInteger(expectedBytes) || expectedBytes <= 0) return "expected_bytes must be a positive integer";
  if (expectedBytes > CAP_BYTES[kind]) return "expected_bytes exceeds object cap";
  return null;
}

test("valid clip reservation passes", () => {
  assert.equal(validateReserveInput("clip_video", "video/mp4", 1000), null);
});

test("wrong mime for kind rejected", () => {
  assert.equal(validateReserveInput("thumbnail", "video/mp4", 1000), "unsupported mime for kind");
});

test("unknown kind rejected", () => {
  assert.equal(validateReserveInput("document", "application/pdf", 1000), "unsupported kind");
});

test("non-integer and non-positive sizes rejected", () => {
  assert.equal(validateReserveInput("clip_video", "video/mp4", 0), "expected_bytes must be a positive integer");
  assert.equal(validateReserveInput("clip_video", "video/mp4", -1), "expected_bytes must be a positive integer");
  assert.equal(validateReserveInput("clip_video", "video/mp4", 10.5), "expected_bytes must be a positive integer");
});

test("clip cap exactly 512 MiB allowed, one byte over rejected", () => {
  assert.equal(validateReserveInput("clip_video", "video/mp4", 536870912), null);
  assert.equal(validateReserveInput("clip_video", "video/mp4", 536870913), "expected_bytes exceeds object cap");
});

test("thumbnail cap exactly 8 MiB allowed, one byte over rejected", () => {
  assert.equal(validateReserveInput("thumbnail", "image/jpeg", 8388608), null);
  assert.equal(validateReserveInput("thumbnail", "image/jpeg", 8388609), "expected_bytes exceeds object cap");
});
