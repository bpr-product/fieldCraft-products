// Module-level snapshot store for "Revoke Suggestion" functionality.
//
// When the Recommendation Panel fires Suggest Materials, Requirements_v2 snapshots
// the user's current field values BEFORE overwriting them, keyed by bid_id.
// The Recommendation Panel then exposes a Revoke button that replays the snapshot
// back through the same applyBidAnswers event, restoring the user's original
// answers and clearing the entry.

type Snapshot = Record<string, string>;

const snapshots = new Map<string, Snapshot>();

export function captureSnapshot(bidId: string, snapshot: Snapshot): void {
  if (!bidId) return;
  snapshots.set(bidId, { ...snapshot });
}

export function getSnapshot(bidId: string): Snapshot | undefined {
  if (!bidId) return undefined;
  return snapshots.get(bidId);
}

export function clearSnapshot(bidId: string): void {
  snapshots.delete(bidId);
}

export function hasSnapshot(bidId: string): boolean {
  return snapshots.has(bidId);
}

/** Clear every snapshot — call on wizard unmount so Suggest/Revoke state
 *  does not carry over into the next Create Bid session opened in the same tab. */
export function clearAllSnapshots(): void {
  snapshots.clear();
}
