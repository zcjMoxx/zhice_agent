# Core Unit Test Cases

## Test Target

Verify shared core helpers that are not tied to a concrete LLM, tool provider, Web route, or session-store implementation.

## Case Coverage

### Case 1: Explicit turn grouping

- Input: adjacent messages with the same `turn_id`.
- Expected: one `TurnGroup` with the explicit id and index.
- Checkpoints: message order is preserved.

### Case 2: Untagged messages

- Input: messages without `turn_id` mixed with messages that have `turn_id`.
- Expected: untagged messages are ignored by turn grouping.
- Checkpoints: no synthetic turn ids are generated.

### Case 3: Multiple explicit turns

- Input: adjacent explicit turns in persisted file order.
- Expected: one group per explicit turn segment.
- Checkpoints: grouping does not reorder messages.

### Case 4: Next turn index

- Input: sessions with explicit indices or no explicit indices.
- Expected: explicit max index plus one wins; otherwise index starts at `1`.
- Checkpoints: untagged messages do not create derived indices.
