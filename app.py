"""食堂窗口 AI 观察与营养分析助手 —— 最终项目主程序（Gradio）

系统流程：
    输入（图片 或 文字描述 + 健康目标）
      → LLM 结构化观察（视觉通道 / 文本通道，二选一）
      → Pydantic 校验（失败自动修正重试）
      → 营养计算 + 风险阈值复核（程序动作）
      → 数字资产落盘（meal_log.jsonl）+ 网页展示
      → 历史记录与统计（第二个标签页）

运行：
    python -m pip install -r requirements.txt
    # 复制 .env.example 为 .env，按需填写 VISION_* 或 TEXT_* 模型配置
    python app.py
    浏览器访问 http://127.0.0.1:7860
"""
import json
import os

import gradio as gr
from dotenv import load_dotenv

from core import llm, nutrition, storage
from core.schema import MealObservation

load_dotenv()

CATEGORY_ZH = {
    "staple": "主食", "meat": "荤菜", "vegetable": "素菜", "soup": "汤羹",
    "dessert": "甜点", "drink": "饮品", "other": "其他",
}
RISK_ZH = {"low": "低", "medium": "中", "high": "高"}
GOAL_OPTIONS = {
    "无特定目标": None,
    "减脂": "fat_loss",
    "增肌": "muscle_gain",
    "控糖": "sugar_control",
    "均衡": "balanced",
}

DEMO_OBS = MealObservation.model_validate({
    "scene": "meal_tray",
    "dishes": [
        {"name": "红烧肉", "category": "meat", "estimated_weight_g": 150, "cooking_method": "红烧"},
        {"name": "清炒油麦菜", "category": "vegetable", "estimated_weight_g": 150, "cooking_method": "清炒"},
        {"name": "米饭", "category": "staple", "estimated_weight_g": 200, "cooking_method": "蒸"},
    ],
    "total_estimated_kcal": 948,
    "user_goal": "fat_loss",
    "diet_risk": "high",
    "evidence": ["画面中出现红烧肉", "绿色蔬菜为清炒油麦菜", "主食为白米饭"],
    "confidence": 0.88,
    "summary": "红烧肉 + 清炒油麦菜 + 米饭，脂肪偏高的减脂期午餐。",
    "missing_info": None,
})


def render_result(obs: MealObservation, nutrition: dict, risk: str, advice: str,
                  goal_label: str, demo: bool) -> str:
    lines = []
    if demo:
        lines.append("> ⚠️ **演示模式**：以下为内置示例数据，未调用真实模型。配置 `.env` 后自动切换为真实调用。\n")

    lines.append(f"### 观察摘要\n{obs.summary}")
    lines.append(f"- 场景类别：`{obs.scene}`　|　模型置信度：`{obs.confidence:.2f}`")
    lines.append(f"- 健康目标：**{goal_label}**　|　风险等级：**{RISK_ZH.get(risk, risk)}**")

    if obs.evidence:
        lines.append("- 判断证据：")
        lines.extend(f"  - {e}" for e in obs.evidence)

    lines.append("\n### 识别到的菜品")
    lines.append("| 菜品 | 类别 | 估重(g) | 热量(kcal) | 蛋白(g) | 脂肪(g) | 碳水(g) |")
    lines.append("|---|---|---|---|---|---|---|")
    for item in nutrition["detail"]:
        lines.append(f"| {item['name']} | {CATEGORY_ZH.get(item['category'], item['category'])} "
                     f"| {item['weight_g']} | {item['kcal']} | {item['protein']} | {item['fat']} | {item['carb']} |")
    t = nutrition["total"]
    lines.append(f"| **合计** | — | — | **{t['kcal']}** | **{t['protein']}** | **{t['fat']}** | **{t['carb']}** |")

    lines.append("\n### 营养与风险提示")
    lines.append(f"- 整餐约 **{t['kcal']} kcal**，蛋白质 {t['protein']}g / 脂肪 {t['fat']}g / 碳水 {t['carb']}g。")
    if risk == "high":
        lines.append(f"- 🟥 **高风险**：该餐与「{goal_label}」目标冲突较明显。")
    elif risk == "medium":
        lines.append(f"- 🟧 **中风险**：部分指标偏离「{goal_label}」目标，建议微调。")
    else:
        lines.append(f"- 🟩 **低风险**：整体符合或接近「{goal_label}」目标。")
    lines.append(f"- 建议：{advice}")

    if obs.missing_info:
        lines.append(f"\n> 💬 信息补充：{obs.missing_info}")
    return "\n".join(lines)


