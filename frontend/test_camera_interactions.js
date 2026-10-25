/** Regression tests for the production camera interaction state used by CameraScreen. */

import assert from 'node:assert/strict';
import {
  applyExposureResult,
  applyFocusResult,
  applyNativeLockState,
  applyUnlockResult,
  beginPointerSession,
  crossedIntegerZoomBoundary,
  endPointerSession,
  exposureIndexForPointerMove,
  isCameraLifecycleTransition,
  markLockAttemptTimedOut,
  shouldUnlockAndRefocusOnTap,
  sliderFractionFromZoomRatio,
  zoomRatioFromSliderFraction,
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
assert.equal(applyFocusResult(pendingLock, {
  triggerId: 1001, success: true, isLocked: true, hasAf: false, hasAe: false,
}).status, 'failed', 'a success flag without a confirmed channel cannot claim a lock');

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
const uncertainLock = applyFocusResult(timedOutLock, {
  triggerId: 1001, success: false, isLocked: false, hasAf: false, hasAe: false,
  lockStateUncertain: true, error: 'Recovery timed out',
});
assert.equal(uncertainLock.status, 'uncertain', 'missing confirmation remains explicitly uncertain');
assert.equal(uncertainLock.isLocked, false, 'uncertain is never displayed as confirmed lock');
assert.equal(applyNativeLockState(pendingLock, true, {
  isAeLocked: true, isAfLocked: false, isAeAfLockUncertain: true,
}).isLocked, false, 'partial channel lock plus uncertain native state is never reported as confirmed');
const delayedLockResult = applyFocusResult(timedOutLock, {
  triggerId: 1001, success: true, isLocked: true, hasAf: false, hasAe: true,
});
assert.equal(delayedLockResult.status, 'locked', 'a delayed native result restores the confirmed AE-only lock');
assert.equal(delayedLockResult.isLocked, true);

const failedUnlock = applyUnlockResult(aeOnlyConfirmed, false);
assert.equal(failedUnlock.isLocked, true, 'failed unlock keeps confirmed lock UI in sync');
const unlocked = applyUnlockResult(aeOnlyConfirmed, true);
assert.equal(unlocked.isLocked, false);
assert.equal(unlocked.hasAe, false);
assert.equal(unlocked.hasAf, false);

const cameraState = { status: 'ready', lensFacing: 'back', isRecording: false, isTorchEnabled: false, isAeAfLocked: true, isAudioEnabled: true, exposure, zoom: { minZoom: 1, maxZoom: 4, currentZoom: 1 } };
assert.equal(isCameraLifecycleTransition(cameraState, { ...cameraState, status: 'initializing' }), true);
assert.equal(isCameraLifecycleTransition(cameraState, { ...cameraState, lensFacing: 'front' }), true);
assert.equal(isCameraLifecycleTransition(cameraState, { ...cameraState, isAeAfLocked: false }), false);

// A single tap on a locked (or uncertain) reticle unlocks and moves focus; taps on
// idle/focusing/failed reticles keep the plain focus contract.
assert.equal(shouldUnlockAndRefocusOnTap({ isLocked: true, status: 'locked' }), true, 'locked reticle taps unlock + refocus');
assert.equal(shouldUnlockAndRefocusOnTap({ isLocked: false, status: 'uncertain' }), true, 'uncertain reticle taps recover via unlock + refocus');
assert.equal(shouldUnlockAndRefocusOnTap(null), false);
assert.equal(shouldUnlockAndRefocusOnTap({ isLocked: false, status: 'idle' }), false);
assert.equal(shouldUnlockAndRefocusOnTap({ isLocked: false, status: 'focusing' }), false);
assert.equal(shouldUnlockAndRefocusOnTap({ isLocked: false, status: 'success' }), false);

// Recording zoom slider maps fractions to hardware ratios and back, clamping
// out-of-range input instead of trusting it.
assert.equal(zoomRatioFromSliderFraction(0, 1, 5), 1);
assert.equal(zoomRatioFromSliderFraction(1, 1, 5), 5);
assert.equal(zoomRatioFromSliderFraction(0.5, 1, 5), 3);
assert.equal(zoomRatioFromSliderFraction(-0.4, 1, 5), 1, 'negative fractions clamp to min zoom');
assert.equal(zoomRatioFromSliderFraction(1.7, 1, 5), 5, 'overflow fractions clamp to max zoom');
assert.equal(zoomRatioFromSliderFraction(0.25, 0.6, 2.6), 1.1);
assert.ok(Number.isFinite(zoomRatioFromSliderFraction(NaN, 1, 5)), 'non-numeric input never yields NaN');
assert.equal(sliderFractionFromZoomRatio(3, 1, 5), 0.5);
assert.equal(sliderFractionFromZoomRatio(0.6, 0.6, 2.6), 0);
assert.equal(sliderFractionFromZoomRatio(2.6, 0.6, 2.6), 1);
assert.equal(sliderFractionFromZoomRatio(9, 1, 5), 1, 'a hardware ratio above max pins the slider to the end');
const roundTrip = sliderFractionFromZoomRatio(zoomRatioFromSliderFraction(0.37, 1, 5), 1, 5);
assert.ok(Math.abs(roundTrip - 0.37) < 0.01, 'fraction -> ratio -> fraction survives a round trip');

// Haptic ticks fire exactly when the drag crosses an integer zoom boundary.
assert.equal(crossedIntegerZoomBoundary(1.2, 1.9), false);
assert.equal(crossedIntegerZoomBoundary(1.9, 2.05), true);
assert.equal(crossedIntegerZoomBoundary(2.05, 2.4), false);
assert.equal(crossedIntegerZoomBoundary(0.6, 1.0), true);

console.log('  passed: direct slider drags, pointer cleanup, confirmed exposure results, AE-only locks, timeout recovery, unlock and lifecycle state');
console.log('  passed: tap-to-unlock-and-refocus contract, recording zoom slider mapping, integer haptic boundaries');
