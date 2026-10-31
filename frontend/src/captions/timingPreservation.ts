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

let _splitIdCounter = 0;

/**
 * Generate a monotonic, collision-resistant group ID for split operations.
 */
export function generateCollisionResistantGroupId(prefix: string = 'grp'): string {
  _splitIdCounter = (_splitIdCounter + 1) % 100000;
  const timePart = Date.now().toString(36);
  const randPart = Math.random().toString(36).slice(2, 7);
  const seqPart = _splitIdCounter.toString(36);
  return `${prefix}_${timePart}_${randPart}_${seqPart}`;
}

/**
 * Split a caption or layer group at splitTime preserving authentic word timestamps.
 *
 * Invariants:
 * 1. Reject NaN, non-finite, out-of-range, and edge splits (< 0.05s from boundary).
 * 2. For captions with words: snaps to the nearest safe boundary between words.
 *    Words are NEVER chopped in half or have their timestamps altered.
 * 3. Never produces an empty word list on either segment.
 * 4. Deep-clones styling, animation, layout, and role metadata onto both segments.
 * 5. Generates a collision-resistant unique ID for the new segment.
 * 6. Performs post-split validation before returning: invalid results return null.
 */
export function splitCaptionGroupAtTime(
  group: CaptionGroup,
  splitTime: number
): [CaptionGroup, CaptionGroup] | null {
  if (!group || typeof splitTime !== 'number' || !Number.isFinite(splitTime)) {
    return null;
  }

  const duration = group.end - group.start;
  if (!Number.isFinite(duration) || duration < 0.2) {
    return null;
  }

  // Reject edge splits too close to group boundaries
  if (splitTime <= group.start + 0.05 || splitTime >= group.end - 0.05) {
    return null;
  }

  const words = Array.isArray(group.words) ? group.words : [];
  const isCaptionWithWords = (group.type === 'caption' || !group.type) && words.length > 0;

  let leftWords: Word[] = [];
  let rightWords: Word[] = [];
  let effectiveSplitTime = splitTime;

  if (isCaptionWithWords) {
    // A spoken caption must have at least 2 words to be split into two valid caption segments
    if (words.length < 2) {
      return null;
    }

    // Find the closest safe boundary between adjacent words
    let bestBoundaryIndex = 0;
    let minDistance = Infinity;

    for (let i = 0; i < words.length - 1; i++) {
      const prevWord = words[i];
      const nextWord = words[i + 1];
      const boundaryTime = (prevWord.end + nextWord.start) / 2;
      const dist = Math.abs(splitTime - boundaryTime);
      if (dist < minDistance) {
        minDistance = dist;
        bestBoundaryIndex = i;
      }
    }

    leftWords = words.slice(0, bestBoundaryIndex + 1).map((w) => ({ ...w }));
    rightWords = words.slice(bestBoundaryIndex + 1).map((w) => ({ ...w }));

    if (leftWords.length === 0 || rightWords.length === 0) {
      return null;
    }

    const lastLeft = leftWords[leftWords.length - 1];
    const firstRight = rightWords[0];

    // Split boundary MUST fall cleanly between the two word sets
    let boundaryMid: number;
    if (lastLeft.end <= firstRight.start) {
      boundaryMid = (lastLeft.end + firstRight.start) / 2;
    } else {
      boundaryMid = lastLeft.end;
    }
    boundaryMid = Number(boundaryMid.toFixed(3));

    // Strictly reject edge splits too close to group boundaries without moving the boundary!
    if (boundaryMid <= group.start + 0.05 || boundaryMid >= group.end - 0.05) {
      return null;
    }

    effectiveSplitTime = boundaryMid;

    // Strict validation: verify that all left words are inside [group.start, boundaryMid]
    // and all right words are inside [boundaryMid, group.end]
    const leftInside = leftWords.every((w) => w.start >= group.start - 0.02 && w.end <= boundaryMid + 0.02);
    const rightInside = rightWords.every((w) => w.start >= boundaryMid - 0.02 && w.end <= group.end + 0.02);
    if (!leftInside || !rightInside) {
      return null;
    }
  }

  const leftEnd = isCaptionWithWords ? effectiveSplitTime : Number(Math.max(group.start + 0.05, splitTime).toFixed(3));
  const rightStart = isCaptionWithWords ? effectiveSplitTime : Number(Math.min(group.end - 0.05, splitTime).toFixed(3));

  if (leftEnd <= group.start || rightStart >= group.end) {
    return null;
  }

  const leftText = isCaptionWithWords
    ? leftWords.map((w) => w.text).join(' ')
    : (group.text || '');

  const rightText = isCaptionWithWords
    ? rightWords.map((w) => w.text).join(' ')
    : (group.text || '');

  const leftGroup: CaptionGroup = {
    ...JSON.parse(JSON.stringify(group)),
    id: group.id,
    start: group.start,
    end: leftEnd,
    text: leftText,
    words: leftWords,
  };

  const rightGroup: CaptionGroup = {
    ...JSON.parse(JSON.stringify(group)),
    id: generateCollisionResistantGroupId('grp'),
    start: rightStart,
    end: group.end,
    text: rightText,
    words: rightWords,
  };

  // Final sanity validation: neither segment may have degenerate bounds
  if (leftGroup.end <= leftGroup.start || rightGroup.end <= rightGroup.start) {
    return null;
  }
  if (isCaptionWithWords && (!leftGroup.words?.length || !rightGroup.words?.length)) {
    return null;
  }

  return [leftGroup, rightGroup];
}

/**
 * Merge two adjacent caption groups into one, preserving all authentic word timestamps,
 * combined text, and styling.
 */
export function mergeAdjacentCaptionGroups(
  groupA: CaptionGroup,
  groupB: CaptionGroup
): CaptionGroup | null {
  if (!groupA || !groupB) return null;

  const first = groupA.start <= groupB.start ? groupA : groupB;
  const second = groupA.start <= groupB.start ? groupB : groupA;

  // Type compatibility check
  const typeA = first.type || 'caption';
  const typeB = second.type || 'caption';
  if (typeA !== typeB) {
    return null;
  }

  // Adjacency check: gap cannot exceed 1.5s of silence, and second cannot be behind first
  const gap = second.start - first.end;
  if (gap > 1.5 || gap < -0.1) {
    return null;
  }

  const combinedWords = [
    ...(first.words || []).map((w) => ({ ...w })),
    ...(second.words || []).map((w) => {
      const copy = { ...w };
      if (second.style) {
        const overrides = { ...(copy.style_override || {}) };
        if (second.style.primary_color && second.style.primary_color !== first.style?.primary_color) {
          overrides.color = overrides.color || second.style.primary_color;
        }
        if (second.style.highlight_color && second.style.highlight_color !== first.style?.highlight_color) {
          overrides.highlight_color = overrides.highlight_color || second.style.highlight_color;
        }
        if (second.style.font_weight && second.style.font_weight !== first.style?.font_weight) {
          overrides.font_weight = overrides.font_weight || second.style.font_weight;
        }
        if (Object.keys(overrides).length > 0) {
          copy.style_override = overrides;
        }
      }
      return copy;
    }),
  ].sort((a, b) => a.start - b.start);

  const combinedText = combinedWords.length > 0
    ? combinedWords.map((w) => w.text).join(' ')
    : `${first.text || ''} ${second.text || ''}`.trim();

  return {
    ...JSON.parse(JSON.stringify(first)),
    id: first.id,
    start: Math.min(first.start, second.start),
    end: Math.max(first.end, second.end),
    text: combinedText,
    words: combinedWords,
  };
}
