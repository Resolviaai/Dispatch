/**
 * Dispatch Curated Creator Font Catalog & Font Management Engine
 *
 * Implements:
 * 1. Curated 20-25 creator fonts organized by use case
 * 2. Real supported weight resolution (no fake weights)
 * 3. Custom font imports via /api/fonts/import & FontFace API
 * 4. Recent & favorite font caching in localStorage
 */

export type FontCategoryKey =
  | 'all'
  | 'bold'
  | 'clean'
  | 'gaming'
  | 'decorative'
  | 'language'
  | 'favorites'
  | 'custom';

export interface FontCategory {
  key: FontCategoryKey;
  label: string;
}

export const FONT_CATEGORIES: FontCategory[] = [
  { key: 'all', label: 'All Fonts' },
  { key: 'bold', label: 'Bold / Captions' },
  { key: 'clean', label: 'Clean' },
  { key: 'gaming', label: 'Gaming / Comedy' },
  { key: 'decorative', label: 'Decorative' },
  { key: 'language', label: 'Language Support' },
  { key: 'favorites', label: 'Favorites' },
  { key: 'custom', label: 'Custom' },
];

export interface FontEntry {
  family: string;
  category: string;
  categoryKey: FontCategoryKey;
  weights: string[]; // Strict list of actual available weights (e.g. ['400'] or ['400', '700'])
  isCustom?: boolean;
  filename?: string;
  url?: string;
  license?: string;
}

