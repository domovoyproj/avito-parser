"""Deterministic offline scoring audit. Usage: python tools/evaluate_feedback.py DB --out report.json"""

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path


def evaluate(path: Path) -> dict:
    with closing(sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        rows = [dict(row) for row in connection.execute("""WITH ranked AS (
            SELECT *,ROW_NUMBER() OVER(PARTITION BY item_id,user_id ORDER BY id DESC) AS rn
            FROM item_feedback_events)
            SELECT label,score,grade,score_version,score_confidence FROM ranked WHERE rn=1
            ORDER BY score_version,score,grade,label,id""")]
    versions = {}
    for row in rows:
        version = row["score_version"]
        group = versions.setdefault(version, {"count": 0, "false_positives": 0,
                                             "high_score_count": 0, "high_score_false_positives": 0})
        group["count"] += 1
        group["false_positives"] += row["label"] == "false_positive"
        if row["score"] is not None and row["score"] >= 70:
            group["high_score_count"] += 1
            group["high_score_false_positives"] += row["label"] == "false_positive"
    for group in versions.values():
        group["false_positive_rate"] = round(group["false_positives"] / group["count"], 4)
        group["high_score_false_positive_rate"] = (round(group["high_score_false_positives"] / group["high_score_count"], 4)
                                                   if group["high_score_count"] else None)
    return {"schema": 1, "definition": "Latest label per user/item; high score means >=70. Score is not a probability.",
            "examples": rows, "versions": versions}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.database)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Evaluated {len(result['examples'])} anonymized labels -> {args.out}")
