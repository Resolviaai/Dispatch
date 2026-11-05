/** Shared-fixture contract tests for TypeScript EDL v1 timeline math. */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  concatToSource,
  deletePiece,
  intersectSourceInterval,
  keptDuration,
  normalizeEdlDocument,
  normalizeRanges,
  remapCaptionTrackToTimeline,
  sourceToConcat,
  splitAt,
  TimelineError,
  timelineIntervalToSource,
  trimEdge,
} from './src/captions/timelineEngine';

const here = dirname(fileURLToPath(import.meta.url));
const fixtures = JSON.parse(
  readFileSync(resolve(here, '..', 'tests', 'fixtures', 'segments_v1.json'), 'utf8'),
);

function assertApprox(actual: unknown, expected: unknown, path = 'root'): void {
  if (typeof actual === 'number' && typeof expected === 'number') {
    assert.ok(Math.abs(actual - expected) <= 1e-8, `${path}: expected ${expected}, received ${actual}`);
    return;
  }
  if (Array.isArray(expected)) {
    assert.ok(Array.isArray(actual), `${path}: expected array`);
    assert.equal(actual.length, expected.length, `${path}: array length`);
    expected.forEach((value, index) => assertApprox(actual[index], value, `${path}[${index}]`));
    return;
  }
  if (expected && typeof expected === 'object') {
    assert.ok(actual && typeof actual === 'object', `${path}: expected object`);
    for (const [key, value] of Object.entries(expected as Record<string, unknown>)) {
      assertApprox((actual as Record<string, unknown>)[key], value, `${path}.${key}`);
    }
    return;
  }
  assert.equal(actual, expected, path);
}

function expectTimelineError(run: () => unknown, label: string): void {
  assert.throws(run, TimelineError, label);
}

console.log('EDL v1 timeline engine shared fixtures...');


{
  const sourceTrack = {
    id: 'captions',
    groups: [{
      id: 'caption-1',
      start: 0.2,
      end: 3.0,
      style: { primary_color: '#ff0000' },
      words: [
        { id: 'before', text: 'before', start: 0.2, end: 0.8, style_override: { color: '#111111' } },
        { id: 'crossing', text: 'crossing', start: 1.5, end: 2.5, style_override: { color: '#222222' } },
        { id: 'after', text: 'after', start: 2.5, end: 3.0 },
      ],
    }],
  };
  const singleRemainingRange = remapCaptionTrackToTimeline(
    sourceTrack,
    [[12.0, 18.0]],
    10.0,
    30.0,
  );
  assert.equal(singleRemainingRange.groups.length, 1, 'single-range EDL still remaps captions');
  assert.deepEqual(
    singleRemainingRange.groups[0].words.map((word: any) => [word.id, word.start, word.end]),
    [['crossing', 0, 0.5], ['after', 0.5, 1]],
    'deleted source words are omitted and boundary-crossing words are clipped once',
  );
  assert.equal(singleRemainingRange.groups[0].style.primary_color, '#ff0000');
  assert.equal(singleRemainingRange.groups[0].words[0].style_override.color, '#222222');

  const editedAgain = remapCaptionTrackToTimeline(
    sourceTrack,
    [[12.0, 12.8], [13.2, 14.0]],
    10.0,
    30.0,
  );
  assert.deepEqual(
    editedAgain.groups[0].words.map((word: any) => [word.id, word.start, word.end]),
    [['crossing', 0, 0.5], ['after', 0.5, 0.8], ['after', 0.8, 1.3]].filter((row: any[], i: number, rows: any[]) =>
      rows.findIndex((candidate: any[]) => candidate[0] === row[0] && candidate[1] === row[1]) === i
    ),
    'source-origin remapping remains deterministic across EDL revisions',
  );
}



const sourceDuration = fixtures.source_duration;
const cases = fixtures.cases;

for (const name of ['split', 'split_multi_range']) {
  const item = cases[name];
  const result = splitAt(item.ranges, item.source_time, sourceDuration);
  assertApprox(result, item.expected, name);
  assert.ok(Math.abs(keptDuration(result) - keptDuration(item.ranges)) <= 1e-8, `${name}: split preserves duration`);
}

