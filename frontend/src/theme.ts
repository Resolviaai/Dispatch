/**
 * Dispatch theme manager (Design System v3 §2.5/§2.8).
 * Three preferences — device / dark / light — resolved onto <html data-theme>
 * before first paint so switching recolours instantly with no reload.
 */

export type ThemePreference = 'device' | 'dark' | 'light';

const STORAGE_KEY = 'dispatch_theme';

export function getThemePreference(): ThemePreference {
  if (typeof localStorage === 'undefined') return 'device';
  const saved = localStorage.getItem(STORAGE_KEY);
  return saved === 'dark' || saved === 'light' || saved === 'device' ? saved : 'device';
}

function resolvedTheme(pref: ThemePreference): 'dark' | 'light' {
  if (pref === 'dark' || pref === 'light') return pref;
  if (typeof window !== 'undefined' && window.matchMedia) {
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }
  return 'dark';
}

export function applyTheme(pref: ThemePreference): 'dark' | 'light' {
  const resolved = resolvedTheme(pref);
  if (typeof document !== 'undefined') {
    document.documentElement.setAttribute('data-theme', resolved);
  }
  return resolved;
}

/** Persists the preference and recolours immediately (YouTube-style instant swap). */
export function setThemePreference(pref: ThemePreference): 'dark' | 'light' {
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(STORAGE_KEY, pref);
  }
  return applyTheme(pref);
}
