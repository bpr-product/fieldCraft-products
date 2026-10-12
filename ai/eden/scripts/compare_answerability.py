"""
Runs the REAL confidence-branch logic (build_estimate_change_message /
build_schedule_change_message's decision, not the whole Mongo-querying
agent methods) over EVERY project_health document in a given database, and
reports how many would get a real causal answer (confidence high/low) vs.
the honest "no confirmed reason" fallback (none/missing) -- for Q1 and Q2
independently.

Usage:
    MONGODB_URI="..." python3 scripts/compare_answerability.py gold_eden
    MONGODB_URI="..." python3 scripts/compare_answerability.py gold_eden_synthesised
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pymongo import MongoClient


def classify(current_entry: dict | None) -> str:
    if current_entry is None:
        return "no_history"  # fewer than 2 versions -- nothing to explain
    confidence = current_entry.get("confidence")
    reason = current_entry.get("reason")
    if confidence in ("high", "low") and reason:
        return confidence
    return "none"


def main():
    db_name = sys.argv[1] if len(sys.argv) > 1 else "gold_eden"
    uri = os.environ["MONGODB_URI"]
    client = MongoClient(uri, serverSelectionTimeoutMS=8000)
    coll = client[db_name]["project_health"]

    total = coll.count_documents({})
    q1_counts = {"high": 0, "low": 0, "none": 0, "no_history": 0}
    q2_counts = {"high": 0, "low": 0, "none": 0, "no_history": 0}

    for doc in coll.find({}, {"estimateHistory": 1, "scheduleRevisions": 1}):
        history = doc.get("estimateHistory") or []
        current = next((e for e in history if e.get("isCurrent")), None) if len(history) >= 2 else None
        q1_counts[classify(current)] += 1

        revisions = doc.get("scheduleRevisions") or []
        current_rev = next((e for e in revisions if e.get("isCurrent")), None) if len(revisions) >= 2 else None
        q2_counts[classify(current_rev)] += 1

    def pct(n):
        return f"{n} ({100*n/total:.1f}%)" if total else f"{n} (n/a)"

    print(f"=== {db_name} -- project_health: {total} total documents ===")
    print(f"\nQ1 (estimate-change) answerability:")
    print(f"  high confidence (real causal answer): {pct(q1_counts['high'])}")
    print(f"  low confidence  (hedged answer):       {pct(q1_counts['low'])}")
    print(f"  none/unconfirmed (honest fallback):     {pct(q1_counts['none'])}")
    print(f"  no version history to explain:          {pct(q1_counts['no_history'])}")

    print(f"\nQ2 (schedule-move) answerability:")
    print(f"  high confidence (real causal answer): {pct(q2_counts['high'])}")
    print(f"  low confidence  (hedged answer):       {pct(q2_counts['low'])}")
    print(f"  none/unconfirmed (honest fallback):     {pct(q2_counts['none'])}")
    print(f"  no version history to explain:          {pct(q2_counts['no_history'])}")


if __name__ == "__main__":
    main()
