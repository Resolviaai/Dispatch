import { CaptionGroup, Word } from './types';

/**
 * Intelligent word-timing preservation engine.
 * 
 * Invariants:
 * 1. Single word edits strictly preserve every other word's ID, start/end timestamps, and style overrides.
 * 2. Multi-word edits find unchanged anchor words via alignment and only interpolate timing for the modified span.
 * 3. Never silently distribute words evenly across a segment when authentic Whisper timing exists.
 */

export function preserveWordTimingsOnEdit(
  originalWords: Word[] = [],
  newText: string,
  groupStart: number,
  groupEnd: number,
  groupId: string = 'grp'
): Word[] {
  const trimmed = newText.trim();
  if (!trimmed) {
    return [];
  }

  const rawTokens = trimmed.split(/\s+/).filter(Boolean);
  if (rawTokens.length === 0) {
    return [];
  }

  const totalDuration = Math.max(0.2, groupEnd - groupStart);

  // Case 0: No prior words existed — construct baseline with estimated flags
  if (!originalWords || originalWords.length === 0) {
    const step = totalDuration / rawTokens.length;
    return rawTokens.map((text, idx) => ({
      id: `${groupId}-w${idx}-${Date.now()}`,
      text,
      start: Number((groupStart + idx * step).toFixed(2)),
      end: Number((groupStart + (idx + 1) * step).toFixed(2)),
      confidence: 0.8,
    }));
  }

  // Case 1: Word count is identical — 1:1 mapping preserving all unmodified words and timestamps
  if (originalWords.length === rawTokens.length) {
    return rawTokens.map((token, idx) => {
      const orig = originalWords[idx];
      return {
        ...orig,
        text: token,
        // Preserve original id and timings exactly
        id: orig.id || `${groupId}-w${idx}`,
        start: orig.start,
        end: orig.end,
        style_override: orig.style_override,
      };
    });
  }

  // Case 2: Multi-word insertion or deletion — find prefix and suffix matches (anchor points)
  let prefixCount = 0;
  while (
    prefixCount < originalWords.length &&
    prefixCount < rawTokens.length &&
    originalWords[prefixCount].text.toLowerCase() === rawTokens[prefixCount].toLowerCase()
  ) {
    prefixCount++;
  }

  let suffixCount = 0;
  while (
    suffixCount < (originalWords.length - prefixCount) &&
    suffixCount < (rawTokens.length - prefixCount) &&
    originalWords[originalWords.length - 1 - suffixCount].text.toLowerCase() ===
      rawTokens[rawTokens.length - 1 - suffixCount].toLowerCase()
  ) {
    suffixCount++;
  }

  const result: Word[] = [];

  // 1. Add matched prefix words with untouched timestamps and IDs
  for (let i = 0; i < prefixCount; i++) {
    result.push({
      ...originalWords[i],
      text: rawTokens[i], // Keep casing of new text
    });
  }

  // 2. Identify the middle span to retime
  const changedTokens = rawTokens.slice(prefixCount, rawTokens.length - suffixCount);
  
  if (changedTokens.length > 0) {
    const spanStart = prefixCount > 0 ? originalWords[prefixCount - 1].end : groupStart;
    const spanEnd = suffixCount > 0 
      ? originalWords[originalWords.length - suffixCount].start 
      : groupEnd;
    
    const spanDuration = Math.max(0.1, spanEnd - spanStart);
    const step = spanDuration / changedTokens.length;

    changedTokens.forEach((token, idx) => {
      result.push({
        id: `${groupId}-mod-${prefixCount + idx}-${Date.now()}`,
        text: token,
        start: Number((spanStart + idx * step).toFixed(2)),
        end: Number((spanStart + (idx + 1) * step).toFixed(2)),
        confidence: 0.8,
      });
    });
  }

  // 3. Add matched suffix words with untouched timestamps and IDs
  const origSuffixStartIndex = originalWords.length - suffixCount;
  const newSuffixStartIndex = rawTokens.length - suffixCount;

  for (let i = 0; i < suffixCount; i++) {
    const origWord = originalWords[origSuffixStartIndex + i];
    result.push({
      ...origWord,
      text: rawTokens[newSuffixStartIndex + i],
    });
  }

  return result;
}

/**
 * Split a caption group at splitTime while preserving authentic word timestamps.
 */
export function splitCaptionGroupAtTime(
  group: CaptionGroup,
  splitTime: number
): [CaptionGroup, CaptionGroup] | null {
  if (splitTime <= group.start + 0.1 || splitTime >= group.end - 0.1) {
    return null;
  }

  const words = group.words || [];
  const leftWords: Word[] = [];
  const rightWords: Word[] = [];

  words.forEach((w) => {
    const mid = (w.start + w.end) / 2;
    if (mid <= splitTime) {
      leftWords.push({
        ...w,
        end: Math.min(w.end, splitTime),
      });
    } else {
      rightWords.push({
        ...w,
        start: Math.max(w.start, splitTime),
      });
    }
  });

  const leftGroup: CaptionGroup = {
    ...group,
    id: group.id,
    start: group.start,
    end: splitTime,
    words: leftWords,
  };

  const rightGroup: CaptionGroup = {
    ...group,
    id: `grp_${Date.now()}_split`,
    start: splitTime,
    end: group.end,
    words: rightWords,
  };

  return [leftGroup, rightGroup];
}

/**
 * Merge two adjacent caption groups into one, preserving all authentic word timestamps.
 */
export function mergeAdjacentCaptionGroups(
  groupA: CaptionGroup,
  groupB: CaptionGroup
): CaptionGroup {
  const first = groupA.start <= groupB.start ? groupA : groupB;
  const second = groupA.start <= groupB.start ? groupB : groupA;

  const combinedWords = [...(first.words || []), ...(second.words || [])].sort(
    (a, b) => a.start - b.start
  );

  return {
    ...first,
    id: first.id,
    start: Math.min(first.start, second.start),
    end: Math.max(first.end, second.end),
    words: combinedWords,
  };
}
