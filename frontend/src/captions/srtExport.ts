import { CaptionTrack } from './types';

/**
 * Format seconds into SubRip (SRT) timestamp: HH:MM:SS,mmm
 */
function formatSrtTimestamp(seconds: number): string {
  const safe = Math.max(0, Number.isFinite(seconds) ? seconds : 0);
  const hrs = Math.floor(safe / 3600);
  const mins = Math.floor((safe % 3600) / 60);
  const secs = Math.floor(safe % 60);
  const millis = Math.floor((safe % 1) * 1000);

  const hh = hrs.toString().padStart(2, '0');
  const mm = mins.toString().padStart(2, '0');
  const ss = secs.toString().padStart(2, '0');
  const mmm = millis.toString().padStart(3, '0');

  return `${hh}:${mm}:${ss},${mmm}`;
}

/**
 * Generate standard SubRip (.srt) subtitle content from authentic caption track.
 */
export function generateSrtFromTrack(track: CaptionTrack | null): string {
  if (!track || !track.groups || track.groups.length === 0) {
    return '';
  }

  // Filter only spoken caption or text groups, sort by start time
  const validGroups = track.groups
    .filter((g) => (!g.type || g.type === 'caption' || g.type === 'text') && g.words && g.words.length > 0)
    .sort((a, b) => a.start - b.start);

  if (validGroups.length === 0) {
    return '';
  }

  const srtBlocks: string[] = [];

  validGroups.forEach((grp, idx) => {
    const startStr = formatSrtTimestamp(grp.start);
    const endStr = formatSrtTimestamp(grp.end);
    const text = (grp.words || []).map((w) => w.text).join(' ');

    if (!text.trim()) return;

    srtBlocks.push(`${idx + 1}\n${startStr} --> ${endStr}\n${text.trim()}`);
  });

  return srtBlocks.join('\n\n') + '\n';
}

/**
 * Trigger client-side file download of .srt subtitles
 */
export function downloadSrtFile(track: CaptionTrack | null, clipTitle: string = 'captions') {
  const content = generateSrtFromTrack(track);
  if (!content) {
    alert('No caption text available to export.');
    return;
  }

  const cleanFilename = (clipTitle || 'dispatch_captions')
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '_')
    .slice(0, 32);

  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `${cleanFilename}.srt`;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}