def analyze(image_path: str | None, text_desc: str, goal_label: str, use_demo: bool):
    goal = GOAL_OPTIONS.get(goal_label)
    goal_label = goal_label or "无特定目标"

    # 判断是否有任何已配置的模型通道
    has_vision = bool(llm.VISION_API_URL and llm.VISION_API_KEY and llm.VISION_MODEL)
    has_text = bool(llm.TEXT_API_KEY)

    demo = use_demo or not (has_vision or has_text)
    if demo:
        obs, demo_flag = DEMO_OBS, True
    else:
        demo_flag = False
        try:
            if image_path:
                obs = llm.observe_from_image(image_path, text_desc, goal)
            elif text_desc and text_desc.strip():
                obs = llm.observe_from_text(text_desc, goal)
            else:
                return "请上传图片，或输入一段文字描述。", ""
        except Exception as exc:
            return f"❌ 分析失败：{exc}\n\n请检查模型配置（`.env`）或网络。", ""

    # 结构化结果驱动后续程序动作
    nut = nutrition.compute_nutrition(obs)
    risk, advice = nutrition.decide_risk(goal, obs.diet_risk, nut)
    storage.save_record(obs, nut, risk, is_demo=demo_flag)

    markdown = render_result(obs, nut, risk, advice, goal_label, demo_flag)
    raw_json = json.dumps(obs.model_dump(), ensure_ascii=False, indent=2)
    return markdown, raw_json


def render_history(_=None) -> str:
    records = storage.load_records()
    if not records:
        return "暂无记录。去「分析一餐」页完成一次分析后，这里会显示历史记录。"
    stats = storage.summarize(records)
    lines = [f"### 历史统计（最近 {stats['count']} 条）",
             f"- 记录数：**{stats['count']}**　|　平均热量：**{stats['avg_kcal']} kcal**"
             f"　|　最高 {stats['max_kcal']} / 最低 {stats['min_kcal']} kcal",
             f"- 风险分布：低 {stats['risk_count'].get('low', 0)} · "
             f"中 {stats['risk_count'].get('medium', 0)} · 高 {stats['risk_count'].get('high', 0)}",
             "\n### 记录明细",
             "| 时间 | 摘要 | 热量(kcal) | 目标 | 风险 |", "|---|---|---|---|---|"]
    for r in records:
        goal = r.get("user_goal") or "未设定"
        lines.append(f"| {r['ts']} | {r['summary']} | {r['nutrition']['kcal']} | {goal} "
                     f"| {RISK_ZH.get(r['diet_risk'], r['diet_risk'])} |")
    return "\n".join(lines)


with gr.Blocks(title="食堂窗口 AI 观察与营养分析助手") as app:
    gr.Markdown(
        "# 🍱 食堂窗口 AI 观察与营养分析助手\n"
        "上传食堂窗口/餐盘照片**或**输入一段文字描述，模型先转成结构化菜品清单，"
        "程序再自动计算营养、判定风险并写入饮食日志。"
    )

    with gr.Tab("📷 分析一餐"):
        with gr.Row():
            with gr.Column(scale=1):
                image = gr.Image(type="filepath", label="① 上传照片（可选）")
                text_desc = gr.Textbox(
                    label="② 或用文字描述这一餐（可选，有照片时作为补充问题）",
                    placeholder="例如：三食堂打了红烧肉、清炒油麦菜和一碗米饭，最近在减脂。",
                    lines=3,
                )
                goal = gr.Dropdown(choices=list(GOAL_OPTIONS.keys()), value="减脂",
                                   label="③ 健康目标（可选）")
                use_demo = gr.Checkbox(label="使用演示数据（未配置 API 时自动生效）", value=False)
                button = gr.Button("开始分析", variant="primary")
            with gr.Column(scale=1):
                result_md = gr.Markdown(label="分析结果")
                with gr.Accordion("查看模型原始 JSON（结构化输出）", open=False):
                    raw_json = gr.Code(label="原始 JSON", language="json")

        button.click(analyze, inputs=[image, text_desc, goal, use_demo],
                     outputs=[result_md, raw_json])

    with gr.Tab("📈 历史记录"):
        refresh = gr.Button("刷新历史", variant="secondary")
        history_md = gr.Markdown()
        refresh.click(render_history, outputs=history_md)

if __name__ == "__main__":
    app.launch(server_name="127.0.0.1", server_port=int(os.getenv("PORT", "7860")))
