/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Dispatch Design System v2 Semantic Tokens
        'studio-canvas': 'var(--studio-canvas, #0D0F12)',
        'studio-surface': 'var(--studio-surface, #16181C)',
        'studio-surface-elevated': 'var(--studio-surface-elevated, #1C1F24)',
        'studio-recessed': 'var(--studio-recessed, #23262C)',
        'studio-recessed-deep': 'var(--studio-recessed-deep, #1A1C21)',
        'studio-border': 'var(--studio-border, #2E3239)',
        'studio-border-subtle': 'var(--studio-border-subtle, #242830)',
        'studio-border-strong': 'var(--studio-border-strong, #3E434D)',
        'studio-text-primary': 'var(--studio-text-primary, #F3F4F6)',
        'studio-text-secondary': 'var(--studio-text-secondary, #A3AAB5)',
        'studio-text-muted': 'var(--studio-text-muted, #747D8A)',

        // Preserved tokens for backwards compatibility
        studio: '#0D0F12',
        background: '#0D0F12',
        'surface-100': '#16181C',
        'surface-200': '#1C1F24',
        'surface-300': '#23262C',
        border: '#2E3239',
        'border-subtle': '#242830',
        'border-strong': '#3E434D',
        primary: {
          DEFAULT: '#2563EB',
          hover: '#1D4ED8',
          soft: 'rgba(37, 99, 235, 0.12)',
        },
        'text-main': '#F3F4F6',
        'text-secondary': '#A3AAB5',
        'text-muted': '#747D8A',
        success: '#10B981',
        warning: '#EAB308',
        danger: '#F43F5E',
      },
      fontFamily: {
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'monospace'],
        sans: ['-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', '"Helvetica Neue"', 'Arial', 'sans-serif'],
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
