import type { ExposureInfo, ExposureResultEvent, FocusResultEvent, CameraStateEvent } from '../bridge';

export type FocusStatus = 'idle' | 'focusing' | 'locking' | 'unlocking' | 'success' | 'failed' | 'locked' | 'uncertain';

export interface FocusExposureState {
  x: number;
  y: number;
  screenNormX: number;
  screenNormY: number;
  status: FocusStatus;
  triggerId: number;
  isLocked: boolean;
  hasAf: boolean;
  hasAe: boolean;
  timestamp: number;
  error?: string;
}

export interface PointerSession {
  pointerId: number;
  originX: number;
  originY: number;
  startExposureIndex: number;
  isExposureSlider: boolean;
}

export function beginPointerSession(
  pointerId: number,
  x: number,
  y: number,
  startExposureIndex: number,
  isExposureSlider: boolean
): PointerSession {
  return { pointerId, originX: x, originY: y, startExposureIndex, isExposureSlider };
}

export function exposureIndexForPointerMove(
  session: PointerSession | null,
  pointerId: number,
  x: number,
  y: number,
  exposure: ExposureInfo,
  totalDragDistancePx = 160
): number | null {
  if (!session || session.pointerId !== pointerId || !session.isExposureSlider || !exposure.isSupported) {
    return null;
  }

  const range = Math.max(1, exposure.maxIndex - exposure.minIndex);
  const indexOffset = (-(y - session.originY) / totalDragDistancePx) * range;
  return Math.min(
    exposure.maxIndex,
    Math.max(exposure.minIndex, Math.round(session.startExposureIndex + indexOffset))
  );
}

export function endPointerSession(session: PointerSession | null, pointerId: number): PointerSession | null {
  return session?.pointerId === pointerId ? null : session;
}

export function applyExposureResult(
  currentIndex: number,
  result: ExposureResultEvent
): { index: number; error: string | null } {
  const applied = result.index;
  if (result.success && applied !== undefined && Number.isInteger(applied)) {
    return { index: applied, error: null };
  }
  return {
    index: currentIndex,
    error: result.error || 'Camera did not apply the exposure change',
  };
}

export function applyFocusResult(
  current: FocusExposureState | null,
  result: FocusResultEvent
): FocusExposureState | null {
  if (!current || !Number.isFinite(result.triggerId) || current.triggerId !== result.triggerId) {
    return current;
  }

  if (result.lockStateUncertain) {
    return {
      ...current,
      status: 'uncertain',
      isLocked: false,
      hasAf: result.hasAf ?? current.hasAf,
      hasAe: result.hasAe ?? current.hasAe,
      error: result.error || 'Camera lock state is uncertain; unlock to recover safely.',
    };
  }

  const canConfirmDelayedLock = (current.status === 'failed' || current.status === 'uncertain') && result.success && result.isLocked;
  const lockConfirmed = result.success && result.isLocked && (result.hasAf === true || result.hasAe === true);
  if (current.status === 'locking' || canConfirmDelayedLock) {
    return {
      ...current,
      status: result.lockStateUncertain ? 'uncertain' : lockConfirmed ? 'locked' : 'failed',
      isLocked: lockConfirmed,
      hasAf: result.hasAf ?? current.hasAf,
      hasAe: result.hasAe ?? current.hasAe,
      error: result.error || (lockConfirmed ? undefined : 'Camera did not confirm the AE/AF lock'),
    };
  }

  if (current.status === 'unlocking') return current;

  return {
    ...current,
    status: result.success && !result.isLocked ? 'success' : 'failed',
    isLocked: false,
    hasAf: result.hasAf ?? current.hasAf,
    hasAe: result.hasAe ?? current.hasAe,
    error: result.success ? undefined : (result.error || 'Camera focus failed'),
  };
}

export function markLockAttemptTimedOut(
  current: FocusExposureState | null,
  triggerId: number
): FocusExposureState | null {
  if (!current || current.triggerId !== triggerId || current.status !== 'locking') return current;
  return {
    ...current,
    status: 'failed',
    isLocked: false,
    error: 'Lock confirmation timed out; resetting camera focus state',
  };
}

export function applyUnlockResult(
  current: FocusExposureState | null,
  success: boolean
): FocusExposureState | null {
  if (!current) return null;
  if (success) {
    return { ...current, status: 'idle', isLocked: false, hasAf: false, hasAe: false, error: undefined };
  }
  return {
    ...current,
    status: current.isLocked ? 'locked' : current.status === 'unlocking' ? 'uncertain' : current.status === 'uncertain' ? 'uncertain' : 'failed',
    error: 'Camera did not confirm unlock. Try again.',
  };
}

