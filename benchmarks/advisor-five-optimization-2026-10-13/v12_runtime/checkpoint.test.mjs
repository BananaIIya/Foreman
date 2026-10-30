import test from 'node:test';
import assert from 'node:assert/strict';
import { parseCheckpoint } from './checkpoint.mjs';

const checkpoint = {status: 'working', summary: 'A check remains', relevant_paths: ['/app/draft.py']};
test('pure JSON checkpoint continues', () => {
  assert.deepEqual(parseCheckpoint(JSON.stringify(checkpoint)), checkpoint);
});
test('prose and fenced JSON retain working status', () => {
  assert.deepEqual(parseCheckpoint('Some work is done.\n```json\n' + JSON.stringify(checkpoint) + '\n```'), checkpoint);
});
test('nested objects and braces in escaped strings remain data', () => {
  const value = {...checkpoint, summary: 'braces { } and "quoted" text \\', checks: [{result: {ok: false}}]};
  assert.deepEqual(parseCheckpoint('Report:\n' + JSON.stringify(value) + '\nEnd.'), value);
});
test('completed and blocked checkpoints do not become working', () => {
  for (const status of ['completed', 'blocked']) {
    assert.equal(parseCheckpoint('```json\n' + JSON.stringify({...checkpoint, status}) + '\n```').status, status);
  }
});
test('truncated JSON does not fabricate a checkpoint', () => {
  assert.equal(parseCheckpoint('```json\n' + JSON.stringify(checkpoint).slice(0, -1)), undefined);
});
test('multiple checkpoint objects are rejected as ambiguous', () => {
  assert.equal(parseCheckpoint('First: ' + JSON.stringify(checkpoint) + '\nSecond: ' + JSON.stringify({...checkpoint, status: 'completed'})), undefined);
});
test('missing metadata or unknown status is rejected', () => {
  for (const value of [{status: 'working'}, {...checkpoint, relevant_paths: [7]}, {...checkpoint, status: 'guess'}]) {
    assert.equal(parseCheckpoint(JSON.stringify(value)), undefined);
  }
});
test('unrelated JSON does not control continuation', () => {
  assert.equal(parseCheckpoint('Observed {"exit_code":0}'), undefined);
});
test('non-string and non-object outputs are rejected', () => {
  for (const value of [null, {}, 'null', '[]', '42']) assert.equal(parseCheckpoint(value), undefined);
});
