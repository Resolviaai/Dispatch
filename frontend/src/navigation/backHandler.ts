/**
 * Dispatch Unified Back-Stack Navigation System
 *
 * Implements a prioritized back-stack registry matching YouTube and Instagram UX:
 * 1. Priority 40+: Modals, bottom sheets, search overlays, and confirmation dialogs
 * 2. Priority 30: Nested editing workstations (e.g. Studio, Publish)
 * 3. Priority 20: Screen detail views (e.g. Clip Detail, Session Detail)
 * 4. Priority 10: Tab history stack (e.g. Settings -> Clips -> Sessions -> Record)
 * 5. Priority 0: Root exit guard (handled natively via double-tap to exit toast)
 */

import { useEffect, useRef } from 'react';

export type BackHandlerFn = () => boolean;

interface RegisteredHandler {
  id: string;
  priority: number;
  handler: BackHandlerFn;
}

const handlers: RegisteredHandler[] = [];

/**
 * Register a back-press handler with an explicit priority.
 * Handlers returning `true` indicate the event was consumed and prevent further propagation.
 */
export function registerBackHandler(id: string, priority: number, handler: BackHandlerFn): () => void {
  const existingIdx = handlers.findIndex((h) => h.id === id);
  if (existingIdx !== -1) {
    handlers.splice(existingIdx, 1);
  }
  handlers.push({ id, priority, handler });
  handlers.sort((a, b) => b.priority - a.priority);

  return () => {
    const idx = handlers.findIndex((h) => h.id === id);
    if (idx !== -1) {
      handlers.splice(idx, 1);
    }
  };
}

/**
 * Executes the highest priority registered handler.
 * Returns true if the back action was consumed, false if at root.
 */
export function executeBack(): boolean {
  for (const reg of handlers) {
    try {
      const handled = reg.handler();
      if (handled) return true;
    } catch (e) {
      console.warn('[BackHandler] Error executing handler', reg.id, e);
    }
  }
  return false;
}

/**
 * React hook to register a back-press handler during a component's lifecycle.
 */
export function useBackHandler(
  id: string,
  priority: number,
  handler: BackHandlerFn,
  active: boolean = true
): void {
  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  useEffect(() => {
    if (!active) return;
    const unregister = registerBackHandler(id, priority, () => handlerRef.current());
    return unregister;
  }, [id, priority, active]);
}

// Hook into global window object for Android WebView bridge evaluation
if (typeof window !== 'undefined') {
  (window as any).__dispatchBackHandler = executeBack;

  // Support Desktop Escape key to trigger back navigation
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      executeBack();
    }
  });
}
