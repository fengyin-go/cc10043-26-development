"""流式数据导出领域服务：按时间范围和条件将流式处理结果导出为离线文件。

数据链路位置：数据消费层，将处理完成的数据产物导出为文件供下游分析和离线使用。

导出数据源支持两类：
- 采集载荷：仅扫描消息载荷存储（payload_store），按数据源名子串匹配。
- 调用日志：仅扫描 API 调用日志（api_logger），按 API 名称精确匹配 + 调用时间窗口
  过滤；同名载荷、名称相近（如 billing-test）的日志都不会混入文件。

每次成功导出都会提交一个不可变的文件版本（明细行 + 逐类合计来自同一次扫描快照），
下载接口按版本取回完整文件，不会出现前后两轮范围混杂的合计。
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store

# 已提交的导出文件内容：(导出任务ID, 版本号) -> 文件文本。
# 版本提交后内容不再变化，下载接口按版本原样取回。
EXPORT_FILES: dict[tuple[int, int], str] = {}

LOG_STATUSES = ["成功", "客户端错误", "服务端错误", "超时"]

# 列表字段之外、允许在登记时一并写入任务的扩展字段。
EXTRA_ENTRY_FIELDS = ["数据源类型", "API名称", "时段开始", "时段结束", "来源模块"]


def _parse_moment(value: str) -> datetime | None:
    """把 调用时间/时段边界 解析为可比较的时刻；解析失败返回 None。"""
    text = (value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _before(moment: str, bound: str) -> bool:
    """moment 严格早于 bound；两边都能解析时按时间比较，否则退化为字符串比较。"""
    dt, db = _parse_moment(moment), _parse_moment(bound)
    if dt is not None and db is not None:
        return dt < db
    return moment < bound


def _in_window(moment: str, start: str, end: str) -> bool:
    """判断调用时间是否落在 [start, end] 闭区间内；空边界表示不限制。"""
    if start and _before(moment, start):
        return False
    if end and _before(end, moment):
        return False
    return True


def _safe_cell(value: Any) -> Any:
    """与清单导出一致的 CSV 注入防护。"""
    text = str(value) if value is not None else ""
    return "'" + text if text.startswith(("=", "+", "-", "@")) else value


class StreamExportService(BaseModuleService):
    MODULE = "stream_export"
    ENTITY = "导出任务"
    OPERATION_TERM = "导出"
    LIST_FIELDS = ["任务名称", "数据源", "导出格式", "时间范围", "文件大小"]
    REQUIRED_FIELDS = ["任务名称", "数据源", "导出格式"]
    STATUS_ORDER = ["排队中", "导出中", "已完成", "导出失败"]
    ACTION_RULES = {"开始导出": "导出中", "重试导出": "导出中"}
    NEGATIVE_ACTIONS = []

    # ---- 登记与来源解析 ----

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """登记导出任务：除列表字段外，保留数据源类型、API名称、时段等扩展字段。"""
        entry, missing = super().create_entry(values)
        if entry is None:
            return None, missing
        for field in EXTRA_ENTRY_FIELDS:
            if str(values.get(field) or "").strip():
                entry[field] = str(values[field]).strip()
        entry["versions"] = []
        return entry, []

    def create_log_export(
        self,
        api_name: str,
        start: str,
        end: str,
        export_format: str = "csv",
        task_name: str | None = None,
    ) -> dict[str, Any]:
        """从 API 调用日志页登记一个调用日志导出任务（排队中，等待执行）。"""
        rows = store.rows(self.MODULE)
        entry = {
            "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
            "任务名称": task_name or f"调用日志导出-{api_name}",
            "数据源": "调用日志",
            "数据源类型": "调用日志",
            "API名称": api_name,
            "时段开始": start,
            "时段结束": end,
            "导出格式": export_format or "csv",
            "时间范围": f"{start}~{end}",
            "文件大小": "0",
            "来源模块": "api_logger",
            "status": "排队中",
            "pending": True,
            "abnormal": False,
            "versions": [],
        }
        rows.append(entry)
        return entry

    def _source_type(self, entry: dict[str, Any]) -> str:
        """解析任务的数据源类型；老数据没有显式类型时按字段推断，默认采集载荷。"""
        explicit = str(entry.get("数据源类型") or "").strip()
        if explicit in ("调用日志", "api_logger"):
            return "调用日志"
        if explicit in ("采集载荷", "payload_store"):
            return "采集载荷"
        if str(entry.get("API名称") or "").strip():
            return "调用日志"
        if str(entry.get("数据源") or "").strip() in ("调用日志", "api_logger"):
            return "调用日志"
        return "采集载荷"

    def _window(self, entry: dict[str, Any]) -> tuple[str, str]:
        """取任务的时段边界：优先显式字段，其次解析 时间范围 的 '起~止' 写法。"""
        start = str(entry.get("时段开始") or "").strip()
        end = str(entry.get("时段结束") or "").strip()
        if not start and not end:
            text = str(entry.get("时间范围") or "")
            if "~" in text:
                start, end = (part.strip() for part in text.split("~", 1))
        return start, end

    # ---- 数据筛选：筛选口径与下载文件保持一致 ----

    def _match_logs(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        """调用日志口径：API 名称精确相等 + 调用时间落在时段内，按调用编号排序。"""
        api_name = str(entry.get("API名称") or "").strip()
        start, end = self._window(entry)
        matched = []
        for row in store.rows("api_logger"):
            if api_name and str(row.get("API名称") or "").strip() != api_name:
                continue
            if not _in_window(str(row.get("调用时间") or ""), start, end):
                continue
            matched.append(row)
        matched.sort(key=lambda r: int(r.get("id", 0)))
        return matched

    def _match_payloads(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        """采集载荷口径：只扫描载荷存储，按数据源名子串匹配。"""
        source = str(entry.get("数据源") or "").strip()
        rows = list(store.rows("payload_store"))
        if source and source not in ("采集载荷", "payload_store"):
            rows = [row for row in rows if source in str(row)]
        rows.sort(key=lambda r: int(r.get("id", 0)))
        return rows

    # ---- 文件生成：明细与合计来自同一次扫描快照 ----

    def _build_log_file(
        self, entry: dict[str, Any], rows: list[dict[str, Any]], version: int, now: str
    ) -> tuple[str, dict[str, Any]]:
        api_name = str(entry.get("API名称") or "").strip()
        start, end = self._window(entry)
        ids = [int(r.get("id", 0)) for r in rows]
        id_range = f"{min(ids)}~{max(ids)}" if ids else ""
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(["# 导出任务", entry.get("id")])
        writer.writerow(["# 任务名称", _safe_cell(entry.get("任务名称", ""))])
        writer.writerow(["# 版本", version])
        writer.writerow(["# 数据源类型", "调用日志"])
        writer.writerow(["# API名称", _safe_cell(api_name)])
        writer.writerow(["# 时段开始", start])
        writer.writerow(["# 时段结束", end])
        writer.writerow(["# 调用编号范围", id_range])
        writer.writerow(["# 生成时间", now])
        writer.writerow(["调用编号", "调用时间", "API名称", "请求参数摘要", "响应状态码", "耗时毫秒", "结果"])
        for row in rows:
            writer.writerow([
                row.get("id"),
                _safe_cell(row.get("调用时间", "")),
                _safe_cell(row.get("API名称", "")),
                _safe_cell(row.get("请求参数摘要", "")),
                _safe_cell(row.get("响应状态码", "")),
                _safe_cell(row.get("耗时毫秒", "")),
                _safe_cell(row.get("status", "")),
            ])
        totals = {status: sum(1 for r in rows if r.get("status") == status) for status in LOG_STATUSES}
        for status in LOG_STATUSES:
            writer.writerow(["# 合计", status, totals[status]])
        writer.writerow(["# 合计", "总计", len(rows)])
        summary = {"调用编号范围": id_range, "行数": len(rows), **totals, "总计": len(rows)}
        return output.getvalue(), summary

    def _build_payload_file(
        self, entry: dict[str, Any], rows: list[dict[str, Any]], version: int, now: str
    ) -> tuple[str, dict[str, Any]]:
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(["# 导出任务", entry.get("id")])
        writer.writerow(["# 任务名称", _safe_cell(entry.get("任务名称", ""))])
        writer.writerow(["# 版本", version])
        writer.writerow(["# 数据源类型", "采集载荷"])
        writer.writerow(["# 数据源", _safe_cell(entry.get("数据源", ""))])
        writer.writerow(["# 生成时间", now])
        writer.writerow(["载荷编号", "采集批次", "消息ID", "原始载荷", "存储时间", "压缩后大小"])
        for row in rows:
            writer.writerow([
                row.get("id"),
                _safe_cell(row.get("采集批次", "")),
                _safe_cell(row.get("消息ID", "")),
                _safe_cell(row.get("原始载荷", "")),
                _safe_cell(row.get("存储时间", "")),
                _safe_cell(row.get("压缩后大小", "")),
            ])
        writer.writerow(["# 合计", "总计", len(rows)])
        return output.getvalue(), {"行数": len(rows), "总计": len(rows)}

    # ---- 导出执行与版本管理 ----

    def execute_export(self, entry_id: int, interrupt_after: int | None = None) -> dict[str, Any]:
        """执行导出任务：按数据源类型筛选数据并生成指定格式文件。

        interrupt_after 用于演练长导出中断：写入第 N 行后连接断开，
        半成品文件直接丢弃、不提交版本，任务转为 导出失败，等待重试。
        """
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "导出任务不存在"}
        source_type = self._source_type(entry)
        entry["status"] = "导出中"
        entry["pending"] = True
        if source_type == "调用日志":
            data_rows = self._match_logs(entry)
        else:
            data_rows = self._match_payloads(entry)
        if interrupt_after is not None and interrupt_after < len(data_rows):
            entry["status"] = "导出失败"
            entry["pending"] = True
            entry["abnormal"] = True
            entry["最近错误"] = f"导出中断：写入第 {interrupt_after} 行后连接断开，半成品文件已丢弃"
            return {"ok": False, "message": entry["最近错误"], "rows_written": interrupt_after}
        now = datetime.now(timezone.utc).isoformat()
        versions: list[dict[str, Any]] = entry.setdefault("versions", [])
        version = len(versions) + 1
        if source_type == "调用日志":
            content, summary = self._build_log_file(entry, data_rows, version, now)
        else:
            content, summary = self._build_payload_file(entry, data_rows, version, now)
        filename = f"stream-export-{entry_id}-v{version}.csv"
        manifest = {
            "版本": version,
            "生成时间": now,
            "数据源类型": source_type,
            "API名称": str(entry.get("API名称") or ""),
            "时段开始": self._window(entry)[0],
            "时段结束": self._window(entry)[1],
            "数据源": str(entry.get("数据源") or ""),
            "文件名称": filename,
            "下载地址": f"/api/stream_export/{entry_id}/download?version={version}",
            **summary,
        }
        versions.append(manifest)
        EXPORT_FILES[(entry_id, version)] = content
        entry["当前版本"] = version
        entry["调用编号范围"] = summary.get("调用编号范围", "")
        entry["行数"] = summary.get("行数", 0)
        entry["文件大小"] = str(len(content))
        entry["status"] = "已完成"
        entry["pending"] = False
        entry["abnormal"] = False
        entry.pop("最近错误", None)
        return {"ok": True, "message": f"导出完成，文件版本 v{version}", "version": version, "manifest": manifest}

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        """开始导出 / 重试导出 直接驱动一次完整导出，而不是只改状态。"""
        if action in self.ACTION_RULES:
            entry = store.find(self.MODULE, entry_id)
            if entry is None:
                return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
            result = self.execute_export(entry_id)
            if not result.get("ok"):
                return entry, result["message"]
            return entry, f"{self.ENTITY}已{action}，{result['message']}"
        return super().run_action(entry_id, action)

    # ---- 版本清单与文件取回 ----

    def get_manifest(self, entry_id: int) -> dict[str, Any] | None:
        """返回任务的版本清单：再次下载前可查到每版使用的调用范围及行数。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None
        versions = list(entry.get("versions") or [])
        return {
            "任务ID": entry_id,
            "任务名称": entry.get("任务名称", ""),
            "status": entry.get("status", ""),
            "数据源类型": self._source_type(entry),
            "API名称": str(entry.get("API名称") or ""),
            "时段开始": self._window(entry)[0],
            "时段结束": self._window(entry)[1],
            "当前版本": entry.get("当前版本", 0),
            "调用编号范围": entry.get("调用编号范围", ""),
            "行数": entry.get("行数", 0),
            "下载地址": f"/api/stream_export/{entry_id}/download",
            "versions": versions,
        }

    def get_file(self, entry_id: int, version: int | None = None) -> tuple[str, str] | None:
        """取回某一版导出文件；缺省取最新版。返回 (文件名, 内容)。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None
        versions: list[dict[str, Any]] = entry.get("versions") or []
        if not versions:
            return None
        target = version if version is not None else int(versions[-1]["版本"])
        content = EXPORT_FILES.get((entry_id, target))
        if content is None:
            return None
        return f"stream-export-{entry_id}-v{target}.csv", content

    # ---- 校验与审计 ----

    def validate_export_params(self, entry_id: int) -> list[str]:
        """校验导出参数的有效性。"""
        errors = []
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return ["任务不存在"]
        fmt = entry.get("导出格式", "")
        if fmt not in ("csv", "json", "parquet", "avro"):
            errors.append(f"不支持的导出格式: {fmt}")
        if self._source_type(entry) == "调用日志":
            has_data = bool(self._match_logs(entry))
        else:
            has_data = bool(self._match_payloads(entry))
        if not has_data:
            errors.append(f"数据源 '{entry.get('数据源', '')}' 无可用数据")
        return errors

    def link_to_audit(self, entry_id: int) -> dict[str, Any]:
        """导出结果与审计记录关联。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "任务不存在"}
        entry["abnormal"] = False
        source = entry.get("数据源", "")
        now = datetime.now(timezone.utc).isoformat()
        audit_rows = store.rows("stream_audit")
        aid = max((int(r.get("id", 0)) for r in audit_rows), default=0) + 1
        audit_rows.append({
            "id": aid, "处理时间": now, "数据源": f"export:{source}",
            "消息总数": str(entry.get("行数", 0) or 0), "成功数": str(entry.get("行数", 0) or 0),
            "失败原因分布": "",
            "status": "正常", "pending": True, "abnormal": False,
        })
        return {"ok": True, "audit_id": aid}
