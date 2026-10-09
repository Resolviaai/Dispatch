/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        studio: '#161616',
        background: '#161616',
        'surface-100': '#1C1C1C',
        'surface-200': '#232323',
        'surface-300': '#282828',
        border: '#2E2E2E',
        'border-subtle': '#232323',
        'border-strong': '#383838',
        primary: {
          DEFAULT: '#2563EB',
          hover: '#3B82F6',
          soft: 'rgba(37, 99, 235, 0.15)',
        },
        'text-main': '#EDEDED',
        'text-secondary': '#A0A0A0',
        'text-muted': '#707070',
        success: '#22C55E',
        warning: '#F59E0B',
        danger: '#EF4444',
      },
      fontFamily: {
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'monospace'],
        sans: ['-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', '"Helvetica Neue"', 'Arial', 'sans-serif'],
      },
      boxShadow: {
        'hero-glow': '0 0 20px rgba(37, 99, 235, 0.25)',
        'hero-sm': '0 0 10px rgba(37, 99, 235, 0.20)',
      }
    },
  },
  plugins: [],
}
