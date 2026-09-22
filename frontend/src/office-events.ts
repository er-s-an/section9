type ScopedEvent = { generation: string; run_id: string | null; sequence: string | number }

// Late completion events may have the new envelope generation but an old run ID.
// Both boundaries must match before an event can be shown as current office activity.
export function currentOfficeEvents<T extends ScopedEvent>(events: T[], generation: string, runId: string | null): T[] {
  return events.filter(event => event.generation === generation &&
    (event.run_id === runId || event.run_id === null))
    .sort((a, b) => Number(b.sequence) - Number(a.sequence))
}
