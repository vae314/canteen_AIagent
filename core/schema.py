"""Pydantic 数据契约：把模型输出约束为可验证的结构化对象。

本模块是整个项目的"数据契约"核心，对应课程"结构化输出"主题：
- 字段数 ≥ 5
- 含 Literal 枚举约束
- 含 Optional 可选字段
- 含 Field 数值/长度约束
- extra="forbid"：模型多输出未定义字段会直接报错
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

DishCategory = Literal["staple", "meat", "vegetable", "soup", "dessert", "drink", "other"]
SceneType = Literal["canteen_window", "meal_tray", "dish_closeup", "text_only"]
GoalType = Literal["fat_loss", "muscle_gain", "sugar_control", "balanced"]
RiskLevel = Literal["low", "medium", "high"]


class DishItem(BaseModel):
    """单个菜品的观察结果。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=20, description="菜品名称")
    category: DishCategory = Field(description="菜品类别，只能是枚举之一")
    estimated_weight_g: int = Field(ge=0, le=2000, description="估算分量（克）")
    cooking_method: Optional[str] = Field(
        default=None, max_length=10, description="烹饪方式，无法判断时为 null"
    )

    @field_validator("estimated_weight_g", mode="before")
    @classmethod
    def _weight_default(cls, v):
        """模型偶尔返回 null：给常见份量的保守估值，避免整条链路因一个空值崩溃。"""
        if v is None or v == "":
            return 150
        return v


class MealObservation(BaseModel):
    """一次就餐的结构化观察结果，是本项目的核心数据契约。"""

    model_config = ConfigDict(extra="forbid")

    scene: SceneType = Field(description="观察场景类型（窗口/餐盘/特写/纯文本）")
    dishes: list[DishItem] = Field(min_length=1, max_length=10, description="识别出的菜品列表")
    total_estimated_kcal: float = Field(ge=0, le=5000, description="整餐估算总热量（千卡）")
    user_goal: Optional[GoalType] = Field(default=None, description="用户健康目标，未提及时为 null")
    diet_risk: RiskLevel = Field(description="与用户目标的冲突等级，驱动是否告警")
    evidence: list[str] = Field(min_length=1, description="支持判断的证据片段（来自输入）")
    confidence: float = Field(ge=0.0, le=1.0, description="模型自评置信度 0~1")
    summary: str = Field(min_length=5, max_length=200, description="一句话观察摘要")
    missing_info: Optional[str] = Field(default=None, max_length=200, description="信息不足时说明缺什么")

    @field_validator("total_estimated_kcal", mode="before")
    @classmethod
    def _kcal_default(cls, v):
        """整餐热量缺省时给 0，后续营养计算会按菜品反算覆盖，不依赖模型这个字段。"""
        if v is None or v == "":
            return 0.0
        return v

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence_default(cls, v):
        if v is None or v == "":
            return 0.5
        return v

    @field_validator("evidence", mode="before")
    @classmethod
    def _evidence_default(cls, v):
        """证据为空时补一条说明，保证 min_length 满足且结果诚实。"""
        if not v:
            return ["（模型未提供明确证据）"]
        return v
