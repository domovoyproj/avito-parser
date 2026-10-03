from typing import Literal, Optional
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from export_jobs import ExportJobs
from dependencies import get_export_jobs
from auth_dependencies import require_auth
from models import User

router = APIRouter()


@router.get("/api/export-jobs")
async def list_exports(export_jobs: ExportJobs = Depends(get_export_jobs)):
    async with export_jobs.db.connection() as connection:
        connection.row_factory = __import__("aiosqlite").Row
        rows = await (
            await connection.execute(
                "SELECT id,format,status,created_at,rows_written,error FROM export_jobs ORDER BY created_at DESC LIMIT 50"
            )
        ).fetchall()
    return {"jobs": [dict(row) for row in rows]}


class ExportRequest(BaseModel):
    format: Literal["csv", "xlsx"] = "csv"
    search_query_id: Optional[str] = Field(default=None, pattern=r"^(?:-1|[1-9][0-9]*)$")
    query: Optional[str] = Field(default=None, max_length=200)
    min_price: Optional[int] = Field(default=None, ge=0)
    max_price: Optional[int] = Field(default=None, ge=0)
    with_discount_only: bool = False
    with_delivery_only: bool = False
    favorites_only: bool = False
    hot_deals_only: bool = False
    gems_only: bool = False
    hide_reserved: bool = False
    deal_grade: Optional[str] = None
    min_deal_score: Optional[int] = Field(default=None, ge=0, le=100)
    sort_by: str = "newest"

    @model_validator(mode="after")
    def validate_price_range(self):
        if self.min_price is not None and self.max_price is not None and self.min_price > self.max_price:
            raise ValueError("Минимальная цена превышает максимальную")
        return self


@router.post("/api/export-jobs", status_code=202)
async def create_export(req: ExportRequest, export_jobs: ExportJobs = Depends(get_export_jobs), user: User = Depends(require_auth)):
    try:
        job_id = await export_jobs.enqueue(
            req.format, req.model_dump(exclude={"format", "sort_by"}) | {"user_id": user.id}
        )
    except ValueError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return {"job_id": job_id, "status": "pending"}


@router.get("/api/export-jobs/{job_id}")
async def export_status(job_id: str, export_jobs: ExportJobs = Depends(get_export_jobs)):
    job = await export_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Экспорт не найден")
    return {
        "job_id": job_id,
        "status": job["status"],
        "rows_written": job["rows_written"],
        "error": job["error"],
        "download_url": f"/api/export-jobs/{job_id}/download"
        if job["status"] == "completed"
        else None,
    }


@router.get("/api/export-jobs/{job_id}/download")
async def download_export(job_id: str, export_jobs: ExportJobs = Depends(get_export_jobs)):
    job = await export_jobs.get(job_id)
    if not job or job["status"] != "completed":
        raise HTTPException(status_code=409, detail="Экспорт ещё не готов")
    path = export_jobs.directory / f"export_{job['id']}.{job['format']}"
    if not path.is_file():
        raise HTTPException(status_code=410, detail="Файл экспорта удалён")
    return FileResponse(path, filename=path.name)
