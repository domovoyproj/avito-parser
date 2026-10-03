"""Immutable snapshots of user feedback about a scoring decision."""

from datetime import datetime, timezone

import aiosqlite


LABELS = frozenset({"helpful", "false_positive", "bought", "sold", "stale"})


class FeedbackRepository:
    async def record_item_feedback(self, item_id: str, user_id: int, label: str, note: str = "") -> dict | None:
        if label not in LABELS:
            raise ValueError("Unknown feedback label")
        note = note.strip()
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            item = await (await db.execute(
                "SELECT id,deal_score,deal_grade,score_version,score_confidence,category,search_query_id FROM items WHERE id=?",
                (item_id,),
            )).fetchone()
            if item is None:
                return None
            last = await (await db.execute(
                "SELECT label,note FROM item_feedback_events WHERE item_id=? AND user_id=? ORDER BY id DESC LIMIT 1",
                (item_id, user_id),
            )).fetchone()
            if last and last["label"] == label and last["note"] == note:
                return {"created": False, "label": label, "note": note}
            await db.execute("""INSERT INTO item_feedback_events
                (item_id,user_id,label,note,score,grade,score_version,score_confidence,category,search_query_id,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (
                item_id, user_id, label, note, item["deal_score"], item["deal_grade"],
                item["score_version"] or "legacy-1", item["score_confidence"],
                item["category"], item["search_query_id"], datetime.now(timezone.utc).isoformat(),
            ))
            await db.commit()
            return {"created": True, "label": label, "note": note}

    async def item_feedback_history(self, item_id: str, user_id: int) -> list[dict]:
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute("""SELECT f.id,f.label,f.note,f.score,f.grade,f.score_version,
                f.created_at,u.username AS author FROM item_feedback_events f
                JOIN users u ON u.id=f.user_id WHERE f.item_id=? AND f.user_id=? ORDER BY f.id DESC""",
                (item_id, user_id))).fetchall()
            return [dict(row) for row in rows]

    async def feedback_report(self, category: str | None = None, search_query_id: int | None = None,
                              score_version: str | None = None) -> dict:
        where = []
        params = []
        for column, value in (("category", category), ("search_query_id", search_query_id),
                              ("score_version", score_version)):
            if value is not None:
                where.append(f"{column}=?")
                params.append(value)
        predicate = " AND ".join(where) or "1=1"
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(f"""WITH ranked AS (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY item_id,user_id ORDER BY id DESC) AS rn
                FROM item_feedback_events)
                SELECT item_id,label,score,grade,score_version,category,search_query_id
                FROM ranked WHERE rn=1 AND {predicate}""", params)).fetchall()
            items_where = []
            items_params = []
            # Inventory is current; historical scoring versions have no historic inventory denominator.
            for column, value in (("category", category), ("search_query_id", search_query_id)):
                if value is not None:
                    items_where.append(f"{column}=?")
                    items_params.append(value)
            denominator = (await (await db.execute(
                "SELECT COUNT(*) FROM items WHERE " + (" AND ".join(items_where) or "1=1"), items_params
            )).fetchone())[0]
        labels = {}
        grades = {}
        scores = {"0-49": 0, "50-69": 0, "70-84": 0, "85-100": 0}
        for row in rows:
            labels[row["label"]] = labels.get(row["label"], 0) + 1
            grades[row["grade"] or "unknown"] = grades.get(row["grade"] or "unknown", 0) + 1
            score = row["score"]
            bucket = "85-100" if score is not None and score >= 85 else "70-84" if score is not None and score >= 70 else "50-69" if score is not None and score >= 50 else "0-49"
            scores[bucket] += 1
        distinct_items = len({row["item_id"] for row in rows})
        count = len(rows)
        return {"count": count, "distinct_items": distinct_items, "items_in_scope": denominator,
                "coverage": round(distinct_items / denominator, 4) if denominator else 0,
                "false_positive_rate": round(labels.get("false_positive", 0) / count, 4) if count else None,
                "labels": labels, "grades": grades, "scores": scores,
                "calibration_ready": count >= 30 and distinct_items >= 10,
                "filters": {"category": category, "search_query_id": search_query_id,
                            "score_version": score_version}}

    async def feedback_dataset(self) -> list[dict]:
        """Latest labels only; omit usernames, item IDs, titles, notes and URLs."""
        async with self.connection() as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute("""WITH ranked AS (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY item_id,user_id ORDER BY id DESC) AS rn
                FROM item_feedback_events)
                SELECT label,score,grade,score_version,score_confidence FROM ranked WHERE rn=1
                ORDER BY score_version,score,grade,label,id""")).fetchall()
            return [dict(row) for row in rows]
