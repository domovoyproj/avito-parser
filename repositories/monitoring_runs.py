"""Durable, bounded history of monitoring checks."""

import time
from typing import Any, Dict, List, Optional

import aiosqlite


class MonitoringRunsRepository:
    async def record_monitoring_run(self, record: Dict[str, Any]) -> None:
        async with self.connection() as connection:
            await connection.execute(
                """INSERT OR IGNORE INTO monitoring_runs
                (run_id, search_id, search_name, started_at, finished_at,
                 duration_seconds, outcome, engine, found_count, new_count,
                 drops_count, error_type)
                VALUES (:run_id, :search_id, :search_name, :started_at, :finished_at,
                        :duration_seconds, :outcome, :engine, :found_count, :new_count,
                        :drops_count, :error_type)""",
                record,
            )
            await connection.execute(
                "DELETE FROM monitoring_runs WHERE started_at < ?",
                (time.time() - 30 * 86400,),
            )
            await connection.commit()

    async def get_monitoring_runs(
        self, search_id: Optional[int] = None, outcome: Optional[str] = None,
        limit: int = 50, offset: int = 0,
    ) -> List[Dict[str, Any]]:
        clauses = []
        params: List[Any] = []
        if search_id is not None:
            clauses.append("search_id = ?")
            params.append(search_id)
        if outcome:
            clauses.append("outcome = ?")
            params.append(outcome)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        async with self.connection() as connection:
            connection.row_factory = aiosqlite.Row
            rows = await (await connection.execute(
                "SELECT * FROM monitoring_runs" + where +
                " ORDER BY started_at DESC, run_id DESC LIMIT ? OFFSET ?",
                (*params, min(200, max(1, limit)), max(0, offset)),
            )).fetchall()
            return [dict(row) for row in rows]

    async def get_monitoring_run_stats(self, days: int = 7) -> Dict[str, Any]:
        async with self.connection() as connection:
            rows = await (await connection.execute(
                "SELECT outcome, COUNT(*) FROM monitoring_runs WHERE started_at >= ? GROUP BY outcome",
                (time.time() - max(1, days) * 86400,),
            )).fetchall()
            return {"days": days, "outcomes": {name: count for name, count in rows}}