export const CURATED_FONTS: FontEntry[] = [
  // 1. Bold / Captions
  {
    family: 'Impact',
    category: 'Bold / Captions',
    categoryKey: 'bold',
    weights: ['400'],
    license: 'System / Core Web Font',
  },
  {
    family: 'Anton',
    category: 'Bold / Captions',
    categoryKey: 'bold',
    weights: ['400'],
    filename: 'Anton-Regular.ttf',
    url: '/api/fonts/Anton-Regular.ttf',
    license: 'OFL 1.1',
  },
  {
    family: 'Archivo Black',
    category: 'Bold / Captions',
    categoryKey: 'bold',
    weights: ['400'],
    filename: 'ArchivoBlack-Regular.ttf',
    url: '/api/fonts/ArchivoBlack-Regular.ttf',
    license: 'OFL 1.1',
  },
  {
    family: 'Montserrat',
    category: 'Bold / Captions',
    categoryKey: 'bold',
    weights: ['400', '500', '600', '700', '800', '900'],
    filename: 'Montserrat-Variable.ttf',
    url: '/api/fonts/Montserrat-Variable.ttf',
    license: 'OFL 1.1',
  },
  {
    family: 'Oswald',
    category: 'Bold / Captions',
    categoryKey: 'bold',
    weights: ['400', '500', '600', '700'],
    filename: 'Oswald-Variable.ttf',
    url: '/api/fonts/Oswald-Variable.ttf',
    license: 'OFL 1.1',
  },
  {
    family: 'Barlow Condensed',
    category: 'Bold / Captions',
    categoryKey: 'bold',
    weights: ['400', '600', '700', '800', '900'],
    filename: 'BarlowCondensed-Bold.ttf',
    url: '/api/fonts/BarlowCondensed-Bold.ttf',
    license: 'OFL 1.1',
  },
  {
    family: 'League Spartan',
    category: 'Bold / Captions',
    categoryKey: 'bold',
    weights: ['400', '600', '700', '800', '900'],
    filename: 'LeagueSpartan-Variable.ttf',
    url: '/api/fonts/LeagueSpartan-Variable.ttf',
    license: 'OFL 1.1',
  },

  // 2. Clean
  {
    family: 'Inter',
    category: 'Clean',
    categoryKey: 'clean',
    weights: ['400', '500', '600', '700', '800', '900'],
    filename: 'Inter-Variable.ttf',
    url: '/api/fonts/Inter-Variable.ttf',
    license: 'OFL 1.1',
  },
  {
    family: 'Poppins',
    category: 'Clean',
    categoryKey: 'clean',
    weights: ['400', '500', '600', '700', '800', '900'],
    filename: 'Poppins-Bold.ttf',
    url: '/api/fonts/Poppins-Bold.ttf',
    license: 'OFL 1.1',
  },
  {
    family: 'Roboto',
    category: 'Clean',
    categoryKey: 'clean',
    weights: ['400', '500', '700', '900'],
    filename: 'Roboto-Variable.ttf',
    url: '/api/fonts/Roboto-Variable.ttf',
    license: 'Apache 2.0',
  },
  {
    family: 'Open Sans',
    category: 'Clean',
    categoryKey: 'clean',
    weights: ['400', '600', '700', '800'],
    filename: 'OpenSans-Variable.ttf',
    url: '/api/fonts/OpenSans-Variable.ttf',
    license: 'OFL 1.1',
  },
  {
    family: 'Noto Sans',
    category: 'Clean',
    categoryKey: 'clean',
    weights: ['400', '600', '700', '800'],
    filename: 'NotoSans-Variable.ttf',
    url: '/api/fonts/NotoSans-Variable.ttf',
    license: 'OFL 1.1',
  },
  {
    family: 'Space Grotesk',
    category: 'Clean',
    categoryKey: 'clean',
    weights: ['400', '500', '600', '700'],
    filename: 'SpaceGrotesk-Variable.ttf',
    url: '/api/fonts/SpaceGrotesk-Variable.ttf',
    license: 'OFL 1.1',
  },

  // 3. Gaming / Comedy
  {
    family: 'Bangers',
    category: 'Gaming / Comedy',
    categoryKey: 'gaming',
    weights: ['400'],
    filename: 'Bangers-Regular.ttf',
    url: '/api/fonts/Bangers-Regular.ttf',
    license: 'OFL 1.1',
  },
  {
    family: 'Luckiest Guy',
    category: 'Gaming / Comedy',
    categoryKey: 'gaming',
    weights: ['400'],
    filename: 'LuckiestGuy-Regular.ttf',
    url: '/api/fonts/LuckiestGuy-Regular.ttf',
    license: 'Apache 2.0',
  },
  {
    family: 'Russo One',
    category: 'Gaming / Comedy',
    categoryKey: 'gaming',
    weights: ['400'],
    filename: 'RussoOne-Regular.ttf',
    url: '/api/fonts/RussoOne-Regular.ttf',
    license: 'OFL 1.1',
  },
  {
    family: 'Titan One',
    category: 'Gaming / Comedy',
    categoryKey: 'gaming',
    weights: ['400'],
    filename: 'TitanOne-Regular.ttf',
    url: '/api/fonts/TitanOne-Regular.ttf',
    license: 'OFL 1.1',
  },
  {
    family: 'Bungee',
    category: 'Gaming / Comedy',
    categoryKey: 'gaming',
    weights: ['400'],
    filename: 'Bungee-Regular.ttf',
    url: '/api/fonts/Bungee-Regular.ttf',
    license: 'OFL 1.1',
  },

  // 4. Decorative
  {
    family: 'Bebas Neue',
    category: 'Decorative',
    categoryKey: 'decorative',
    weights: ['400'],
    filename: 'BebasNeue-Regular.ttf',
    url: '/api/fonts/BebasNeue-Regular.ttf',
    license: 'OFL 1.1',
  },
  {
    family: 'Permanent Marker',
    category: 'Decorative',
    categoryKey: 'decorative',
    weights: ['400'],
    filename: 'PermanentMarker-Regular.ttf',
    url: '/api/fonts/PermanentMarker-Regular.ttf',
    license: 'Apache 2.0',
  },

  // 5. Language Support
  {
    family: 'Noto Sans Devanagari',
    category: 'Language Support',
    categoryKey: 'language',
    weights: ['400', '600', '700', '800'],
    filename: 'NotoSansDevanagari-Variable.ttf',
    url: '/api/fonts/NotoSansDevanagari-Variable.ttf',
    license: 'OFL 1.1',
  },
];

// In-memory cache for dynamically loaded fonts
const loadedFontFaceFamilies = new Set<string>();

/**
 * Ensure a font asset is registered in document.fonts via the Web FontFace API.
 */
export async function loadFontFace(family: string, fontUrl: string): Promise<boolean> {
  if (loadedFontFaceFamilies.has(family)) return true;
  if (typeof window === 'undefined' || !('FontFace' in window)) return false;

  try {
    const font = new FontFace(family, `url(${fontUrl})`);
    const loaded = await font.load();
    document.fonts.add(loaded);
    loadedFontFaceFamilies.add(family);
    return true;
  } catch (err) {
    console.warn(`[FontLoader] Could not load @font-face for ${family}:`, err);
    return false;
  }
}

/**
 * Fetch all available fonts from backend API, merging custom uploaded fonts with curated defaults.
 */
