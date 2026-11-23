"""流式数据导出专用接口：调用日志导出任务登记、执行、版本清单与文件下载。

通用资源路由只负责清单的查询/登记/动作；导出文件的产生与取回走这里，
保证每次下载拿到的是某一版完整文件（明细行与逐类合计来自同一次扫描）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.services import service_for
from app.services.stream_export import StreamExportService

router = APIRouter(prefix="/api/stream_export", tags=["流式数据导出"])


class LogTaskPayload(BaseModel):
    """从 API 调用日志页登记导出任务的参数：API 与时段随任务留档，可按调用编号核对。"""

    API名称: str
    时段开始: str = ""
    时段结束: str = ""
    任务名称: str | None = None
    导出格式: str = "csv"


class ExecutePayload(BaseModel):
    """执行参数：interrupt_after 演练长导出写到一半断开（半成品不提交版本）。"""

    interrupt_after: int | None = Field(default=None, ge=0)


def _service() -> StreamExportService:
    service = service_for("stream_export")
    assert isinstance(service, StreamExportService)
    return service


@router.post("/log-task")
def create_log_export_task(payload: LogTaskPayload) -> dict[str, Any]:
    """登记调用日志导出任务（排队中），与 API 调用日志页的筛选条件关联。"""
    api_name = payload.API名称.strip()
    if not api_name:
        raise HTTPException(status_code=422, detail="API名称不能为空")
    entry = _service().create_log_export(
        api_name=api_name,
        start=payload.时段开始.strip(),
        end=payload.时段结束.strip(),
        export_format=payload.导出格式.strip() or "csv",
        task_name=(payload.任务名称 or "").strip() or None,
    )
    return {"ok": True, "message": f"导出任务已登记：#{entry['id']}", "entry": entry}


@router.post("/{entry_id}/execute")
def execute_export(
    entry_id: int,
    payload: ExecutePayload | None = None,
    interrupt_after: int | None = Query(default=None, ge=0),
) -> dict[str, Any]:
    """执行导出：成功则提交一个新文件版本；中断则任务转为 导出失败。"""
    cutoff = interrupt_after
    if cutoff is None and payload is not None:
        cutoff = payload.interrupt_after
    result = _service().execute_export(entry_id, interrupt_after=cutoff)
    if "不存在" in result.get("message", ""):
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@router.get("/{entry_id}/manifest")
def export_manifest(entry_id: int) -> dict[str, Any]:
    """版本清单：再次下载前可查每版使用的调用范围、行数与逐类合计。"""
    manifest = _service().get_manifest(entry_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"导出任务 {entry_id} 不存在或已归档")
    return manifest


@router.get("/{entry_id}/download")
def download_export(entry_id: int, version: int | None = Query(default=None, ge=1)) -> StreamingResponse:
    """下载导出文件：缺省取最新版，可用 version 取历史版本，内容按版完整取回。"""
    service = _service()
    if service.get_entry(entry_id) is None:
        raise HTTPException(status_code=404, detail=f"导出任务 {entry_id} 不存在或已归档")
    found = service.get_file(entry_id, version)
    if found is None:
        if version is None:
            raise HTTPException(status_code=409, detail="导出任务尚未生成文件，请先执行导出")
        raise HTTPException(status_code=404, detail=f"导出任务 {entry_id} 没有版本 v{version} 的文件")
    filename, content = found
    return StreamingResponse(
        iter([content]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
