import asyncio
import time
import json
from typing import Any, Dict, List
from fastapi import HTTPException, WebSocket

class ParsingJobManager:
    def __init__(self):
        self.active_jobs: Dict[str, Dict[str, Any]] = {}
        self.subscribers: Dict[str, List[WebSocket]] = {}
        self.tasks = {}

    def create_job(self, job_id: str, url: str, max_pages: int, engine: str) -> Dict[str, Any]:
        for key, old in list(self.active_jobs.items()):
            if key not in self.tasks and time.time() - old['start_time'] > 3600:
                self.active_jobs.pop(key, None)
                self.subscribers.pop(key, None)
        if len(self.tasks) >= 3 or len(self.active_jobs) >= 100:
            raise HTTPException(status_code=429, detail='Достигнут лимит задач парсинга')
        job = {
            "job_id": job_id,
            "url": url,
            "max_pages": max_pages,
            "engine": engine,
            "status": "pending",
            "progress_pct": 0,
            "current_page": 0,
            "items_found": 0,
            "logs": [],
            "items": [],
            "errors": [],
            "start_time": time.time(),
            "elapsed_sec": 0
        }
        self.active_jobs[job_id] = job
        self.subscribers[job_id] = []
        return job

    async def broadcast(self, job_id: str, message_type: str, data: Any):
        if job_id in self.active_jobs:
            if message_type == "log":
                self.active_jobs[job_id]["logs"].append(data)
                self.active_jobs[job_id]['logs'] = self.active_jobs[job_id]['logs'][-500:]
            elif message_type == "progress":
                self.active_jobs[job_id].update(data)
            elif message_type == "complete":
                self.active_jobs[job_id]["status"] = "completed"
                self.active_jobs[job_id].update(data)
            elif message_type == "error":
                self.active_jobs[job_id]["status"] = "error"
                self.active_jobs[job_id]["errors"].append(data)

        msg = json.dumps({"type": message_type, "data": data, "job_id": job_id})
        for ws in self.subscribers.get(job_id, []):
            try:
                await ws.send_text(msg)
            except Exception:
                pass


job_manager=ParsingJobManager()
