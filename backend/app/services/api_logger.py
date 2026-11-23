"""API调用日志领域服务：记录每次API调用的请求参数、响应和耗时，管理日志查询和统计分析。

数据链路位置：监控采集层，记录全链路中每个API调用的详细日志，支撑调用统计和失败分析。

查询口径与流式数据导出保持一致：API名称 精确匹配（billing 不会带出 billing-test），
调用时间 支持 '起~止' 区间筛选，保证页面筛选结果和下载文件对得上。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.services.stream_export import _in_window
from app.store import store


def _status_for_code(status_code: int) -> tuple[str, bool]:
    """按响应状态码归类调用结果。"""
    if 200 <= status_code < 300:
        return "成功", False
    if 400 <= status_code < 500:
        return "客户端错误", True
    if status_code >= 500:
        return "服务端错误", True
    return "超时", True


class ApiLoggerService(BaseModuleService):
    MODULE = "api_logger"
    ENTITY = "调用日志"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["调用时间", "API名称", "请求参数摘要", "响应状态码", "耗时毫秒"]
    REQUIRED_FIELDS = ["调用时间", "API名称", "请求参数摘要"]
    STATUS_ORDER = ["成功", "客户端错误", "服务端错误", "超时"]
    ACTION_RULES = {"查看详情": "客户端错误", "重试调用": "服务端错误"}
    NEGATIVE_ACTIONS = []

    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(self.MODULE))
        for field, expected in (filters or {}).items():
            if field not in self.LIST_FIELDS:
                continue
            if field == "API名称":
                # 精确匹配：billing 不命中 billing-test，与导出文件口径一致。
                rows = [r for r in rows if str(r.get("API名称") or "").strip() == expected.strip()]
            elif field == "调用时间" and "~" in expected:
                start, end = (part.strip() for part in expected.split("~", 1))
                rows = [r for r in rows if _in_window(str(r.get("调用时间") or ""), start, end)]
            else:
                rows = [
                    r for r in rows
                    if expected.casefold() in str(r.get(field, "")).casefold()
                ]
        if keyword and self.LIST_FIELDS:
            field = self.LIST_FIELDS[0]
            rows = [row for row in rows if keyword.casefold() in str(row.get(field, "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """登记调用日志：按响应状态码推导调用结果，保证逐类合计有据可依。"""
        entry, missing = super().create_entry(values)
        if entry is None:
            return None, missing
        code_text = str(entry.get("响应状态码") or "").strip()
        if code_text.isdigit():
            status, abnormal = _status_for_code(int(code_text))
            entry["status"] = status
            entry["abnormal"] = abnormal
            entry["pending"] = not abnormal
        return entry, []

    # ---- 业务扩展：日志收集与统计分析 ----

    def log_api_call(self, api_name: str, params: str, status_code: int, elapsed_ms: int) -> dict[str, Any]:
        """记录一次API调用的完整日志。"""
        rows = store.rows(self.MODULE)
        log_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        now = datetime.now(timezone.utc).isoformat()
        status, abnormal = _status_for_code(status_code)
        entry = {
            "id": log_id, "调用时间": now, "API名称": api_name,
            "请求参数摘要": params, "响应状态码": str(status_code),
            "耗时毫秒": str(elapsed_ms), "status": status,
            "pending": not abnormal, "abnormal": abnormal,
        }
        rows.append(entry)
        return {"ok": True, "log_id": log_id, "status": status}

    def aggregate_call_stats(self, api_name: str | None = None) -> dict[str, Any]:
        """按API名称聚合调用统计，支持全局或按API筛选。"""
        rows = store.rows(self.MODULE)
        if api_name:
            rows = [r for r in rows if api_name in str(r.get("API名称", ""))]
        total = len(rows)
        success = len([r for r in rows if r.get("status") == "成功"])
        client_err = len([r for r in rows if r.get("status") == "客户端错误"])
        server_err = len([r for r in rows if r.get("status") == "服务端错误"])
        timeout = len([r for r in rows if r.get("status") == "超时"])
        return {"api_name": api_name or "全部", "total": total, "success": success,
                "client_error": client_err, "server_error": server_err, "timeout": timeout,
                "success_rate": round(success / max(total, 1) * 100, 1)}

    def validate_api_exists(self, api_name: str) -> bool:
        """校验API是否已在连接器或调度计划中注册。"""
        connectors = store.rows("api_connector")
        schedules = store.rows("api_scheduler")
        has_connector = any(api_name in str(c.get("连接器名称", "")) for c in connectors)
        has_schedule = any(api_name in str(s.get("关联API", "")) for s in schedules)
        return has_connector or has_schedule

    def route_errors_to_isolation(self, entry_id: int) -> dict[str, Any]:
        """调用错误日志路由到失败隔离区。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") in ("成功",):
            return {"ok": False, "message": "无需隔离"}
        now = datetime.now(timezone.utc).isoformat()
        isolation_rows = store.rows("failure_isolation")
        fid = max((int(r.get("id", 0)) for r in isolation_rows), default=0) + 1
        isolation_rows.append({
            "id": fid, "失败消息ID": f"api-err-{entry_id}",
            "失败原因": f"{entry.get('status')}: {entry.get('响应状态码', '')}",
            "原始Topic": entry.get("API名称", ""),
            "隔离时间": now, "重试次数": "0",
            "status": "待处理", "pending": True, "abnormal": False,
        })
        return {"ok": True, "message": "已路由至隔离区", "isolation_id": fid}

    def export_logs(self, api_name: str, time_range: tuple[str, str], export_format: str = "csv") -> dict[str, Any]:
        """导出API调用日志到流式数据导出：登记一个调用日志类型的导出任务。"""
        export_rows = store.rows("stream_export")
        eid = max((int(r.get("id", 0)) for r in export_rows), default=0) + 1
        export_rows.append({
            "id": eid, "任务名称": f"日志导出-{api_name}",
            "数据源": "调用日志", "数据源类型": "调用日志",
            "API名称": api_name,
            "时段开始": time_range[0], "时段结束": time_range[1],
            "导出格式": export_format,
            "时间范围": f"{time_range[0]}~{time_range[1]}",
            "文件大小": "0", "来源模块": "api_logger",
            "status": "排队中", "pending": True, "abnormal": False,
            "versions": [],
        })
        return {"ok": True, "export_id": eid, "api_name": api_name}

    def check_rate_limit_status(self, api_name: str) -> dict[str, Any]:
        """检查与API关联的频控规则状态。"""
        rules = store.rows("rate_guard")
        matching = [r for r in rules if api_name in str(r.get("限流维度", ""))]
        return {"api_name": api_name, "active_rules": len([r for r in matching if r.get("status") == "已生效"]),
                "total_rules": len(matching)}