/**
 * linePartition.ts
 * Deterministic text and caption line layout engine.
 * Calculates visual line boundaries based on font family, font size, font weight,
 * letter spacing, and available container width using an offscreen canvas context.
 */

export interface WordLike {
  id?: string;
  text?: string;
  word?: string;
  start?: number;
  end?: number;
  style_override?: any;
  [key: string]: any;
}

let cachedCanvas: HTMLCanvasElement | null = null;
let cachedCtx: CanvasRenderingContext2D | null = null;

function getSharedMeasureContext(): CanvasRenderingContext2D | null {
  if (typeof document === 'undefined') return null;
  if (!cachedCanvas) {
    try {
      cachedCanvas = document.createElement('canvas');
      cachedCtx = cachedCanvas.getContext('2d');
    } catch {
      cachedCtx = null;
    }
  }
  return cachedCtx;
}

/**
 * Deterministically partitions an array of words into visual lines matching
 * actual rendered layout constraints.
 */
export function partitionWordsIntoVisualLines<T extends WordLike>(
  words: T[],
  fontFamily: string,
  fontSize: number,
  fontWeight: string | number,
  letterSpacing: number,
  maxWidthPx: number,
  textTransform?: string
): T[][] {
  if (!words || words.length === 0) return [];

  const ctx = getSharedMeasureContext();
  if (ctx) {
    ctx.font = `${fontWeight || 'bold'} ${Math.round(fontSize)}px "${fontFamily || 'Montserrat'}", sans-serif`;
  }

  const spaceWidth = ctx ? ctx.measureText(' ').width : fontSize * 0.3;
  const lines: T[][] = [];
  let currentLine: T[] = [];
  let currentLineWidth = 0;

  for (const word of words) {
    let rawText = (word.text || word.word || '').trim();
    if (textTransform === 'uppercase') rawText = rawText.toUpperCase();
    else if (textTransform === 'lowercase') rawText = rawText.toLowerCase();

    // Support explicit newlines inside caption text
    if (rawText.includes('\n')) {
      const parts = rawText.split('\n');
      for (let i = 0; i < parts.length; i++) {
        const partText = parts[i].trim();
        if (i > 0 && currentLine.length > 0) {
          lines.push(currentLine);
          currentLine = [];
          currentLineWidth = 0;
        }
        if (partText) {
          const wWidth = ctx
            ? ctx.measureText(partText).width + (letterSpacing || 0) * partText.length
            : partText.length * fontSize * 0.55;
          currentLine.push({ ...word, text: partText });
          currentLineWidth += wWidth + spaceWidth;
        }
      }
      continue;
    }

    const wordWidth = ctx
      ? ctx.measureText(rawText).width + (letterSpacing || 0) * rawText.length
      : rawText.length * fontSize * 0.55;

    // Wrap to a new line when the word exceeds maximum content width
    if (currentLine.length > 0 && currentLineWidth + wordWidth > maxWidthPx) {
      lines.push(currentLine);
      currentLine = [word];
      currentLineWidth = wordWidth + spaceWidth;
    } else {
      currentLine.push(word);
      currentLineWidth += wordWidth + spaceWidth;
    }
  }

  if (currentLine.length > 0) {
    lines.push(currentLine);
  }

  return lines;
}
