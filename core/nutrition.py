"""营养计算与风险判定：结构化结果真正"驱动"的后续程序动作。

这里体现"结构化输出"的价值：一旦拿到 MealObservation（菜名 + 估重 + 目标），
程序就能查营养库、算整餐营养、按阈值复核风险等级，完全不再依赖自然语言。
"""
from typing import Optional

from .schema import MealObservation

# 每 100g 的热量与三大营养素（简化示例库，可按需扩充/替换为真实数据库）
NUTRITION_TABLE = {
    "米饭":     {"kcal": 116, "protein": 2.6, "fat": 0.3,  "carb": 25.9},
    "番茄炒蛋": {"kcal": 95,  "protein": 5.0, "fat": 6.0,  "carb": 5.0},
    "红烧肉":   {"kcal": 472, "protein": 9.5, "fat": 45.0, "carb": 5.0},
    "清炒油麦菜": {"kcal": 45,  "protein": 1.9, "fat": 3.0,  "carb": 3.5},
    "糖醋里脊": {"kcal": 280, "protein": 14.0, "fat": 15.0, "carb": 22.0},
    "干锅花菜": {"kcal": 110, "protein": 3.5, "fat": 8.0,  "carb": 6.0},
    "紫薯粥":   {"kcal": 60,  "protein": 1.2, "fat": 0.3,  "carb": 13.0},
    "炸鸡腿":   {"kcal": 279, "protein": 18.0, "fat": 18.0, "carb": 8.0},
    "面条":     {"kcal": 140, "protein": 4.5, "fat": 1.5,  "carb": 28.0},
    "鸡蛋":     {"kcal": 144, "protein": 13.0, "fat": 9.5, "carb": 1.5},
    "鸡胸肉":   {"kcal": 165, "protein": 31.0, "fat": 3.6, "carb": 0.0},
}
UNKNOWN_NUTRITION = {"kcal": 150, "protein": 5.0, "fat": 6.0, "carb": 15.0}

GOAL_RULES = {
    "fat_loss":      {"label": "减脂", "max_kcal": 700, "max_fat_g": 25,
                      "advice": "优先清炒/白灼类菜品，红烧与油炸类减半"},
    "muscle_gain":   {"label": "增肌", "min_protein_g": 25,
                      "advice": "可增加鸡胸、鱼、蛋类，主食保持足量"},
    "sugar_control": {"label": "控糖", "max_carb_g": 80,
                      "advice": "避免糖醋、勾芡与粥类，主食换成杂粮并减量"},
    "balanced":      {"label": "均衡", "advice": "保持一荤一素一主食的结构即可"},
}


def lookup_nutrition(name: str) -> dict:
    if name in NUTRITION_TABLE:
        return NUTRITION_TABLE[name]
    for key, value in NUTRITION_TABLE.items():
        if key in name or name in key:
            return value
    return UNKNOWN_NUTRITION


def compute_nutrition(obs: MealObservation) -> dict:
    """把结构化对象换算成整餐营养数据。"""
    totals = {"kcal": 0.0, "protein": 0.0, "fat": 0.0, "carb": 0.0}
    detail = []
    for dish in obs.dishes:
        per100 = lookup_nutrition(dish.name)
        ratio = dish.estimated_weight_g / 100.0
        item = {k: round(v * ratio, 1) for k, v in per100.items()}
        detail.append({"name": dish.name, "category": dish.category,
                       "weight_g": dish.estimated_weight_g, **item})
        for k in totals:
            totals[k] += item[k]
    totals = {k: round(v, 1) for k, v in totals.items()}
    return {"total": totals, "detail": detail}


def decide_risk(goal: Optional[str], model_risk: str, nutrition: dict) -> tuple[str, str]:
    """用阈值复核模型给出的 diet_risk，返回 (最终风险, 建议)。"""
    rule = GOAL_RULES.get(goal) if goal else None
    risk = model_risk
    if rule:
        t = nutrition["total"]
        if "max_kcal" in rule and t["kcal"] > rule["max_kcal"]:
            risk = "high"
        if "max_fat_g" in rule and t["fat"] > rule["max_fat_g"]:
            risk = "high"
        if "max_carb_g" in rule and t["carb"] > rule["max_carb_g"]:
            risk = "high"
        if "min_protein_g" in rule and t["protein"] < rule["min_protein_g"] and risk == "low":
            risk = "medium"
    advice = rule["advice"] if rule else "未设定目标，仅做记录。"
    return risk, advice
