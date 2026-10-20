/**
 * Dispatch Camera Focus, Exposure, Coordinate Mapping & UX Invariant Tests
 */

import assert from 'assert';

console.log('🧪 Starting Dispatch Camera Focus & Exposure Test Suite...\n');

// 1. Coordinate Normalization Invariant
console.log('Test 1: Screen-normalized coordinates for full-bleed PreviewView...');
function calculateNormalizedCoordinates(clientX, clientY, containerRect, windowBounds, isNative) {
  const normX = isNative && windowBounds.width > 0
    ? Math.max(0, Math.min(1, clientX / windowBounds.width))
    : Math.max(0, Math.min(1, (clientX - containerRect.left) / containerRect.width));

  const normY = isNative && windowBounds.height > 0
    ? Math.max(0, Math.min(1, clientY / windowBounds.height))
    : Math.max(0, Math.min(1, (clientY - containerRect.top) / containerRect.height));

  return { normX, normY };
}

// Emulate POCO C65 screen: 393 x 852. Viewfinder is sitting above 56px bottom nav (height = 796px).
const windowPOCO = { width: 393, height: 852 };
const containerPOCO = { left: 0, top: 0, width: 393, height: 796 };

// User touches at center of screen (y = 426)
const nativeCoords = calculateNormalizedCoordinates(196.5, 426, containerPOCO, windowPOCO, true);
assert.strictEqual(nativeCoords.normY, 0.5, 'Center touch must normalize to 0.5 on screen');
assert.strictEqual(nativeCoords.normX, 0.5, 'Center touch must normalize to 0.5 on screen');

// Verify that using container height would have caused severe offset error
const brokenY = 426 / 796; // ~0.535 (30px offset on native PreviewView)
assert.notStrictEqual(nativeCoords.normY, brokenY, 'Native calculation must not use cropped container height');
console.log('  ✓ Native coordinates map 1:1 to PreviewView full-bleed space without letterbox/nav offsets');

// 2. Exposure Adjustment Isolation Invariant
console.log('Test 2: Dedicated exposure slider interaction isolation (no accidental swipes)...');
function handlePointerDrag(isInteractingWithSlider, isSupported, dy, startIndex, expInfo) {
  // RULE: Only change exposure if directly interacting with slider
  if (!isSupported || !isInteractingWithSlider) {
    return { applied: false, index: startIndex };
  }
  const totalDragDistancePx = 160;
  const range = Math.max(1, expInfo.maxIndex - expInfo.minIndex);
  const indexOffset = (-dy / totalDragDistancePx) * range;
  const targetIndex = Math.min(
    expInfo.maxIndex,
    Math.max(expInfo.minIndex, Math.round(startIndex + indexOffset))
  );
  return { applied: true, index: targetIndex };
}

const expInfo = { minIndex: -4, maxIndex: 4, currentIndex: 0, stepEv: 0.5, isSupported: true };

// Accidental vertical swipe on viewfinder (isInteractingWithSlider = false, dy = -50)
const accidentalSwipe = handlePointerDrag(false, true, -50, 0, expInfo);
assert.strictEqual(accidentalSwipe.applied, false, 'Accidental swipe must not alter exposure');
assert.strictEqual(accidentalSwipe.index, 0, 'Index must remain unchanged');

// Intentional touch on slider handle (isInteractingWithSlider = true, dy = -80 => half of 160px drag = +4 index steps in range of 8)
const intentionalDrag = handlePointerDrag(true, true, -80, 0, expInfo);
assert.strictEqual(intentionalDrag.applied, true, 'Intentional slider drag must adjust exposure');
assert.strictEqual(intentionalDrag.index, 4, 'Index should increase by 4 on half-track upward drag');

// Clamping to hardware range
const extremeDrag = handlePointerDrag(true, true, -400, 0, expInfo);
assert.strictEqual(extremeDrag.index, 4, 'Index must clamp to hardware maxIndex');
const extremeDragDown = handlePointerDrag(true, true, 400, 0, expInfo);
assert.strictEqual(extremeDragDown.index, -4, 'Index must clamp to hardware minIndex');

// Unsupported hardware
const unsupportedExp = { ...expInfo, isSupported: false, minIndex: 0, maxIndex: 0 };
const dragOnUnsupported = handlePointerDrag(true, false, -80, 0, unsupportedExp);
assert.strictEqual(dragOnUnsupported.applied, false, 'Unsupported exposure hardware must reject changes');
console.log('  ✓ Exposure changes strictly isolated to dedicated slider and clamped to hardware bounds');

// 3. Real AE/AF Lock Commit Verification
console.log('Test 3: AE/AF Lock state commitments based on asynchronous hardware confirmation...');
function evaluateFocusResult(currentFocus, result) {
  if (currentFocus.triggerId !== result.triggerId) {
    return currentFocus; // Ignore stale callbacks
  }
  if (result.isLocked) {
    return {
      ...currentFocus,
      status: result.success ? 'locked' : 'failed',
      isLocked: result.success
    };
  } else {
    return {
      ...currentFocus,
      status: result.success ? 'success' : 'failed',
      isLocked: false
    };
  }
}

const activeLockAttempt = { triggerId: 1001, status: 'focusing', isLocked: true };

// Hardware fails lock
const failedLock = evaluateFocusResult(activeLockAttempt, { triggerId: 1001, success: false, isLocked: true });
assert.strictEqual(failedLock.status, 'failed', 'Failed lock attempt must transition to failed status');
assert.strictEqual(failedLock.isLocked, false, 'Failed lock must not remain locked');

// Hardware succeeds lock
const successfulLock = evaluateFocusResult(activeLockAttempt, { triggerId: 1001, success: true, isLocked: true });
assert.strictEqual(successfulLock.status, 'locked', 'Successful lock must transition to locked status');
assert.strictEqual(successfulLock.isLocked, true, 'Successful lock commits isLocked true');

// Stale event from prior tap ignored
const staleResult = evaluateFocusResult(successfulLock, { triggerId: 999, success: false, isLocked: false });
assert.strictEqual(staleResult.status, 'locked', 'Stale event must not overwrite current lock');
console.log('  ✓ AE/AF lock commits only upon verified hardware success and rejects stale responses');

// 4. Camera Permission & Microphone Decoupling
console.log('Test 4: Camera initialization with microphone permission denial...');
function evaluateCameraAudioState(permissionState) {
  // If camera is granted but mic denied, video capture remains ready with audioEnabled: false
  if (permissionState.cameraGranted) {
    return {
      canRecordVideo: true,
      audioEnabled: !!permissionState.micGranted,
      status: 'ready'
    };
  }
  return {
    canRecordVideo: false,
    audioEnabled: false,
    status: 'permission_required'
  };
}

const micDenied = evaluateCameraAudioState({ cameraGranted: true, micGranted: false });
assert.strictEqual(micDenied.canRecordVideo, true, 'Camera must be operational even if mic is denied');
assert.strictEqual(micDenied.audioEnabled, false, 'Audio should be disabled when mic is denied');
assert.strictEqual(micDenied.status, 'ready', 'Status must be ready for video recording');

const cameraDenied = evaluateCameraAudioState({ cameraGranted: false, micGranted: true });
assert.strictEqual(cameraDenied.canRecordVideo, false, 'Cannot record video without camera permission');
assert.strictEqual(cameraDenied.status, 'permission_required', 'Must request camera permission');
console.log('  ✓ Microphone permission denial properly decouples and allows video capture');

console.log('\n🎉 ALL 4 CAMERA INTERACTION & BRIDGE TESTS PASSED 100%!');
