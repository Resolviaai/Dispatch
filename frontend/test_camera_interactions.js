/** Regression tests for the production camera interaction state used by CameraScreen. */

import assert from 'node:assert/strict';
import {
  applyExposureResult,
  applyFocusResult,
  applyNativeLockState,
  applyUnlockResult,
  beginPointerSession,
  endPointerSession,
  exposureIndexForPointerMove,
  isCameraLifecycleTransition,
  markLockAttemptTimedOut,
} from './src/camera/cameraInteractionState';

console.log('Camera interaction regressions...');

const exposure = {
  minIndex: -4,
  maxIndex: 4,
  currentIndex: 0,
  stepEv: 0.5,
  isSupported: true,
};

// Direct track/handle pointer-down owns the origin, so the very first captured move is actionable.
const directTrackTouch = beginPointerSession(11, 180, 220, 0, true);
assert.equal(exposureIndexForPointerMove(directTrackTouch, 11, 180, 140, exposure), 4);
assert.equal(exposureIndexForPointerMove(directTrackTouch, 11, 180, 300, exposure), -4);
assert.equal(exposureIndexForPointerMove(directTrackTouch, 12, 180, 140, exposure), null, 'other pointer ids cannot move this drag');
assert.equal(exposureIndexForPointerMove(directTrackTouch, 11, 180, 140, { ...exposure, isSupported: false }), null);

// A general viewfinder gesture never adjusts exposure.
const viewfinderTap = beginPointerSession(13, 180, 220, 0, false);
assert.equal(exposureIndexForPointerMove(viewfinderTap, 13, 180, 140, exposure), null);

// Release, cancellation, and lost capture use the same idempotent session cleanup.
for (const endReason of ['pointerup', 'pointercancel', 'lostpointercapture']) {
  const session = beginPointerSession(14, 180, 220, 0, true);
  assert.equal(endPointerSession(session, 14), null, `${endReason} clears the active pointer origin`);
  assert.equal(exposureIndexForPointerMove(endPointerSession(session, 14), 14, 180, 140, exposure), null);
}
assert.equal(endPointerSession(directTrackTouch, 99), directTrackTouch, 'stale end events must not clear another pointer');

// The Android contract is `index`; only a successful native result changes the displayed value.
assert.deepEqual(applyExposureResult(1, { success: true, index: -3, ev: -1.5 }), { index: -3, error: null });
assert.deepEqual(applyExposureResult(1, { success: false, error: 'Unsupported' }), { index: 1, error: 'Unsupported' });

const pendingLock = {
  x: 150, y: 200, screenNormX: 0.5, screenNormY: 0.5,
  status: 'locking', triggerId: 1001, isLocked: false,
  hasAf: true, hasAe: true, timestamp: 1001,
};
assert.equal(pendingLock.isLocked, false, 'lock UI starts pending, without a confirmed lock');

const aeOnlyConfirmed = applyFocusResult(pendingLock, {
  triggerId: 1001, success: true, isLocked: true, hasAf: false, hasAe: true,
});
assert.equal(aeOnlyConfirmed.status, 'locked', 'native confirmation accepts AE-only locking');
assert.equal(aeOnlyConfirmed.isLocked, true);
assert.equal(aeOnlyConfirmed.hasAf, false);

const failedLock = applyFocusResult(pendingLock, {
  triggerId: 1001, success: false, isLocked: false, hasAf: false, hasAe: false, error: 'Unsupported',
});
assert.equal(failedLock.status, 'failed');
assert.equal(failedLock.isLocked, false);
assert.equal(applyFocusResult(pendingLock, { triggerId: 999, success: true, isLocked: true }), pendingLock, 'stale results are ignored');

const timedOutLock = markLockAttemptTimedOut(pendingLock, 1001);
assert.equal(timedOutLock.status, 'failed');
assert.equal(timedOutLock.isLocked, false);
assert.match(timedOutLock.error, /timed out/i);
assert.equal(applyNativeLockState(timedOutLock, true).isLocked, true, 'a direct native status read can confirm a delayed lock');

const failedUnlock = applyUnlockResult(aeOnlyConfirmed, false);
assert.equal(failedUnlock.isLocked, true, 'failed unlock keeps confirmed lock UI in sync');
assert.equal(applyUnlockResult(aeOnlyConfirmed, true).isLocked, false);

const cameraState = { status: 'ready', lensFacing: 'back', isRecording: false, isTorchEnabled: false, isAeAfLocked: true, isAudioEnabled: true, exposure, zoom: { minZoom: 1, maxZoom: 4, currentZoom: 1 } };
assert.equal(isCameraLifecycleTransition(cameraState, { ...cameraState, status: 'initializing' }), true);
assert.equal(isCameraLifecycleTransition(cameraState, { ...cameraState, lensFacing: 'front' }), true);
assert.equal(isCameraLifecycleTransition(cameraState, { ...cameraState, isAeAfLocked: false }), false);

console.log('  passed: direct slider drags, pointer cleanup, confirmed exposure results, AE-only locks, timeout recovery, unlock and lifecycle state');