export async function fetchFontCatalog(apiBase: string = ''): Promise<FontEntry[]> {
  try {
    const res = await fetch(`${apiBase}/api/fonts`);
    if (!res.ok) return CURATED_FONTS;
    const data = await res.json();
    const serverFonts: any[] = data.fonts || [];

    const customEntries: FontEntry[] = serverFonts
      .filter((sf) => sf.is_custom)
      .map((sf) => ({
        family: sf.family,
        category: 'Custom',
        categoryKey: 'custom',
        weights: Array.isArray(sf.weights) && sf.weights.length > 0 ? sf.weights : ['400'],
        isCustom: true,
        filename: sf.filename,
        url: sf.url ? (sf.url.startsWith('http') ? sf.url : `${apiBase}${sf.url}`) : undefined,
        license: sf.license || 'Custom Imported',
      }));

    // Auto-load all custom fonts into document.fonts
    for (const cf of customEntries) {
      if (cf.url) {
        loadFontFace(cf.family, cf.url).catch(() => {});
      }
    }

    // Merge: Put custom fonts first, then curated
    return [...customEntries, ...CURATED_FONTS];
  } catch (err) {
    console.warn('[FontCatalog] Failed to fetch server fonts, falling back to curated list:', err);
    return CURATED_FONTS;
  }
}

/**
 * Upload a custom TTF/OTF font to the backend and register it in document.fonts.
 */
export async function importCustomFontFile(file: File, apiBase: string = ''): Promise<FontEntry> {
  const formData = new FormData();
  formData.append('file', file);

  const res = await fetch(`${apiBase}/api/fonts/import`, {
    method: 'POST',
    body: formData,
  });

  if (!res.ok) {
    const errData = await res.json().catch(() => ({ detail: 'Failed to import font' }));
    throw new Error(errData.detail || 'Failed to import font file');
  }

  const result = await res.json();
  const fontData = result.font;
  const entry: FontEntry = {
    family: fontData.family,
    category: 'Custom',
    categoryKey: 'custom',
    weights: fontData.weights || ['400'],
    isCustom: true,
    filename: fontData.filename,
    url: fontData.url ? (fontData.url.startsWith('http') ? fontData.url : `${apiBase}${fontData.url}`) : undefined,
    license: fontData.license || 'Custom Imported',
  };

  // Register in browser document.fonts immediately
  if (entry.url) {
    await loadFontFace(entry.family, entry.url);
  }

  return entry;
}

/**
 * Get real supported weights for a font family.
 */
export function getSupportedWeights(family: string, catalog: FontEntry[] = CURATED_FONTS): string[] {
  const found = catalog.find((f) => f.family.toLowerCase() === family.toLowerCase());
  return found?.weights && found.weights.length > 0 ? found.weights : ['400', 'bold'];
}

/**
 * Return closest valid weight for a chosen font.
 */
export function getClosestSupportedWeight(family: string, currentWeight: string = 'bold', catalog: FontEntry[] = CURATED_FONTS): string {
  const weights = getSupportedWeights(family, catalog);
  if (weights.includes(currentWeight)) return currentWeight;

  // Map common strings to numbers
  const norm = (w: string): number => {
    if (w === 'bold') return 700;
    if (w === 'black' || w === 'extrabold') return 900;
    if (w === 'semibold') return 600;
    if (w === 'medium') return 500;
    if (w === 'normal' || w === 'regular') return 400;
    const num = parseInt(w, 10);
    return isNaN(num) ? 400 : num;
  };

  const targetNum = norm(currentWeight);
  let best = weights[0];
  let bestDiff = 9999;
  for (const w of weights) {
    const diff = Math.abs(norm(w) - targetNum);
    if (diff < bestDiff) {
      bestDiff = diff;
      best = w;
    }
  }
  return best;
}

const RECENT_KEY = 'dispatch_recent_fonts';
const FAVORITES_KEY = 'dispatch_favorite_fonts';

export function getRecentFonts(): string[] {
  try {
    const raw = localStorage.getItem(RECENT_KEY);
    return raw ? JSON.parse(raw) : ['Impact', 'Montserrat', 'Anton'];
  } catch {
    return ['Impact', 'Montserrat', 'Anton'];
  }
}

export function addRecentFont(family: string): void {
  try {
    const recents = getRecentFonts().filter((f) => f.toLowerCase() !== family.toLowerCase());
    recents.unshift(family);
    localStorage.setItem(RECENT_KEY, JSON.stringify(recents.slice(0, 8)));
  } catch {}
}

export function getFavoriteFonts(): string[] {
  try {
    const raw = localStorage.getItem(FAVORITES_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

export function toggleFavoriteFont(family: string): boolean {
  try {
    const favs = getFavoriteFonts();
    const exists = favs.some((f) => f.toLowerCase() === family.toLowerCase());
    let next: string[];
    if (exists) {
      next = favs.filter((f) => f.toLowerCase() !== family.toLowerCase());
    } else {
      next = [...favs, family];
    }
    localStorage.setItem(FAVORITES_KEY, JSON.stringify(next));
    return !exists;
  } catch {
    return false;
  }
}
