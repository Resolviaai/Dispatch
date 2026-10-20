import React, { useState, useEffect, useRef, useCallback } from 'react';
import { X, Check, Pipette, RotateCcw } from 'lucide-react';

export interface ColorPickerModalProps {
  isOpen: boolean;
  color: string;
  onChange: (color: string) => void;
  onClose: () => void;
  title?: string;
  enableAlpha?: boolean;
}

// Color math helpers
function clamp(val: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, val));
}

function parseHex(hexStr: string): { r: number; g: number; b: number; a: number } {
  let hex = hexStr.trim().replace(/^#/, '');
  if (hex.length === 3) {
    hex = hex.split('').map((c) => c + c).join('') + 'ff';
  } else if (hex.length === 4) {
    hex = hex.split('').map((c) => c + c).join('');
  } else if (hex.length === 6) {
    hex = hex + 'ff';
  } else if (hex.length !== 8) {
    return { r: 255, g: 255, b: 255, a: 1 };
  }

  const r = parseInt(hex.substring(0, 2), 16) || 0;
  const g = parseInt(hex.substring(2, 4), 16) || 0;
  const b = parseInt(hex.substring(4, 6), 16) || 0;
  const a = parseInt(hex.substring(6, 8), 16) / 255;

  return { r, g, b, a: isNaN(a) ? 1 : a };
}

function rgbToHex(r: number, g: number, b: number, a: number = 1, includeAlpha: boolean = false): string {
  const toHex = (n: number) => clamp(Math.round(n), 0, 255).toString(16).padStart(2, '0').toUpperCase();
  const hex = `#${toHex(r)}${toHex(g)}${toHex(b)}`;
  if (includeAlpha && a < 0.999) {
    return `${hex}${toHex(a * 255)}`;
  }
  return hex;
}

function rgbToHsv(r: number, g: number, b: number): { h: number; s: number; v: number } {
  const rn = r / 255;
  const gn = g / 255;
  const bn = b / 255;
  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  const delta = max - min;

  let h = 0;
  if (delta !== 0) {
    if (max === rn) {
      h = ((gn - bn) / delta) % 6;
    } else if (max === gn) {
      h = (bn - rn) / delta + 2;
    } else {
      h = (rn - gn) / delta + 4;
    }
    h = Math.round(h * 60);
    if (h < 0) h += 360;
  }

  const s = max === 0 ? 0 : delta / max;
  const v = max;

  return { h, s, v };
}

function hsvToRgb(h: number, s: number, v: number): { r: number; g: number; b: number } {
  const c = v * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = v - c;

  let rPrime = 0;
  let gPrime = 0;
  let bPrime = 0;

  if (h >= 0 && h < 60) {
    rPrime = c; gPrime = x; bPrime = 0;
  } else if (h >= 60 && h < 120) {
    rPrime = x; gPrime = c; bPrime = 0;
  } else if (h >= 120 && h < 180) {
    rPrime = 0; gPrime = c; bPrime = x;
  } else if (h >= 180 && h < 240) {
    rPrime = 0; gPrime = x; bPrime = c;
  } else if (h >= 240 && h < 300) {
    rPrime = x; gPrime = 0; bPrime = c;
  } else {
    rPrime = c; gPrime = 0; bPrime = x;
  }

  return {
    r: Math.round((rPrime + m) * 255),
    g: Math.round((gPrime + m) * 255),
    b: Math.round((bPrime + m) * 255),
  };
}

const RECENT_COLORS_KEY = 'dispatch_recent_colors';
const DEFAULT_RECENTS = [
  '#FFFFFF', '#000000', '#FFE600', '#2563EB', '#22C55E', '#EF4444',
  '#00F0FF', '#EC4899', '#8B5CF6', '#F97316'
];

export const ColorPickerModal: React.FC<ColorPickerModalProps> = ({
  isOpen,
  color,
  onChange,
  onClose,
  title = 'Custom Color',
  enableAlpha = false,
}) => {
  const [initialColor] = useState(color);
  const [hsv, setHsv] = useState<{ h: number; s: number; v: number }>({ h: 0, s: 1, v: 1 });
  const [alpha, setAlpha] = useState<number>(1);
  const [hexInput, setHexInput] = useState<string>(color);
  const [isHexValid, setIsHexValid] = useState<boolean>(true);
  const [recentColors, setRecentColors] = useState<string[]>(DEFAULT_RECENTS);

  const spectrumRef = useRef<HTMLDivElement | null>(null);
  const isDraggingSpectrum = useRef(false);

  // Load recents on mount
  useEffect(() => {
    try {
      const stored = localStorage.getItem(RECENT_COLORS_KEY);
      if (stored) {
        const parsed = JSON.parse(stored);
        if (Array.isArray(parsed) && parsed.length > 0) {
          setRecentColors(parsed.slice(0, 10));
        }
      }
    } catch {}
  }, []);

  // Sync internal state with external color prop
  useEffect(() => {
    if (color) {
      const { r, g, b, a } = parseHex(color);
      const newHsv = rgbToHsv(r, g, b);
      setHsv(newHsv);
      setAlpha(a);
      setHexInput(rgbToHex(r, g, b, a, enableAlpha));
      setIsHexValid(true);
    }
  }, [color, enableAlpha]);

  const saveRecentColor = useCallback((newColor: string) => {
    try {
      const current = recentColors.filter((c) => c.toUpperCase() !== newColor.toUpperCase());
      const updated = [newColor, ...current].slice(0, 10);
      setRecentColors(updated);
      localStorage.setItem(RECENT_COLORS_KEY, JSON.stringify(updated));
    } catch {}
  }, [recentColors]);

  const updateColorFromHsv = useCallback(
    (newHsv: { h: number; s: number; v: number }, newAlpha: number) => {
      const rgb = hsvToRgb(newHsv.h, newHsv.s, newHsv.v);
      const hex = rgbToHex(rgb.r, rgb.g, rgb.b, newAlpha, enableAlpha);
      setHexInput(hex);
      setIsHexValid(true);
      onChange(hex);
    },
    [onChange, enableAlpha]
  );

  // Spectrum 2D pointer handling
  const handleSpectrumMove = useCallback(
    (clientX: number, clientY: number) => {
      if (!spectrumRef.current) return;
      const rect = spectrumRef.current.getBoundingClientRect();
      const x = clamp((clientX - rect.left) / rect.width, 0, 1);
      const y = clamp((clientY - rect.top) / rect.height, 0, 1);

      const s = x;
      const v = 1 - y;
      const newHsv = { ...hsv, s, v };
      setHsv(newHsv);
      updateColorFromHsv(newHsv, alpha);
    },
    [hsv, alpha, updateColorFromHsv]
  );

  const handleSpectrumPointerDown = (e: React.PointerEvent) => {
    e.preventDefault();
    e.stopPropagation();
    isDraggingSpectrum.current = true;
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    handleSpectrumMove(e.clientX, e.clientY);
  };

  const handleSpectrumPointerMove = (e: React.PointerEvent) => {
    if (!isDraggingSpectrum.current) return;
    handleSpectrumMove(e.clientX, e.clientY);
  };

  const handleSpectrumPointerUp = (e: React.PointerEvent) => {
    if (isDraggingSpectrum.current) {
      isDraggingSpectrum.current = false;
      try {
        (e.target as HTMLElement).releasePointerCapture(e.pointerId);
      } catch {}
    }
  };

  // Hue slider change
  const handleHueChange = (newHue: number) => {
    const newHsv = { ...hsv, h: newHue };
    setHsv(newHsv);
    updateColorFromHsv(newHsv, alpha);
  };

  // Alpha slider change
  const handleAlphaChange = (newAlpha: number) => {
    setAlpha(newAlpha);
    updateColorFromHsv(hsv, newAlpha);
  };

  // Hex input change & validation
  const handleHexInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = e.target.value;
    setHexInput(raw);
    const cleaned = raw.trim().replace(/^#/, '');
    if (/^[0-9a-fA-F]{3}$|^[0-9a-fA-F]{6}$|^[0-9a-fA-F]{8}$/.test(cleaned)) {
      setIsHexValid(true);
      const { r, g, b, a } = parseHex(cleaned);
      const newHsv = rgbToHsv(r, g, b);
      setHsv(newHsv);
      setAlpha(a);
      const formatted = rgbToHex(r, g, b, a, enableAlpha);
      onChange(formatted);
    } else {
      setIsHexValid(false);
    }
  };

  // Numeric RGB change
  const handleRgbChange = (channel: 'r' | 'g' | 'b', val: number) => {
    const currentRgb = hsvToRgb(hsv.h, hsv.s, hsv.v);
    const newRgb = {
      ...currentRgb,
      [channel]: clamp(isNaN(val) ? 0 : val, 0, 255),
    };
    const newHsv = rgbToHsv(newRgb.r, newRgb.g, newRgb.b);
    setHsv(newHsv);
    updateColorFromHsv(newHsv, alpha);
  };

  const handleDone = () => {
    const currentRgb = hsvToRgb(hsv.h, hsv.s, hsv.v);
    const finalHex = rgbToHex(currentRgb.r, currentRgb.g, currentRgb.b, alpha, enableAlpha);
    saveRecentColor(finalHex);
    onClose();
  };

  const handleReset = () => {
    onChange(initialColor);
    const { r, g, b, a } = parseHex(initialColor);
    const newHsv = rgbToHsv(r, g, b);
    setHsv(newHsv);
    setAlpha(a);
    setHexInput(rgbToHex(r, g, b, a, enableAlpha));
    setIsHexValid(true);
  };

  if (!isOpen) return null;

  const currentRgb = hsvToRgb(hsv.h, hsv.s, hsv.v);
  const currentHex = rgbToHex(currentRgb.r, currentRgb.g, currentRgb.b, alpha, enableAlpha);
  const pureHueRgb = hsvToRgb(hsv.h, 1, 1);
  const pureHueHex = rgbToHex(pureHueRgb.r, pureHueRgb.g, pureHueRgb.b);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-3 bg-black/75 backdrop-blur-sm animate-in fade-in duration-150"
      onClick={onClose}
    >
      <div
        className="w-full max-w-[360px] bg-[#161616] border border-[#2E2E2E] rounded-2xl shadow-2xl overflow-hidden flex flex-col p-4 space-y-3.5 select-none"
        onClick={(e) => e.stopPropagation()}
        style={{ touchAction: 'pan-y' }}
      >
        {/* Header */}
        <div className="flex items-center justify-between pb-2 border-b border-[#2E2E2E]">
          <div className="flex items-center gap-2">
            <Pipette size={16} className="text-primary" />
            <span className="text-[14px] font-semibold text-text-main">{title}</span>
          </div>
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={handleReset}
              className="w-8 h-8 rounded-lg bg-[#232323] hover:bg-[#282828] border border-[#2E2E2E] flex items-center justify-center text-text-secondary hover:text-text-main active:scale-95 transition-all"
              title="Reset to original color"
              aria-label="Reset color"
            >
              <RotateCcw size={13} />
            </button>
            <button
              type="button"
              onClick={onClose}
              className="w-8 h-8 rounded-lg bg-[#232323] hover:bg-[#282828] border border-[#2E2E2E] flex items-center justify-center text-text-secondary hover:text-text-main active:scale-95 transition-all"
              title="Close"
              aria-label="Close"
            >
              <X size={15} />
            </button>
          </div>
        </div>

        {/* 2D Saturation/Brightness Spectrum Area */}
        <div
          ref={spectrumRef}
          onPointerDown={handleSpectrumPointerDown}
          onPointerMove={handleSpectrumPointerMove}
          onPointerUp={handleSpectrumPointerUp}
          className="relative w-full h-36 rounded-xl overflow-hidden cursor-crosshair border border-[#2E2E2E]"
          style={{
            backgroundColor: pureHueHex,
            touchAction: 'none',
          }}
        >
          {/* White to transparent horizontal gradient */}
          <div
            className="absolute inset-0"
            style={{
              background: 'linear-gradient(to right, #FFFFFF, transparent)',
            }}
          />
          {/* Black to transparent vertical gradient */}
          <div
            className="absolute inset-0"
            style={{
              background: 'linear-gradient(to top, #000000, transparent)',
            }}
          />
          {/* Reticle / Draggable Pointer */}
          <div
            className="absolute w-5 h-5 rounded-full border-2 border-white shadow-[0_0_4px_rgba(0,0,0,0.8)] -translate-x-1/2 -translate-y-1/2 pointer-events-none transition-transform"
            style={{
              left: `${hsv.s * 100}%`,
              top: `${(1 - hsv.v) * 100}%`,
              backgroundColor: currentHex,
            }}
          />
        </div>

        {/* Sliders Area (Hue + Alpha) */}
        <div className="space-y-2.5">
          {/* Hue Slider */}
          <div className="space-y-1">
            <div className="flex items-center justify-between text-[11px] text-text-secondary">
              <span>Hue</span>
              <span className="font-mono tabular-nums">{hsv.h}°</span>
            </div>
            <div className="relative h-6 rounded-lg overflow-hidden border border-[#2E2E2E] flex items-center">
              <input
                type="range"
                min={0}
                max={360}
                value={hsv.h}
                onChange={(e) => handleHueChange(Number(e.target.value))}
                className="w-full h-full opacity-0 cursor-pointer z-10"
              />
              <div
                className="absolute inset-0 pointer-events-none"
                style={{
                  background: 'linear-gradient(to right, #ff0000 0%, #ffff00 17%, #00ff00 33%, #00ffff 50%, #0000ff 67%, #ff00ff 83%, #ff0000 100%)',
                }}
              />
              <div
                className="absolute top-0 bottom-0 w-3 rounded bg-white shadow-md border border-black/40 pointer-events-none -translate-x-1/2"
                style={{ left: `${(hsv.h / 360) * 100}%` }}
              />
            </div>
          </div>

          {/* Optional Alpha Slider */}
          {enableAlpha && (
            <div className="space-y-1">
              <div className="flex items-center justify-between text-[11px] text-text-secondary">
                <span>Opacity</span>
                <span className="font-mono tabular-nums">{Math.round(alpha * 100)}%</span>
              </div>
              <div className="relative h-6 rounded-lg overflow-hidden border border-[#2E2E2E] flex items-center bg-[#232323]">
                <input
                  type="range"
                  min={0}
                  max={100}
                  value={Math.round(alpha * 100)}
                  onChange={(e) => handleAlphaChange(Number(e.target.value) / 100)}
                  className="w-full h-full opacity-0 cursor-pointer z-10"
                />
                <div
                  className="absolute inset-0 pointer-events-none"
                  style={{
                    background: `linear-gradient(to right, transparent, ${rgbToHex(currentRgb.r, currentRgb.g, currentRgb.b)})`,
                  }}
                />
                <div
                  className="absolute top-0 bottom-0 w-3 rounded bg-white shadow-md border border-black/40 pointer-events-none -translate-x-1/2"
                  style={{ left: `${alpha * 100}%` }}
                />
              </div>
            </div>
          )}
        </div>

        {/* Color Preview & Inputs Row */}
        <div className="flex items-center gap-3 bg-[#1C1C1C] border border-[#2E2E2E] rounded-xl p-2.5">
          {/* Visual Swatch (Old vs New) */}
          <div className="flex flex-col items-center gap-1 shrink-0">
            <div className="w-10 h-10 rounded-lg border border-[#2E2E2E] overflow-hidden flex flex-col shadow-inner">
              <div
                className="flex-1"
                style={{ backgroundColor: currentHex }}
                title={`New: ${currentHex}`}
              />
            </div>
            <span className="text-[9px] font-mono text-text-muted">NEW</span>
          </div>

          {/* Hex & RGB Controls */}
          <div className="flex-1 min-w-0 space-y-1.5">
            {/* Hex Input */}
            <div className="flex items-center gap-1.5">
              <span className="text-[11px] font-mono font-bold text-text-secondary">HEX</span>
              <input
                type="text"
                value={hexInput}
                onChange={handleHexInputChange}
                className={`flex-1 min-w-0 px-2 py-1 rounded bg-[#232323] border text-[12px] font-mono font-semibold text-text-main outline-none transition-colors ${
                  isHexValid ? 'border-[#2E2E2E] focus:border-primary' : 'border-red-500'
                }`}
                placeholder="#FFFFFF"
                maxLength={enableAlpha ? 9 : 7}
              />
            </div>

            {/* RGB Micro Inputs */}
            <div className="grid grid-cols-3 gap-1">
              {(['r', 'g', 'b'] as const).map((ch) => (
                <div key={ch} className="flex items-center gap-1 bg-[#232323] px-1.5 py-0.5 rounded border border-[#2E2E2E]">
                  <span className="text-[9px] font-mono uppercase text-text-muted">{ch}</span>
                  <input
                    type="number"
                    min={0}
                    max={255}
                    value={currentRgb[ch]}
                    onChange={(e) => handleRgbChange(ch, parseInt(e.target.value, 10))}
                    className="w-full bg-transparent text-[10px] font-mono text-right text-text-main outline-none [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                  />
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Recent Colors Shelf */}
        <div className="space-y-1.5">
          <span className="text-[11px] font-medium text-text-secondary">Recent Colors</span>
          <div className="flex items-center gap-1.5 overflow-x-auto no-scrollbar py-0.5">
            {recentColors.map((c, i) => (
              <button
                key={`${c}-${i}`}
                type="button"
                onClick={() => {
                  const { r, g, b, a } = parseHex(c);
                  const newHsv = rgbToHsv(r, g, b);
                  setHsv(newHsv);
                  setAlpha(a);
                  setHexInput(c);
                  setIsHexValid(true);
                  onChange(c);
                }}
                className={`w-7 h-7 rounded-lg shrink-0 border-2 transition-transform active:scale-90 ${
                  currentHex.toUpperCase() === c.toUpperCase() ? 'border-primary scale-110 shadow-hero-sm' : 'border-[#2E2E2E]'
                }`}
                style={{ backgroundColor: c }}
                title={c}
              />
            ))}
          </div>
        </div>

        {/* Done Button (Law of Single Hero CTA) */}
        <div className="pt-1">
          <button
            type="button"
            onClick={handleDone}
            className="w-full h-10 rounded-xl bg-primary hover:bg-primary-hover text-white text-[12px] font-semibold flex items-center justify-center gap-1.5 active:scale-[0.98] transition-all shadow-hero-glow"
          >
            <Check size={14} />
            <span>Apply Custom Color</span>
          </button>
        </div>
      </div>
    </div>
  );
};
