import type { ExposureInfo, ExposureResultEvent, FocusResultEvent, CameraStateEvent } from '../bridge';

export type FocusStatus = 'idle' | 'focusing' | 'locking' | 'unlocking' | 'success' | 'failed' | 'locked';

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
  if (result.success && Number.isInteger(result.index)) {
    return { index: result.index, error: null };
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

  const lockConfirmed = current.status === 'locking' && result.success && result.isLocked;
  if (current.status === 'locking') {
    return {
      ...current,
      status: lockConfirmed ? 'locked' : 'failed',
      isLocked: lockConfirmed,
      hasAf: result.hasAf ?? current.hasAf,
      hasAe: result.hasAe ?? current.hasAe,
      error: lockConfirmed ? undefined : (result.error || 'Camera did not confirm the AE/AF lock'),
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
    return { ...current, status: 'idle', isLocked: false, error: undefined };
  }
  return {
    ...current,
    status: current.isLocked ? 'locked' : 'failed',
    error: 'Camera did not confirm unlock. Try again.',
  };
}

export function applyNativeLockState(
  current: FocusExposureState | null,
  isAeAfLocked: boolean
): FocusExposureState | null {
  if (!current) return current;
  if (isAeAfLocked) {
    return { ...current, status: 'locked', isLocked: true, error: undefined };
  }
  if (current.isLocked || current.status === 'locking' || current.status === 'unlocking') {
    return { ...current, status: current.status === 'failed' ? 'failed' : 'idle', isLocked: false };
  }
  return current;
}

export function isCameraLifecycleTransition(
  previous: CameraStateEvent,
  next: CameraStateEvent
): boolean {
  return previous.lensFacing !== next.lensFacing || next.status !== 'ready';
}