for (const name of [
  'trim_start',
  'trim_expand_into_gap',
  'trim_end',
  'trim_end_expand_into_gap',
]) {
  const item = cases[name];
  assertApprox(
    trimEdge(item.ranges, item.index, item.edge, item.new_source_time, sourceDuration),
    item.expected,
    name,
  );
}

{
  const item = cases.delete_middle;
  const result = deletePiece(item.ranges, item.index, sourceDuration);
  assertApprox(result, item.expected, 'delete_middle');
  assert.equal(result.length, 2, 'delete preserves separate surviving blocks');
}

{
  const item = cases.adjacency_preserved;
  const result = normalizeRanges(item.ranges, sourceDuration);
  assert.equal(result.length, item.expected_count, 'adjacent source pieces are never merged');
  assertApprox(keptDuration(result), item.expected_duration, 'adjacent duration');
}

{
  const item = cases.point_mapping;
  for (const point of item.source_to_concat) {
    assertApprox(
      sourceToConcat(item.ranges, point.source, sourceDuration),
      point.expected,
      `source_to_concat(${point.source})`,
    );
  }
  for (const point of item.concat_to_source) {
    assertApprox(
      concatToSource(item.ranges, point.timeline, sourceDuration),
      point.expected,
      `concat_to_source(${point.timeline})`,
    );
  }
}

{
  const item = cases.source_interval_fragments;
  assertApprox(
    intersectSourceInterval(item.ranges, item.source_start, item.source_end, sourceDuration),
    item.expected,
    'source interval fragmentation',
  );
}

{
  const item = cases.timeline_interval_fragments;
  assertApprox(
    timelineIntervalToSource(item.ranges, item.timeline_start, item.timeline_end, sourceDuration),
    item.expected,
    'timeline interval fragmentation',
  );
}

{
  const item = cases.edl_document;
  const result = normalizeEdlDocument(item.document);
  assert.equal(result.ranges.length, item.expected_range_count);
  assertApprox(keptDuration(result.ranges), item.expected_duration, 'EDL kept duration');
  assertApprox(result.settings, item.document.settings, 'EDL settings preserved');
  assertApprox(result.provenance, item.document.provenance, 'EDL provenance preserved');
}

for (const item of fixtures.invalid_ranges) {
  expectTimelineError(
    () => normalizeRanges(item.ranges, item.source_duration),
    `invalid shared fixture: ${item.name}`,
  );
}

for (const point of [9.0, 10.0, 10.05, 19.95, 20.0, 22.0]) {
  expectTimelineError(
    () => splitAt([[10.0, 20.0]], point, 100.0),
    `split must reject invalid point ${point}`,
  );
}

expectTimelineError(
  () => trimEdge([[10.0, 20.0], [25.0, 35.0]], 0, 'end', 26.0, 100.0),
  'trim may not overlap next segment',
);
expectTimelineError(
  () => trimEdge([[10.0, 20.0], [25.0, 35.0]], 1, 'start', 19.0, 100.0),
  'trim may not overlap previous segment',
);
expectTimelineError(
  () => trimEdge([[10.0, 20.0], [25.0, 35.0]], 0, 'start', 19.95, 100.0),
  'trim must preserve 0.1s minimum piece',
);
expectTimelineError(
  () => deletePiece([[10.0, 20.0]], 0, 100.0),
  'cannot delete the final kept segment',
);
expectTimelineError(
  () => normalizeEdlDocument({
    v: 2, source_asset_id: 'asset', source_duration: 10, ranges: [[0, 10]],
  }),
  'unsupported EDL version',
);

assertApprox(normalizeRanges([[-0.005, 5.005]], 5.0), [[0.0, 5.0]], 'source bounds epsilon clamp');

console.log('  passed: split/trim/delete, no-merge invariant, dual-clock mapping, interval fragmentation, EDL validation, and invalid inputs');
