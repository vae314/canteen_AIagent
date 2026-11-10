"""LLM 调用模块：视觉通道（图像理解）+ 文本通道（文字描述），统一走 OpenAI 兼容 /chat/completions 协议。

- 视觉通道：把图片以 base64 data URL 放进 image_url 内容块，交给视觉大模型。
- 文本通道：把用户对一餐的文字描述交给文本大模型（如 DeepSeek）。
- 两个通道共用同一套 SYSTEM_PROMPT，都要求模型输出结构化 JSON；
  解析/校验失败时带报错信息做一次修正重试（最多 MAX_ATTEMPTS 次）。
"""
import base64
import json
import os
import re
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

from .schema import MealObservation

load_dotenv()

# ---- 模型配置（从环境变量读取） ----
VISION_API_URL = os.getenv("VISION_API_URL", "").strip()
VISION_API_KEY = os.getenv("VISION_API_KEY", "").strip()
VISION_MODEL = os.getenv("VISION_MODEL", "").strip()

TEXT_API_URL = os.getenv("TEXT_API_URL", "https://api.deepseek.com/chat/completions").strip()
TEXT_API_KEY = os.getenv("TEXT_API_KEY", "").strip()
TEXT_MODEL = os.getenv("TEXT_MODEL", "deepseek-chat").strip()

MAX_ATTEMPTS = 2

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}
MIME_MAP = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}

SYSTEM_PROMPT = (
    "你是一个食堂就餐场景的结构化观察助手（AI Observer）。"
    "请根据用户提供的照片或文字描述，输出一个 JSON 对象（不要任何多余文字或代码围栏）。"
    "JSON 必须包含以下字段：\n"
    '  scene: "canteen_window" / "meal_tray" / "dish_closeup" / "text_only" 之一；\n'
    "  dishes: 数组，每项含 name(菜名)、category(staple/meat/vegetable/soup/dessert/drink/other)、"
    "estimated_weight_g(整数克数，无法判断时给常见份量保守估值)、cooking_method(可空)；\n"
    "  total_estimated_kcal: 0~5000 的数值；\n"
    '  diet_risk: "low" / "medium" / "high"；\n'
    "  evidence: 字符串数组，说明判断所依据的可见内容；\n"
    "  confidence: 0~1 之间的数值；\n"
    "  summary: 30 字以内的一句话摘要；\n"
    "  missing_info: 信息不足时写清缺少什么，充足时填 null。"
)


def _goal_hint(goal: Optional[str]) -> str:
    return f"用户健康目标：{goal}" if goal else "用户未提供健康目标。"


def _build_user_text(question: str, goal: Optional[str]) -> str:
    return (
        f"{_goal_hint(goal)}\n"
        f"用户描述/问题：{question or '请识别菜品、估算分量并给出营养与风险分析。'}\n"
        "请只输出一个 JSON 对象。"
    )


def _post_chat(api_url: str, api_key: str, model: str, messages: list[dict]) -> str:
    """发起一次 OpenAI 兼容的 chat.completions 请求，返回模型文本。"""
    response = requests.post(
        api_url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "messages": messages, "max_tokens": 800, "temperature": 0.2},
        timeout=90,
    )
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    if isinstance(content, list):
        return "\n".join(str(x.get("text", "")) for x in content if isinstance(x, dict))
    return str(content)


def extract_json(text: str) -> dict:
    """容错解析：剥掉 Markdown 围栏，截取第一个 { 到最后一个 }。"""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("未在模型输出中找到 JSON 对象")
    return json.loads(text[start:end + 1])


def _image_to_data_url(image_path: str) -> tuple[str, str]:
    path = Path(image_path)
    if path.suffix.lower() not in ALLOWED_EXT:
        raise ValueError("仅支持 JPG、PNG 或 WEBP 图片")
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("图片请控制在 8 MB 以内")
    mime = MIME_MAP[path.suffix.lower()]
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return mime, b64


def _repair_prompt(question: str, goal: Optional[str], bad_output: str, error: str) -> str:
    return (
        f"{_goal_hint(goal)}\n用户描述/问题：{question}\n\n"
        f"你上一次的输出没有通过结构校验，请修正后重新输出一个 JSON 对象。\n"
        f"上一次输出：\n{bad_output}\n\n校验报错：\n{error}"
    )


def observe_from_image(image_path: str, question: str, goal: Optional[str]) -> MealObservation:
    """视觉通道：图片 → 结构化 MealObservation。"""
    mime, b64 = _image_to_data_url(image_path)
    return _observe_with_retry(
        question=question,
        goal=goal,
        make_messages=lambda text: [{
            "role": "system", "content": SYSTEM_PROMPT,
        }, {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }],
        api_url=VISION_API_URL,
        api_key=VISION_API_KEY,
        model=VISION_MODEL,
    )


def observe_from_text(text: str, goal: Optional[str]) -> MealObservation:
    """文本通道：文字描述 → 结构化 MealObservation。"""
    return _observe_with_retry(
        question=text,
        goal=goal,
        make_messages=lambda t: [{"role": "system", "content": SYSTEM_PROMPT},
                                 {"role": "user", "content": t}],
        api_url=TEXT_API_URL,
        api_key=TEXT_API_KEY,
        model=TEXT_MODEL,
    )


def _observe_with_retry(question, goal, make_messages, api_url, api_key, model) -> MealObservation:
    if not (api_url and api_key and model):
        raise RuntimeError("该通道的模型尚未配置（缺少 API 地址或密钥）")

    raw_text = ""
    last_error = ""
    for _ in range(MAX_ATTEMPTS):
        try:
            user_text = (_repair_prompt(question, goal, raw_text, last_error)
                         if last_error else _build_user_text(question, goal))
            raw_text = _post_chat(api_url, api_key, model, make_messages(user_text))
            data = extract_json(raw_text)
            return MealObservation.model_validate(data)
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError,
                json.JSONDecodeError) as exc:  # ValidationError 是 ValueError 的子类
            last_error = f"{type(exc).__name__}: {exc}"

    raise RuntimeError(f"已重试 {MAX_ATTEMPTS} 次仍失败，最后一次错误：{last_error}")
