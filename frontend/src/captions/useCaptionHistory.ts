import { useState, useCallback, useRef } from 'react';
import { CaptionTrack } from './types';

export function useCaptionHistory(initialTrack: CaptionTrack | null) {
  const [track, setInternalTrack] = useState<CaptionTrack | null>(initialTrack);
  const historyRef = useRef<CaptionTrack[]>(initialTrack ? [JSON.parse(JSON.stringify(initialTrack))] : []);
  const indexRef = useRef<number>(0);

  const setTrack = useCallback((newTrack: CaptionTrack) => {
    const cloned = JSON.parse(JSON.stringify(newTrack));
    const newHistory = historyRef.current.slice(0, indexRef.current + 1);
    newHistory.push(cloned);
    if (newHistory.length > 50) newHistory.shift();
    historyRef.current = newHistory;
    indexRef.current = newHistory.length - 1;
    setInternalTrack(cloned);
  }, []);

  const undo = useCallback((): CaptionTrack | null => {
    if (indexRef.current > 0) {
      indexRef.current -= 1;
      const prev = JSON.parse(JSON.stringify(historyRef.current[indexRef.current]));
      setInternalTrack(prev);
      return prev;
    }
    return null;
  }, []);

  const redo = useCallback((): CaptionTrack | null => {
    if (indexRef.current < historyRef.current.length - 1) {
      indexRef.current += 1;
      const next = JSON.parse(JSON.stringify(historyRef.current[indexRef.current]));
      setInternalTrack(next);
      return next;
    }
    return null;
  }, []);

  const resetHistory = useCallback((freshTrack: CaptionTrack) => {
    const cloned = JSON.parse(JSON.stringify(freshTrack));
    historyRef.current = [cloned];
    indexRef.current = 0;
    setInternalTrack(cloned);
  }, []);

  return {
    track,
    setTrack,
    undo,
    redo,
    canUndo: indexRef.current > 0,
    canRedo: indexRef.current < historyRef.current.length - 1,
    resetHistory
  };
}
