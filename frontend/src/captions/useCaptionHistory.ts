import { useState, useCallback, useRef } from 'react';
import { CaptionTrack } from './types';

/**
 * High-performance undo/redo history hook for caption tracks.
 * 
 * Supports:
 * - Transient updates during active gestures (drag, resize, trim) with zero cloning overhead.
 * - Single immutable commit boundary on gesture completion (`pointerup`).
 * - Reactive canUndo and canRedo state tracking.
 * - History isolation bounded strictly to 50 snapshots.
 */
export function useCaptionHistory(initialTrack: CaptionTrack | null) {
  const [track, setInternalTrack] = useState<CaptionTrack | null>(initialTrack);
  const historyRef = useRef<CaptionTrack[]>(initialTrack ? [JSON.parse(JSON.stringify(initialTrack))] : []);
  const indexRef = useRef<number>(0);

  const [canUndo, setCanUndo] = useState<boolean>(false);
  const [canRedo, setCanRedo] = useState<boolean>(false);

  const syncFlags = useCallback(() => {
    setCanUndo(indexRef.current > 0);
    setCanRedo(indexRef.current < historyRef.current.length - 1);
  }, []);

  /**
   * Set caption track.
   * @param newTrack The updated caption track.
   * @param commitToHistory If false, updates track for fluid preview without appending a history snapshot.
   */
  const setTrack = useCallback((newTrack: CaptionTrack, commitToHistory: boolean = true) => {
    if (!commitToHistory) {
      // Fluid preview update: update state directly without JSON cloning or history mutation
      setInternalTrack(newTrack);
      return;
    }

    const cloned = JSON.parse(JSON.stringify(newTrack));
    const newHistory = historyRef.current.slice(0, indexRef.current + 1);
    newHistory.push(cloned);
    if (newHistory.length > 50) {
      newHistory.shift();
    }
    historyRef.current = newHistory;
    indexRef.current = newHistory.length - 1;
    setInternalTrack(cloned);
    syncFlags();
  }, [syncFlags]);

  /**
   * Explicit commit at gesture end.
   */
  const commitTrack = useCallback((finalTrack: CaptionTrack) => {
    setTrack(finalTrack, true);
  }, [setTrack]);

  /**
   * Explicit transient preview during active gesture.
   */
  const setTransientTrack = useCallback((previewTrack: CaptionTrack) => {
    setTrack(previewTrack, false);
  }, [setTrack]);

  const undo = useCallback((): CaptionTrack | null => {
    if (indexRef.current > 0) {
      indexRef.current -= 1;
      const prev = JSON.parse(JSON.stringify(historyRef.current[indexRef.current]));
      setInternalTrack(prev);
      syncFlags();
      return prev;
    }
    return null;
  }, [syncFlags]);

  const redo = useCallback((): CaptionTrack | null => {
    if (indexRef.current < historyRef.current.length - 1) {
      indexRef.current += 1;
      const next = JSON.parse(JSON.stringify(historyRef.current[indexRef.current]));
      setInternalTrack(next);
      syncFlags();
      return next;
    }
    return null;
  }, [syncFlags]);

  const resetHistory = useCallback((freshTrack: CaptionTrack) => {
    const cloned = JSON.parse(JSON.stringify(freshTrack));
    historyRef.current = [cloned];
    indexRef.current = 0;
    setInternalTrack(cloned);
    setCanUndo(false);
    setCanRedo(false);
  }, []);

  return {
    track,
    setTrack,
    commitTrack,
    setTransientTrack,
    undo,
    redo,
    canUndo,
    canRedo,
    resetHistory,
  };
}
