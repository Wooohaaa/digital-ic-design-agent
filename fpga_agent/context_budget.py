"""Deterministic context pruning with immutable objectives and explicit accounting."""
from __future__ import annotations

import copy
import json


class ContextBudgetError(ValueError):
    pass


def size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False))


def pack_context(context: dict, budget: int = 96000) -> tuple[dict, dict]:
    if type(budget) is not int or not 16000 <= budget <= 2_000_000:
        raise ContextBudgetError("上下文预算须为 16,000..2,000,000 字符。")
    packed = copy.deepcopy(context)
    before = {key: size(value) for key, value in packed.items()}
    # This is an input character cap, not a provider tokenizer or a claimed model window.
    reserve = min(8000, budget // 4)
    capacity = budget - reserve
    protected = {"objective", "acceptance_target", "steering", "memory", "plan", "project_instructions", "experience_metrics",
                 "task_projects", "last_observation", "loaded_skills", "remaining_iterations", "project", "orchestration"}
    critical = {key: value for key, value in packed.items() if key in protected}
    if size(critical) + 3000 > capacity:
        raise ContextBudgetError("目标、追加要求、已加载技能和当前观察超过上下文预算；请 /continue ID --context-chars 更大值，原始记录已保留。")
    changes = []

    def over():
        # Reserve room for the accounting report itself, including section sizes.
        return size(packed) + 3000 > capacity

    if over() and "engineering_review" in packed:
        review = packed["engineering_review"]
        if isinstance(review, dict):
            packed["engineering_review"] = {key: review[key] for key in
                ("status", "artifact_ready", "engineering_signoff", "required_open_items", "reason") if key in review}
            items = packed["engineering_review"].get("required_open_items")
            if isinstance(items, list):
                packed["engineering_review"]["required_open_items"] = items[:12]
            changes.append("engineering_review: summary; inspect reports/engineering-review.json for details")
    if over() and "history" in packed:
        packed["history"] = [{key: step[key] for key in ("iteration", "status", "action", "exit_code", "message") if key in step}
                             for step in packed["history"]]
        changes.append("history: old output removed; recall taskID:iteration retrieves archived observations")
    for key in ("sources", "attachments"):
        if over() and isinstance(packed.get(key), dict):
            packed[key] = {name: value[:800] if isinstance(value, str) else value for name, value in packed[key].items()}
            changes.append(key + ": excerpts; inspect/search retrieves complete files")
    if over() and isinstance(packed.get("skill_catalog"), dict):
        catalog = packed["skill_catalog"]
        packed["skill_catalog"] = {"skills": [{**item, "description": item.get("description", "")[:160]}
            for item in catalog.get("skills", [])[:20]], "total": len(catalog.get("skills", [])), "bounded": True}
        changes.append("skill_catalog: metadata reduced; skill query can discover/load additional entries")
    if over() and isinstance(packed.get("manifest"), dict):
        manifest = packed["manifest"]
        packed["manifest"] = {key: manifest[key] for key in ("project", "device", "top_module") if key in manifest}
        changes.append("manifest: summary; inspect project.json for full configuration")
    for key in ("sources", "attachments", "engineering_review", "source_index", "history", "skill_catalog", "projects", "experience_catalog"):
        if not over():
            break
        if key in packed and key not in protected:
            packed.pop(key)
            changes.append(key + ": omitted from request; original context remains in the step archive")
    if over():
        raise ContextBudgetError("当前上下文仍超过预算，请扩大 --context-chars；任务目标和补充要求未被裁剪。")
    report = {"method": "JSON characters, not provider tokens", "limit_chars": budget,
              "reserved_chars": reserve, "input_capacity_chars": capacity, "before_chars": size(context),
              "compacted": bool(changes), "changes": changes, "sections_before": before,
              "sections_after": {key: size(value) for key, value in packed.items()}}
    packed["context_report"] = report
    # Fixed-point measurement: report is itself part of the submitted JSON.
    report["after_chars"] = size(packed)
    report["after_chars"] = size(packed)
    if size(packed) > capacity:
        raise ContextBudgetError("上下文审计信息超过预算，请扩大 --context-chars。")
    return packed, report
