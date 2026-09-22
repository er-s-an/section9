import test from 'node:test';
import assert from 'node:assert/strict';
import { currentOfficeEvents } from '../frontend/src/office-events.ts';

test('late completion with a new envelope generation cannot leak the previous run into the office', () => {
  const events = [
    { generation: '2', run_id: 'old', sequence: 9 },
    { generation: '1', run_id: 'new', sequence: 8 },
    { generation: '2', run_id: 'new', sequence: 7 },
  ];
  assert.deepEqual(currentOfficeEvents(events, '2', 'new').map(event => event.sequence), [7]);
});

test('reset with no active run retains only current unscoped events', () => {
  const events = [
    { generation: '3', run_id: 'old', sequence: 12 },
    { generation: '3', run_id: null, sequence: 11 },
    { generation: '2', run_id: null, sequence: 10 },
  ];
  assert.deepEqual(currentOfficeEvents(events, '3', null).map(event => event.sequence), [11]);
});

test('numeric event order is preserved across string sequences without mutating the input', () => {
  const events = [
    { generation: '3', run_id: 'run', sequence: '9' },
    { generation: '3', run_id: 'run', sequence: '10' },
    { generation: '3', run_id: null, sequence: '8' },
  ];
  assert.deepEqual(currentOfficeEvents(events, '3', 'run').map(event => event.sequence), ['10', '9', '8']);
  assert.equal(events[0].sequence, '9');
});
