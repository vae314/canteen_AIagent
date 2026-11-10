"""数字资产管理：把每次分析结果持久化到 meal_log.jsonl，并支持读取与统计。

这是课程的"数字资产管理"主题的最小落地：
- 每次结构化结果 + 营养计算被追加写入一个 JSONL 文件（可追加、可重放）。
- 提供读取与简单聚合（平均热量、记录条数、风险分布），用于界面上的历史页。
"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from .schema import MealObservation

LOG_PATH = Path(os.getenv("MEAL_LOG_PATH", "meal_log.jsonl"))


def save_record(obs: MealObservation, nutrition: dict, risk: str, is_demo: bool = False) -> dict:
    """把一次分析结果落盘，返回写入的记录。"""
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "is_demo": is_demo,
        "scene": obs.scene,
        "dishes": [d.model_dump() for d in obs.dishes],
        "nutrition": nutrition["total"],
        "user_goal": obs.user_goal,
        "diet_risk": risk,
        "model_risk": obs.diet_risk,
        "confidence": obs.confidence,
        "summary": obs.summary,
        "evidence": obs.evidence,
    }
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def load_records(limit: int = 20) -> list[dict]:
    """读取最近的记录（倒序）。"""
    if not LOG_PATH.exists():
        return []
    lines = LOG_PATH.read_text(encoding="utf-8").strip().splitlines()
    records = [json.loads(line) for line in lines if line.strip()]
    return records[::-1][:limit]


def summarize(records: list[dict]) -> dict:
    """对历史记录做简单聚合统计。"""
    if not records:
        return {"count": 0}
    kcal_list = [r["nutrition"]["kcal"] for r in records]
    risk_count = {"low": 0, "medium": 0, "high": 0}
    goal_count: dict[str, int] = {}
    for r in records:
        risk_count[r.get("diet_risk", "low")] = risk_count.get(r.get("diet_risk", "low"), 0) + 1
        goal = r.get("user_goal") or "未设定"
        goal_count[goal] = goal_count.get(goal, 0) + 1
    return {
        "count": len(records),
        "avg_kcal": round(sum(kcal_list) / len(kcal_list), 1),
        "max_kcal": round(max(kcal_list), 1),
        "min_kcal": round(min(kcal_list), 1),
        "risk_count": risk_count,
        "goal_count": goal_count,
    }
