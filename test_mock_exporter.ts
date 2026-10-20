import { CaptionTrack } from './frontend/src/captions/types';

export function export_to_ass_dialogue_mock(track: CaptionTrack): { text: string; start: number; end: number }[] {
  const events: { text: string; start: number; end: number }[] = [];
  for (const group of track.groups || []) {
    // Non-text layers and hidden layers are never rendered or exported as subtitles
    if (group.type === 'audio' || group.type === 'voiceover' || group.type === 'overlay' || group.is_hidden) {
      continue;
    }
    const text = (group.words || []).map((w) => w.text).join(' ');
    if (text) {
      events.push({ text, start: group.start, end: group.end });
    }
  }
  return events;
}

export function export_to_srt_mock(track: CaptionTrack): { text: string; start: number; end: number }[] {
  const subtitles: { text: string; start: number; end: number }[] = [];
  for (const group of track.groups || []) {
    if (group.type === 'audio' || group.type === 'voiceover' || group.type === 'overlay' || group.is_hidden) {
      continue;
    }
    const text = (group.words || []).map((w) => w.text).join(' ');
    if (text) {
      subtitles.push({ text, start: group.start, end: group.end });
    }
  }
  return subtitles;
}
