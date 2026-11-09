const DEFAULT_LENS_KEY = 'dispatch_default_lens';

export function getDefaultLensPref(): 'back' | 'front' {
  if (typeof localStorage === 'undefined') return 'back';
  return localStorage.getItem(DEFAULT_LENS_KEY) === 'front' ? 'front' : 'back';
}

export function setDefaultLensPref(lens: 'back' | 'front') {
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(DEFAULT_LENS_KEY, lens);
  }
}
