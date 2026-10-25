/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Dispatch Design System v3 — semantic tokens backed by CSS variables.
        // Dark values are the canonical fallbacks; [data-theme="light"] swaps them.
        canvas: 'var(--ds-canvas, #161616)',
        surface: 'var(--ds-surface, #1C1C1C)',
        'surface-2': 'var(--ds-surface-2, #232323)',
        'surface-3': 'var(--ds-surface-3, #282828)',
        border: 'var(--ds-border, #2E2E2E)',
        'border-strong': 'var(--ds-border-strong, #3E3E3E)',
        'text-main': 'var(--ds-text, #F5F5F5)',
        'text-secondary': 'var(--ds-text-2, #A3A3A3)',
        'text-muted': 'var(--ds-text-3, #737373)',
        // Accent stays #2563EB in BOTH themes (v3 §2.8), so it can stay a literal
        // hex — Tailwind alpha modifiers (bg-primary/15) only work on real colors.
        primary: {
          DEFAULT: '#2563EB',
          hover: '#1D4ED8',
          soft: 'rgba(37, 99, 235, 0.12)',
        },
        // Semantic colors swap per theme (v3 §2.2). Never use alpha modifiers on
        // them — use the .chip-* utilities in index.css for tinted surfaces.
        success: 'var(--ds-success, #22C55E)',
        warning: 'var(--ds-warning, #F59E0B)',
        danger: 'var(--ds-danger, #EF4444)',
        info: 'var(--ds-info, #38BDF8)',
        overlay: 'var(--ds-overlay, rgba(0, 0, 0, 0.6))',

        // Legacy aliases (v2 names kept alive for the caption studio and any
        // not-yet-migrated components). All point at the same v3 variables.
        studio: 'var(--ds-canvas, #161616)',
        background: 'var(--ds-canvas, #161616)',
        'surface-100': 'var(--ds-surface, #1C1C1C)',
        'surface-200': 'var(--ds-surface-2, #232323)',
        'surface-300': 'var(--ds-surface-3, #282828)',
        'border-subtle': 'var(--ds-border, #2E2E2E)',
        'studio-canvas': 'var(--ds-canvas, #161616)',
        'studio-surface': 'var(--ds-surface, #1C1C1C)',
        'studio-surface-elevated': 'var(--ds-surface-2, #232323)',
        'studio-recessed': 'var(--ds-surface-3, #282828)',
        'studio-recessed-deep': 'var(--ds-canvas, #161616)',
        'studio-border': 'var(--ds-border, #2E2E2E)',
        'studio-border-subtle': 'var(--ds-border, #2E2E2E)',
        'studio-border-strong': 'var(--ds-border-strong, #3E3E3E)',
        'studio-text-primary': 'var(--ds-text, #F5F5F5)',
        'studio-text-secondary': 'var(--ds-text-2, #A3A3A3)',
        'studio-text-muted': 'var(--ds-text-3, #737373)',
      },
      fontFamily: {
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'monospace'],
        sans: ['"Inter"', '-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'Roboto', '"Helvetica Neue"', 'Arial', 'sans-serif'],
      },
      boxShadow: {
        'hero-glow': '0 0 20px rgba(37, 99, 235, 0.25)',
        'hero-sm': '0 0 10px rgba(37, 99, 235, 0.20)',
        'card-subtle': '0 4px 20px -2px rgba(0, 0, 0, 0.4)',
      }
    },
  },
  plugins: [],
}