export function applyNativeLockState(
  current: FocusExposureState | null,
  isAeAfLocked: boolean,
  nativeState?: Pick<CameraStateEvent, 'isAeLocked' | 'isAfLocked' | 'isAeAfLockUncertain'>
): FocusExposureState | null {
  if (!current) return current;
  const hasKnownChannel = current.hasAe || current.hasAf;
  const hasAe = nativeState?.isAeLocked ?? (isAeAfLocked ? (hasKnownChannel ? current.hasAe : true) : false);
  const hasAf = nativeState?.isAfLocked ?? (isAeAfLocked ? (hasKnownChannel ? current.hasAf : true) : false);
  if (nativeState?.isAeAfLockUncertain) {
    return { ...current, status: 'uncertain', isLocked: false, hasAe, hasAf, error: 'Camera lock state could not be confirmed; unlock to recover safely.' };
  }
  if (isAeAfLocked) {
    return { ...current, status: 'locked', isLocked: true, hasAe, hasAf, error: undefined };
  }
  if (current.status === 'locking') return current;
  if (current.isLocked || current.status === 'unlocking') {
    return { ...current, status: current.status === 'unlocking' ? 'idle' : current.status, isLocked: false, hasAe: false, hasAf: false };
  }
  return current;
}

export function isCameraLifecycleTransition(
  previous: CameraStateEvent,
  next: CameraStateEvent
): boolean {
  return previous.lensFacing !== next.lensFacing || next.status !== 'ready';
}

/**
 * Native camera tap contract on a locked reticle: a single tap anywhere outside
 * the sun dial unlocks AE/AF and moves focus to the tapped point. Uncertain lock
 * states are included so a tap also recovers the camera.
 */
export function shouldUnlockAndRefocusOnTap(
  focus: Pick<FocusExposureState, 'isLocked' | 'status'> | null
): boolean {
  if (!focus) return false;
  return focus.isLocked || focus.status === 'uncertain';
}

/** Maps a 0..1 zoom slider fraction to a hardware zoom ratio. */
export function zoomRatioFromSliderFraction(
  fraction: number,
  minZoom: number,
  maxZoom: number
): number {
  const min = Number.isFinite(minZoom) ? minZoom : 1.0;
  const max = Number.isFinite(maxZoom) && maxZoom > min ? maxZoom : Math.max(min + 2, 5.0);
  const f = Math.min(1, Math.max(0, Number.isFinite(fraction) ? fraction : 0.5));

  if (f >= 0.5) {
    if (max <= 1.0) return 1.0;
    const ratio = 1.0 + ((f - 0.5) / 0.5) * (max - 1.0);
    return Math.round(ratio * 10) / 10;
  } else {
    if (min < 1.0) {
      const ratio = min + (f / 0.5) * (1.0 - min);
      return Math.round(ratio * 10) / 10;
    }
    return 1.0;
  }
}

/** Maps a hardware zoom ratio back to a 0..1 slider fraction. */
export function sliderFractionFromZoomRatio(
  ratio: number,
  minZoom: number,
  maxZoom: number
): number {
  const min = Number.isFinite(minZoom) ? minZoom : 1.0;
  const max = Number.isFinite(maxZoom) && maxZoom > min ? maxZoom : Math.max(min + 2, 5.0);
  const r = Math.min(max, Math.max(min, Number.isFinite(ratio) ? ratio : 1.0));

  if (r >= 1.0) {
    if (max <= 1.0) return 0.5;
    return 0.5 + ((r - 1.0) / (max - 1.0)) * 0.5;
  } else {
    if (min >= 1.0) return 0.5;
    return ((r - min) / (1.0 - min)) * 0.5;
  }
}

/** True when a zoom drag crosses an integer boundary (1x → 2x), for haptic ticks. */
export function crossedIntegerZoomBoundary(previous: number, next: number): boolean {
  return Math.floor(previous + 1e-6) !== Math.floor(next + 1e-6);
}


/**
 * Calculates zoom ratio from a jog-dial slider drag.
 * 0.5 is the neutral center position.
 * fraction > 0.5 increases from startRatio towards maxZoom.
 * fraction < 0.5 decreases from startRatio down towards minZoom (clamped at 1.0 if minZoom >= 1.0).
 */
export function zoomRatioFromJogDrag(
  fraction: number,
  startRatio: number,
  minZoom: number,
  maxZoom: number
): number {
  const min = Number.isFinite(minZoom) ? minZoom : 1.0;
  const max = Number.isFinite(maxZoom) && maxZoom > min ? maxZoom : Math.max(min + 2, 5.0);
  const baseRatio = Math.min(max, Math.max(min, Number.isFinite(startRatio) ? startRatio : 1.0));
  const f = Math.min(1, Math.max(0, Number.isFinite(fraction) ? fraction : 0.5));

  if (f >= 0.5) {
    const progress = (f - 0.5) / 0.5;
    const newRatio = baseRatio + progress * (max - baseRatio);
    return Math.round(newRatio * 10) / 10;
  } else {
    const progress = (0.5 - f) / 0.5;
    const effectiveMin = min < 1.0 ? min : 1.0;
    const newRatio = Math.max(effectiveMin, baseRatio - progress * (baseRatio - effectiveMin));
    return Math.round(newRatio * 10) / 10;
  }
}
