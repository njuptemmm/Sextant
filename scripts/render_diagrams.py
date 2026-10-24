#!/usr/bin/env python3
"""Generate the diagrams used in the hackathon submission.

The script writes standalone SVG files under ``docs/diagrams/``.  It only uses
the standard library, so the diagrams can be regenerated on any machine:

    python scripts/render_diagrams.py

PNG versions are produced separately with a headless browser (see
``docs/diagrams/README.md``), because keeping rasterisation out of this script
avoids a Pillow/Cairo dependency.
"""

from __future__ import annotations

import html
from pathlib import Path

# ---------------------------------------------------------------------------
# palette / primitives
# ---------------------------------------------------------------------------

BG = "#F5F7FB"
WHITE = "#FFFFFF"
INK = "#0F172A"
MUTED = "#64748B"
LINE = "#CBD5E1"
GRID = "#E2E8F0"

BLUE = "#2563EB"
BLUE_BG = "#EFF6FF"
TEAL = "#0D9488"
TEAL_BG = "#F0FDFA"
AMBER = "#D97706"
AMBER_BG = "#FFFBEB"
RED = "#DC2626"
RED_BG = "#FEF2F2"
PURPLE = "#7C3AED"
PURPLE_BG = "#F5F3FF"
SLATE_BG = "#F1F5F9"

FONT = '"Microsoft YaHei", "Segoe UI", "Noto Sans SC", sans-serif'
MONO = '"Cascadia Mono", "Consolas", "SFMono-Regular", monospace'

MARKER = {
    MUTED: "arrow-gray",
    BLUE: "arrow-blue",
    TEAL: "arrow-teal",
    AMBER: "arrow-amber",
    RED: "arrow-red",
    PURPLE: "arrow-purple",
}


def esc(value: str) -> str:
    return html.escape(value, quote=True)


def svg_doc(width: int, height: int, body: str) -> str:
    markers = "\n".join(
        f'<marker id="{name}" viewBox="0 0 10 10" refX="9" refY="5" '
        f'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M0,0 L10,5 L0,10 z" fill="{color}"/></marker>'
        for color, name in MARKER.items()
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">
<defs>
<filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">
  <feDropShadow dx="0" dy="4" stdDeviation="9" flood-color="#0F172A" flood-opacity="0.07"/>
</filter>
<filter id="soft" x="-20%" y="-20%" width="140%" height="140%">
  <feDropShadow dx="0" dy="2" stdDeviation="4" flood-color="#0F172A" flood-opacity="0.05"/>
</filter>
{markers}
</defs>
<rect width="{width}" height="{height}" fill="{BG}"/>
{body}
</svg>
'''


def text(x: float, y: float, value: str, size: float = 13, weight: int = 400,
         fill: str = INK, anchor: str = "start", mono: bool = False,
         opacity: float = 1.0) -> str:
    family = MONO if mono else FONT
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{esc(family)}" '
            f'font-size="{size}" font-weight="{weight}" fill="{fill}" '
            f'text-anchor="{anchor}" opacity="{opacity}">{esc(value)}</text>')


def lines(x: float, y: float, values: list[str], size: float = 13, leading: float = 19,
          fill: str = MUTED, weight: int = 400, mono: bool = False) -> str:
    return "".join(text(x, y + i * leading, value, size, weight, fill, mono=mono)
                   for i, value in enumerate(values))


def chip(x: float, y: float, value: str, fill: str = WHITE, stroke: str = LINE,
         color: str = MUTED, size: float = 11.5) -> str:
    width = max(46, 12.2 * len(value) + 18)
    return (f'<rect x="{x - width / 2:.1f}" y="{y - 13}" width="{width:.1f}" height="26" rx="13" '
            f'fill="{fill}" stroke="{stroke}"/>' + text(x, y + 4, value, size, 600, color, "middle"))


def panel(x: float, y: float, w: float, h: float, label: str, color: str = MUTED,
          label_x: float | None = None) -> str:
    lx = x + 18 if label_x is None else label_x
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="20" fill="none" '
            f'stroke="{color}" stroke-opacity="0.38" stroke-width="1.4" stroke-dasharray="7 6"/>'
            + f'<rect x="{lx}" y="{y - 12}" width="{max(70, 12.5 * len(label) + 20)}" height="24" rx="12" fill="{BG}"/>'
            + text(lx + 10, y + 4, label, 11.5, 700, color))


def card(x: float, y: float, w: float, h: float, title: str, body: list[str] = (),
         accent: str = BLUE, fill: str = WHITE, title_size: float = 16,
         body_size: float = 12.5, mono: bool = False, num: int | None = None,
         shadow: bool = True) -> str:
    shadow_attr = ' filter="url(#shadow)"' if shadow else ""
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{fill}" '
           f'stroke="{GRID}" stroke-width="1.2"{shadow_attr}/>',
           f'<rect x="{x}" y="{y}" width="{w}" height="5" rx="2.5" fill="{accent}"/>']
    tx = x + 18
    if num is not None:
        out.append(f'<circle cx="{x + 25}" cy="{y + 30}" r="14" fill="{accent}" opacity="0.12"/>')
        out.append(text(x + 25, y + 35, str(num), 12, 700, accent, "middle"))
        tx = x + 47
    out.append(text(tx, y + 35, title, title_size, 700, INK, mono=mono))
    if body:
        out.append(lines(x + 18, y + 59, list(body), body_size, 18.5, MUTED, mono=mono))
    return "".join(out)


def arrow(x1: float, y1: float, x2: float, y2: float, color: str = MUTED,
          dash: str | None = None, label: str | None = None,
          lx: float | None = None, ly: float | None = None, width: float = 2.0) -> str:
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    out = (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{width}" '
           f'stroke-linecap="round" marker-end="url(#{MARKER[color]})"{dash_attr}/>')
    if label:
        out += chip(lx if lx is not None else (x1 + x2) / 2,
                    ly if ly is not None else (y1 + y2) / 2 - 8,
                    label, BG, LINE, color, 11)
    return out


def path_arrow(d: str, color: str = MUTED, dash: str | None = None,
               label: str | None = None, lx: float | None = None,
               ly: float | None = None, width: float = 2.0) -> str:
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    out = (f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}" '
           f'stroke-linecap="round" stroke-linejoin="round" '
           f'marker-end="url(#{MARKER[color]})"{dash_attr}/>')
    if label:
        out += chip(lx or 0, ly or 0, label, BG, LINE, color, 11)
    return out


def title_block(title: str, subtitle: str, legend: list[tuple[str, str]] | None = None,
                width: int = 1500) -> str:
    out = [text(60, 62, title, 30, 700, INK),
           text(60, 92, subtitle, 14.5, 400, MUTED)]
    if legend:
        x = width - 60
        for label, color in reversed(legend):
            w = 12.2 * len(label) + 30
            x -= w
            out.append(f'<rect x="{x}" y="42" width="{w}" height="28" rx="14" fill="{WHITE}" stroke="{GRID}"/>')
            out.append(f'<circle cx="{x + 15}" cy="56" r="4.5" fill="{color}"/>')
            out.append(text(x + 27, 60, label, 11.5, 600, MUTED))
            x -= 10
    return "".join(out)


def section_label(x: float, y: float, value: str, color: str = MUTED) -> str:
    return text(x, y, value, 11.5, 700, color)


# ---------------------------------------------------------------------------
# 01 architecture
# ---------------------------------------------------------------------------

def diagram_architecture() -> str:
    out = [title_block(
        "Sextant 系统架构",
        "一套内核，两个入口；证据目录是主产物，调研报告是按需启动的独立任务",
        [("同步请求", BLUE), ("后台任务", AMBER), ("数据 / 产物", TEAL)],
    )]
    # entry layer
    out.append(card(60, 140, 260, 90, "Web 控制台", ["主题创建 / 实时进度", "目录筛选 / 报告入口"], BLUE))
    out.append(card(860, 140, 260, 90, "MCP Agent", ["run_topic / generate_report", "get_run_status"], PURPLE))

    # API process
    out.append(panel(60, 270, 840, 330, "API 进程 · FastAPI :8000", BLUE))
    out.append(card(90, 320, 210, 80, "REST 路由", ["Pydantic 校验", "202 / 4xx"], BLUE))
    out.append(card(320, 320, 220, 80, "JobRegistry", ["按主题互斥", "pipeline / report"], AMBER))
    out.append(card(90, 430, 210, 70, "SSE 事件流", ["seq 去重 / 断线重连"], TEAL, body_size=11.5))
    out.append(card(320, 430, 220, 70, "RunLogger", ["JSONL + EventBus"], TEAL, body_size=11.5))
    out.append(card(560, 320, 310, 190, "报告任务（独立）", [
        "独立 run_id / 日志 / SSE",
        "分析 → 综合 → 渲染",
        "生成期间目录仍可访问",
        "失败保留旧报告；重复启动 409",
        "触发：POST /topics/{id}/report",
        "产物：output/ 下的报告 HTML",
    ], AMBER, body_size=12))

    # MCP process
    out.append(panel(920, 270, 200, 330, "MCP 进程 :8001", PURPLE))
    out.append(card(940, 320, 160, 190, "MCP Server", [
        "run_topic",
        "generate_report",
        "get_run_status",
        "同一套 pipeline",
    ], PURPLE, title_size=14, body_size=10.5, mono=True))

    # pipeline
    out.append(panel(60, 640, 1050, 220, "检索链路 · 6 段顺序执行", BLUE, label_x=740))
    stages = [
        ("plan", "查询规划", "4 来源 / 6 查询"),
        ("retrieve", "多源检索", "并行 + 限流"),
        ("normalize", "去重归一", "DOI / 标题指纹"),
        ("enrich", "中文增强", "标题 / 摘要 / 归类"),
        ("rank", "相关性排序", "6 项加权评分"),
        ("render", "目录生成", "自包含 HTML"),
    ]
    for i, (stage, name, body) in enumerate(stages):
        x = 90 + i * 162
        out.append(card(x, 695, 150, 110, name, [stage, body], BLUE if i < 4 else TEAL,
                        title_size=14, body_size=10.8))
        if i < len(stages) - 1:
            out.append(arrow(x + 150, 750, x + 162, 750, BLUE, width=1.6))

    # workspace
    out.append(panel(1160, 270, 280, 590, "主题工作区 · 磁盘", TEAL))
    out.append(f'<rect x="1186" y="310" width="228" height="470" rx="12" fill="{WHITE}" stroke="{GRID}"/>')
    tree = [
        ("workspace/<topic>/", INK, 700),
        ("01_queries/", MUTED, 400),
        ("  queries.json", MUTED, 400),
        ("02_raw/search/*.jsonl", MUTED, 400),
        ("03_processed/", MUTED, 400),
        ("  ranked_articles.jsonl", MUTED, 400),
        ("04_summary/*.json", MUTED, 400),
        ("output/catalog.html", TEAL, 600),
        ("output/", TEAL, 600),
        ("  综合调研报告：*.html", AMBER, 600),
        ("logs/<run_id>.jsonl", MUTED, 400),
        ("runs.json", MUTED, 400),
    ]
    for i, (value, color, weight) in enumerate(tree):
        out.append(text(1200, 342 + i * 36, value, 10.6, weight, color, mono=True))
    out.append(text(1186, 806, "阶段只依赖文件，可重入；", 11, 400, MUTED))
    out.append(text(1186, 824, "进程崩溃不丢已完成结果。", 11, 400, MUTED))

    # external dependencies
    out.append(panel(60, 900, 1060, 150, "外部依赖", PURPLE))
    out.append(card(90, 935, 620, 90, "学术元数据源", [
        "OpenAlex · arXiv · Crossref · DOAJ",
        "生物医学主题自动追加 PubMed / Europe PMC",
    ], PURPLE, body_size=12))
    out.append(card(740, 935, 350, 90, "LLM（可选）", [
        "OpenAI 兼容 /chat/completions",
        "enrich 与 report 使用",
    ], PURPLE, body_size=12))
    out.append(card(1160, 900, 280, 150, "设计原则", [
        "文件即状态，阶段可重入",
        "单源失败不拖垮整次运行",
        "报告不阻塞目录交付",
    ], TEAL, title_size=14, body_size=11.5))

    # arrows
    out.append(path_arrow("M 190 230 L 190 290 L 195 290 L 195 320", BLUE, label="HTTP", lx=255, ly=301))
    out.append(path_arrow("M 990 230 L 990 250 L 930 250 L 930 400 L 940 400", PURPLE, label="MCP", lx=960, ly=238))
    out.append(arrow(300, 360, 320, 360, BLUE))
    out.append(path_arrow("M 390 400 L 390 620 L 165 620 L 165 695", BLUE, label="启动检索", lx=285, ly=607))
    out.append(path_arrow("M 500 400 L 500 430 L 560 430", AMBER, label="启动报告", lx=520, ly=416))
    out.append(path_arrow("M 715 510 L 715 620 L 1140 620 L 1140 700 L 1160 700", AMBER,
                          label="报告", lx=1100, ly=684))
    out.append(arrow(1050, 750, 1160, 750, TEAL, label="产物", lx=1105, ly=735))
    out.append(arrow(1020, 600, 1020, 695, PURPLE, dash="5 5", label="同一套 pipeline", lx=1020, ly=648))
    out.append(path_arrow("M 327 805 L 327 935", PURPLE, label="检索", lx=327, ly=870))
    out.append(path_arrow("M 700 805 L 700 870 L 800 870 L 800 935", PURPLE, label="enrich / report", lx=850, ly=880))
    out.append(text(90, 590, "JobRegistry 只登记任务，不保存业务状态", 11, 400, MUTED))
    return svg_doc(1500, 1080, "".join(out))


# ---------------------------------------------------------------------------
# 02 agent flow
# ---------------------------------------------------------------------------

def diagram_agent_flow() -> str:
    out = [title_block(
        "Agent 执行流程",
        "六段检索链路 + 一个按需报告任务；每段都有明确输入、产物和失败边界",
        [("正常路径", BLUE), ("降级 / 恢复", AMBER), ("独立任务", PURPLE)],
    )]
    rows = [
        ("plan", "查询规划", "选择来源；生成每源检索式；模型可用时扩充同义词", "01_queries/queries.json", "模型失败 → 退回规则检索式"),
        ("retrieve", "多源检索", "每个来源一个线程并行；同源查询串行以尊重限流", "02_raw/search/<source>.jsonl", "单源失败记 warning，其余继续；全失败才终止"),
        ("normalize", "去重归一", "DOI / 标题指纹去重；合并多源命中与摘要", "pending_verification_articles.jsonl", "摘要不合格 → 待核实区，不参与排序"),
        ("enrich", "中文增强", "中文标题、中文摘要、一句话结论、研究方向（可选）", "记录字段 + 批次 notes", "单批失败只丢该批；全部失败退回规则模式"),
        ("rank", "相关性排序", "关键词 40 + 标题 10 + 引用 20 + 时效 20 + 跨源 5 + 质量 5", "03_processed/ranked_articles.jsonl", "纯函数，无外部依赖"),
        ("render", "目录生成", "分组、筛选、中英对照，写入自包含 HTML", "output/catalog.html", "中文质量门控失败 → 降级渲染并告警"),
    ]
    y = 135
    for i, (stage, name, action, artifact, fallback) in enumerate(rows, 1):
        out.append(card(60, y, 760, 100, f"{name}", [
            f"{stage} · {action}",
        ], BLUE if i <= 3 else TEAL, num=i, title_size=16, body_size=12))
        out.append(f'<rect x="850" y="{y}" width="590" height="100" rx="12" fill="{WHITE}" stroke="{GRID}"/>')
        out.append(text(870, y + 28, "产物", 11, 700, TEAL))
        out.append(text(870, y + 50, artifact, 11.5, 400, INK, mono=True))
        out.append(text(870, y + 78, "失败处理", 11, 700, AMBER))
        out.append(text(945, y + 78, fallback, 11.5, 400, MUTED))
        if i < len(rows):
            out.append(arrow(440, y + 100, 440, y + 118, BLUE))
        y += 118

    # report branch
    out.append(path_arrow("M 440 825 L 440 845 L 285 845 L 285 895", PURPLE, dash="5 5"))
    out.append(chip(430, 837, "目录就绪后，用户点击「生成调研报告」", BG, LINE, PURPLE, 11))
    out.append(panel(60, 860, 1380, 210, "报告任务（独立）", PURPLE))
    out.append(card(90, 895, 390, 145, "分析 20 篇证据", ["每批 5 篇，提取研究影响", "单批失败只丢该批"], PURPLE, num=7))
    out.append(card(520, 895, 390, 145, "综合写作", ["六个分析小节 + 论文卡片", "截断 → 预算翻倍重试"], PURPLE, num=8))
    out.append(card(950, 895, 450, 145, "渲染与校验", ["每张卡必须归属子方向", "失败保留旧报告"], PURPLE, num=9))
    out.append(arrow(480, 967, 520, 967, PURPLE))
    out.append(arrow(910, 967, 950, 967, PURPLE))
    out.append(text(90, 1055, "报告有自己的 run_id、日志和 SSE 流，不写入 runs.json；目录在报告生成期间始终可访问。", 12, 400, MUTED))
    return svg_doc(1500, 1120, "".join(out))


# ---------------------------------------------------------------------------
# 03 report sequence
# ---------------------------------------------------------------------------

def diagram_report_sequence() -> str:
    out = [title_block(
        "调研报告：独立任务时序",
        "报告由按钮触发；有自己的 run_id、日志和 SSE 流，失败不删除目录或旧报告",
        [("调用", BLUE), ("事件", TEAL), ("失败 / 冲突", RED)],
    )]
    lanes = [
        (130, "用户 / 浏览器", BLUE),
        (430, "API 路由", BLUE),
        (710, "JobRegistry", AMBER),
        (990, "报告任务", PURPLE),
        (1270, "LLM", PURPLE),
    ]
    for x, label, color in lanes:
        out.append(f'<rect x="{x - 95}" y="125" width="190" height="52" rx="12" fill="{WHITE}" stroke="{GRID}" filter="url(#soft)"/>')
        out.append(text(x, 157, label, 13, 700, color, "middle"))
        out.append(f'<line x1="{x}" y1="177" x2="{x}" y2="870" stroke="{LINE}" stroke-width="1.4" stroke-dasharray="5 5"/>')

    msgs = [
        (210, 130, 430, "POST /topics/{id}/report", BLUE, None),
        (262, 430, 710, "start_report(topic)", BLUE, None),
        (314, 710, 990, "创建 job，分配 run_id", AMBER, None),
        (366, 430, 130, "202 {job: running}", BLUE, "返回"),
        (418, 990, 1270, "分析 20 篇（4 批 × 5）", PURPLE, None),
        (470, 1270, 990, "impacts JSON", PURPLE, None),
        (522, 990, 1270, "综合写作（max_tokens 16000）", PURPLE, None),
        (574, 1270, 990, "sections + cards", PURPLE, None),
        (626, 990, 430, "report/completed", TEAL, "写日志"),
        (678, 430, 130, "SSE: report/running → completed", TEAL, "推送"),
        (730, 990, 430, "run/completed", TEAL, "结束"),
    ]
    for y, x1, x2, label, color, tag in msgs:
        out.append(arrow(x1, y, x2, y, color, label=None))
        out.append(text((x1 + x2) / 2, y - 9, label, 11.5, 600, color, "middle"))
        if tag:
            out.append(text((x1 + x2) / 2, y + 19, tag, 10.5, 400, MUTED, "middle"))

    # conflict + failure branches
    out.append(f'<rect x="450" y="760" width="520" height="115" rx="12" fill="{RED_BG}" stroke="{RED}" stroke-opacity="0.35"/>')
    out.append(text(470, 788, "冲突：同一主题已有检索 / 报告任务", 11.5, 700, RED))
    out.append(text(470, 813, "→ 409，提示「已有运行中的任务」", 11.5, 400, MUTED))
    out.append(text(470, 838, "失败：未配置模型 / 目录不存在 / 文献不足", 11.5, 700, RED))
    out.append(text(470, 863, "→ 4xx 或 report/warning；旧报告保留", 11.5, 400, MUTED))
    out.append(path_arrow("M 710 314 L 710 755", RED, dash="5 5"))
    out.append(path_arrow("M 990 626 L 990 755", RED, dash="5 5", label="失败", lx=990, ly=700))

    out.append(text(60, 930, "截图口径：报告任务不会出现在主题的 runs.json 中；GET /api/topics/{id} 返回 active_report 供控制台恢复进度。", 12, 400, MUTED))
    return svg_doc(1500, 970, "".join(out))


# ---------------------------------------------------------------------------
# 04 state machine
# ---------------------------------------------------------------------------

def diagram_state_machine() -> str:
    out = [title_block(
        "运行状态机",
        "检索与报告是两类任务；目录随时可用，重跑会使旧报告失效",
        [("检索", BLUE), ("报告", PURPLE), ("人工动作", AMBER)],
    )]
    out.append(panel(60, 130, 650, 490, "检索运行", BLUE))
    out.append(panel(760, 130, 680, 490, "报告任务", PURPLE))

    def state(x, y, label, body, color, fill):
        return card(x, y, 220, 100, label, body, color, fill, title_size=15, body_size=11.5)

    out.append(state(100, 190, "无运行", ["主题刚创建"], MUTED, WHITE))
    out.append(state(390, 190, "检索中", ["plan → render", "SSE 实时进度"], BLUE, WHITE))
    out.append(state(390, 390, "失败", ["保留中间产物", "日志可定位阶段"], RED, RED_BG))
    out.append(state(100, 390, "目录就绪", ["catalog.html 可用", "报告可随时启动"], TEAL, TEAL_BG))

    out.append(arrow(320, 240, 390, 240, BLUE, label="创建", lx=352, ly=222))
    out.append(path_arrow("M 500 290 L 500 390", RED))
    out.append(path_arrow("M 420 290 L 420 340 L 320 340 L 320 390", TEAL, label="成功", lx=365, ly=330))
    # retry after failure: loop around the right edge
    out.append(path_arrow("M 610 440 L 650 440 L 650 240 L 610 240", AMBER, label="重跑", lx=650, ly=340))
    # rerun from a ready catalog: loop above the initial state
    out.append(path_arrow("M 100 440 L 80 440 L 80 165 L 500 165 L 500 190", AMBER,
                          label="重跑", lx=80, ly=320))

    out.append(state(1090, 190, "报告生成中", ["analyze → synthesize", "独立 SSE"], PURPLE, WHITE))
    out.append(state(1090, 390, "报告就绪", ["中英双语 HTML", "卡片归属已校验"], TEAL, TEAL_BG))
    out.append(state(800, 390, "报告失败 / 降级", ["notes 说明原因", "旧报告仍可用"], RED, RED_BG))

    # 目录就绪 -> 报告生成中（从下方绕行，经两张报告卡之间的空隙上行）
    out.append(path_arrow("M 210 490 L 210 560 L 1050 560 L 1050 170 L 1200 170 L 1200 190",
                          PURPLE, label="点击生成", lx=700, ly=547))
    out.append(path_arrow("M 1200 290 L 1200 390", PURPLE))
    out.append(path_arrow("M 1090 290 L 1090 340 L 910 340 L 910 390", RED, label="失败", lx=1000, ly=328))
    # 报告失败 -> 重试（左侧绕行）
    out.append(path_arrow("M 800 440 L 770 440 L 770 240 L 1090 240", AMBER, dash="5 5",
                          label="重新生成", lx=790, ly=340))
    # 报告就绪 -> 重新生成（右侧绕行）
    out.append(path_arrow("M 1310 440 L 1340 440 L 1340 240 L 1310 240", AMBER, dash="5 5",
                          label="重新生成", lx=1340, ly=340))

    out.append(f'<rect x="60" y="650" width="1380" height="90" rx="14" fill="{WHITE}" stroke="{GRID}"/>')
    out.append(text(85, 682, "人工介入点", 13, 700, AMBER))
    out.append(text(85, 710, "重跑检索（新 run_id）· 修改关键词新建主题 · 重新生成报告 · 直接查看 logs/<run_id>.jsonl 定位阶段", 12, 400, MUTED))
    out.append(text(60, 790, "约束：同一主题同一时刻只允许一个任务；报告任务不写入 runs.json；重跑检索会作废旧报告。", 12, 400, MUTED))
    return svg_doc(1500, 830, "".join(out))


# ---------------------------------------------------------------------------
# 05 error handling
# ---------------------------------------------------------------------------

def diagram_error_handling() -> str:
    out = [title_block(
        "异常处理：失败域与降级路径",
        "失败局部化、可解释、可恢复；任何外部依赖出问题，用户都不会只看到「出错了」",
        [("继续", TEAL), ("降级 / 告警", AMBER), ("终止 / 4xx", RED)],
    )]
    headers = [("触发", 60, 360), ("系统处理", 450, 640), ("用户看到", 1120, 320)]
    for label, x, w in headers:
        out.append(f'<rect x="{x}" y="130" width="{w}" height="44" rx="10" fill="{WHITE}" stroke="{GRID}"/>')
        out.append(text(x + 16, 158, label, 12, 700, MUTED))

    rows = [
        ("请求级", "参数非法、主题重复、路径越界", "立即返回 4xx，不启动任务；路径 resolve 后校验父目录", "明确错误码 + 字段", RED),
        ("来源级", "单个学术源超时 / 限流 / 解析失败", "该源记 warning，其余来源继续；全失败才终止", "目录仍生成，标注失败来源", AMBER),
        ("查询级", "同源内某条检索式失败", "记录后继续下一条，不影响同源其他查询", "日志中可见失败检索式", AMBER),
        ("模型级", "超时、非法 JSON、字段缺失、截断", "单批失败只丢该批（10 条）；截断拆批；全部失败退回规则模式", "目录保留英文原文，不阻断", AMBER),
        ("报告级", "未配置模型 / 文献不足 / 综合截断", "入口 4xx；截断时预算翻倍（16k→32k→64k），到顶按方法家族归组", "warning + notes；旧报告保留", RED),
        ("渲染级", "中文质量门控不通过", "降级渲染并告警，不用占位符冒充中文", "目录可用，标注降级", AMBER),
    ]
    y = 190
    for domain, trigger, handling, visible, color in rows:
        fill = RED_BG if color == RED else AMBER_BG
        out.append(f'<rect x="60" y="{y}" width="1380" height="100" rx="12" fill="{WHITE}" stroke="{GRID}"/>')
        out.append(f'<rect x="60" y="{y}" width="6" height="100" rx="3" fill="{color}"/>')
        out.append(text(82, y + 30, domain, 14, 700, color))
        out.append(text(82, y + 56, trigger, 11.5, 400, MUTED))
        out.append(text(450, y + 42, handling, 11.5, 400, INK))
        out.append(f'<rect x="1120" y="{y + 18}" width="300" height="64" rx="10" fill="{fill}"/>')
        out.append(text(1140, y + 44, visible, 11.5, 600, color))
        if domain == "报告级":
            out.append(text(450, y + 70, "失败时保留旧报告；不会删除目录或 runs.json", 11, 400, MUTED))
        y += 108

    out.append(f'<rect x="60" y="850" width="1380" height="80" rx="14" fill="{WHITE}" stroke="{GRID}"/>')
    out.append(text(85, 880, "状态恢复", 13, 700, BLUE))
    out.append(text(85, 906, "每个阶段只依赖工作区文件，可独立重入 · 控制台重跑重新执行整条链路 · 进程重启后已完成产物仍在 · 失败后先看日志再重跑", 11.5, 400, MUTED))
    return svg_doc(1500, 960, "".join(out))


# ---------------------------------------------------------------------------
# 06 observability
# ---------------------------------------------------------------------------

def diagram_observability() -> str:
    out = [title_block(
        "可观测性：从事件到证据",
        "同一份事件流同时写入 JSONL、推送到 SSE、汇总为 run_summary；排查不靠猜",
        [("事件", BLUE), ("存储", TEAL), ("查询", PURPLE)],
    )]
    out.append(card(60, 140, 300, 100, "检索链路", ["plan → retrieve → … → render", "每阶段 emit 事件"], BLUE))
    out.append(card(60, 270, 300, 100, "报告任务", ["analyze → synthesize → render", "独立 run_id"], PURPLE))

    out.append(card(440, 175, 340, 150, "RunLogger", [
        "seq / ts / stage / status",
        "duration_ms / data / message",
        "所有事件先落一条 JSONL",
    ], TEAL))

    out.append(card(860, 130, 270, 90, "JSONL 日志", ["logs/<run_id>.jsonl", "可 jq / grep"], TEAL))
    out.append(card(860, 250, 270, 90, "EventBus", ["进程内发布订阅", "按 seq 回放"], BLUE))
    out.append(card(1190, 250, 250, 90, "SSE", ["text/event-stream", "断线重连"], BLUE))
    out.append(card(1190, 130, 250, 90, "控制台", ["阶段进度 / 耗时", "失败提示"], PURPLE))
    out.append(card(860, 380, 270, 90, "run_summary.json", ["条数 / 去重 / 待核实", "阶段耗时 / 模型名"], TEAL))
    out.append(card(1190, 380, 250, 90, "runs.json", ["运行历史", "产物路径"], TEAL))
    out.append(card(1190, 500, 250, 90, "/api/health", ["版本 / 模型状态", "工作区可写"], PURPLE))

    out.append(arrow(360, 190, 440, 230, BLUE))
    out.append(arrow(360, 320, 440, 285, PURPLE))
    out.append(arrow(780, 210, 860, 175, TEAL))
    out.append(arrow(780, 250, 860, 295, BLUE))
    out.append(arrow(1130, 295, 1190, 295, BLUE))
    out.append(arrow(1315, 250, 1315, 220, PURPLE))
    out.append(arrow(780, 300, 860, 410, TEAL, label="汇总", lx=820, ly=365))
    out.append(arrow(1130, 425, 1190, 425, TEAL))
    out.append(arrow(1315, 470, 1315, 500, PURPLE))

    # real event strip
    out.append(f'<rect x="60" y="600" width="1380" height="190" rx="16" fill="{WHITE}" stroke="{GRID}"/>')
    out.append(text(85, 632, "一次真实运行的事件带（run 20261024T173630-b15ef5）", 13, 700, INK))
    events = [
        ("run/started", "0.0 s", BLUE),
        ("plan", "5.9 s", BLUE),
        ("retrieve", "11.8 s", BLUE),
        ("normalize", "0.005 s", TEAL),
        ("enrich", "109.8 s", AMBER),
        ("rank", "0.03 s", TEAL),
        ("render", "0.19 s", TEAL),
        ("run/completed", "127.7 s", BLUE),
    ]
    x0, step = 90, 184
    out.append(f'<line x1="{x0}" y1="700" x2="{x0 + step * (len(events) - 1)}" y2="700" stroke="{LINE}" stroke-width="2"/>')
    for i, (name, duration, color) in enumerate(events):
        x = x0 + i * step
        out.append(f'<circle cx="{x}" cy="700" r="7" fill="{color}"/>')
        out.append(text(x, 682, name, 11, 600, color, "middle"))
        out.append(text(x, 728, duration, 11, 400, MUTED, "middle"))
    out.append(text(90, 770, "报告任务另有一条独立事件带：run/started → report/started → 5/20 → 10/20 → 15/20 → 20/20 → 综合写作 → 渲染 → completed。", 11.5, 400, MUTED))

    # commands
    out.append('<rect x="60" y="820" width="1380" height="90" rx="14" fill="#0F172A"/>')
    out.append(text(85, 850, "排查命令", 11.5, 700, "#93C5FD", mono=True))
    out.append(text(85, 875, "jq -r 'select(.status==\"completed\") | [.stage,.duration_ms] | @tsv' logs/<run_id>.jsonl", 11, 400, "#E2E8F0", mono=True))
    out.append(text(85, 897, "jq -r 'select(.status==\"warning\") | [.stage,.message] | @tsv' logs/<run_id>.jsonl", 11, 400, "#E2E8F0", mono=True))
    return svg_doc(1500, 940, "".join(out))


# ---------------------------------------------------------------------------
# 07 cost & performance
# ---------------------------------------------------------------------------

def diagram_cost_performance() -> str:
    out = [title_block(
        "成本与性能：实测数据",
        "无模型时 1–2 秒；开启中文增强约 128 秒；报告是独立的 1–2 分钟任务",
        [("检索", BLUE), ("增强 / 报告", AMBER), ("成本", TEAL)],
    )]
    # panel A: stacked bar
    out.append(f'<rect x="60" y="130" width="650" height="330" rx="16" fill="{WHITE}" stroke="{GRID}"/>')
    out.append(text(85, 165, "一次检索链路的耗时构成", 14, 700, INK))
    out.append(text(85, 188, "主题 Agent Memory Systems Acceptance · 4 个来源 · 含中文增强", 11.5, 400, MUTED))
    total = 127.7
    segments = [("plan", 5.9, BLUE), ("retrieve", 11.8, "#60A5FA"), ("enrich", 109.8, AMBER), ("其他", 0.2, "#94A3B8")]
    x = 90
    width = 590
    for _name, value, color in segments:
        w = width * value / total
        out.append(f'<rect x="{x}" y="220" width="{w}" height="42" fill="{color}"/>')
        if w > 46:
            out.append(text(x + w / 2, 247, f"{value:.0f}s", 11.5, 700, WHITE, "middle"))
        x += w
    out.append(text(90, 285, "enrich 占 86%：40 条记录、4 批串行调用；检索本身只占 9%", 11.5, 400, MUTED))
    out.append(f'<line x1="90" y1="330" x2="680" y2="330" stroke="{GRID}"/>')
    out.append(text(90, 360, "无模型对照", 11.5, 700, MUTED))
    out.append(f'<rect x="190" y="345" width="8" height="22" fill="{TEAL}"/>')
    out.append(text(210, 362, "1.2 s（每源 5 条，17 条去重后）", 11.5, 400, INK))
    out.append(text(90, 400, "有模型", 11.5, 700, MUTED))
    out.append(f'<rect x="190" y="385" width="128" height="22" fill="{AMBER}"/>')
    out.append(text(326, 402, "127.7 s（120 条 → 102 条）", 11.5, 400, INK))
    out.append(text(90, 435, "结论：等待主要来自模型增强，而不是检索；报告不在这条链路里。", 11.5, 400, MUTED))

    # panel B: report timeline
    out.append(f'<rect x="740" y="130" width="700" height="330" rx="16" fill="{WHITE}" stroke="{GRID}"/>')
    out.append(text(765, 165, "报告任务的时间线（Top 20）", 14, 700, INK))
    out.append(text(765, 188, "run 20261024T174151-2e2f57 · deepseek-flash · 总计 134.6 秒", 11.5, 400, MUTED))
    blocks = [
        ("分析 1–5", 33, PURPLE), ("分析 6–10", 18, PURPLE), ("分析 11–15", 17, PURPLE),
        ("分析 16–20", 21, PURPLE), ("综合写作", 45, AMBER), ("渲染", 1, TEAL),
    ]
    total_b = sum(v for _, v, _ in blocks)
    x = 765
    width_b = 650
    render_w = 24
    scale_total = total_b - 1  # render is pinned to a visible minimum width
    for name, value, color in blocks:
        if name == "渲染":
            w = render_w
        else:
            w = max(4, (width_b - render_w) * value / scale_total)
        out.append(f'<rect x="{x}" y="225" width="{w - 2}" height="48" rx="6" fill="{color}"/>')
        out.append(text(x + (w - 2) / 2, 254, f"{value}s", 11, 700, WHITE, "middle"))
        if name == "渲染":
            out.append(text(x + w - 2, 295, name, 10.5, 400, MUTED, "end"))
        else:
            out.append(text(x + (w - 2) / 2, 295, name, 10.5, 400, MUTED, "middle"))
        x += w
    out.append(text(765, 340, "4 次分析 + 1 次综合；分析批次失败只丢该批，综合截断时预算翻倍重试。", 11.5, 400, MUTED))
    out.append(text(765, 390, "报告独立运行，目录在等待期间始终可访问。", 11.5, 600, TEAL))
    out.append(text(765, 420, "实测 80–135 秒，随主题与模型输出长度浮动。", 11.5, 400, MUTED))

    # panel C: cost
    out.append(f'<rect x="60" y="490" width="680" height="300" rx="16" fill="{WHITE}" stroke="{GRID}"/>')
    out.append(text(85, 525, "成本（人民币）", 14, 700, INK))
    out.append(text(85, 548, "按输入 1 元/百万 token、输出 2 元/百万 token 估算", 11, 400, MUTED))
    costs = [("学术 API", "¥0", "全部免费", TEAL), ("中文增强", "≈ ¥0.04", "40 条上限 / 次", BLUE),
             ("调研报告", "≈ ¥0.05–0.1", "20 篇 / 份", AMBER), ("一次演示", "< ¥0.5", "3–5 次运行", PURPLE)]
    for i, (name, value, note, color) in enumerate(costs):
        y = 580 + i * 48
        out.append(f'<circle cx="95" cy="{y}" r="5" fill="{color}"/>')
        out.append(text(112, y + 5, name, 12, 600, INK))
        out.append(text(330, y + 5, value, 13, 700, color))
        out.append(text(470, y + 5, note, 11, 400, MUTED))

    # panel D: resources
    out.append(f'<rect x="770" y="490" width="670" height="300" rx="16" fill="{WHITE}" stroke="{GRID}"/>')
    out.append(text(795, 525, "资源占用（实测）", 14, 700, INK))
    resources = [("API 常驻内存", "54–61 MB"), ("/api/health 热延迟", "2–4 ms"),
                 ("单主题工作区", "855 KB / 59 条"), ("catalog.html", "303 KB 自包含")]
    for i, (name, value) in enumerate(resources):
        y = 575 + i * 52
        out.append(text(795, y, name, 12, 400, MUTED))
        out.append(text(1415, y, value, 14, 700, INK, "end"))
        out.append(f'<line x1="795" y1="{y + 14}" x2="1415" y2="{y + 14}" stroke="{GRID}"/>')
    out.append(text(795, 780, "单核即可运行；外部 API 全部免费，现金成本来自可选的模型调用。", 11, 400, MUTED))

    out.append(text(60, 830, "口径：Sextant 数字来自 run_summary.json 与运行日志；人工耗时估算见场景价值文档。", 11.5, 400, MUTED))
    return svg_doc(1500, 870, "".join(out))


# ---------------------------------------------------------------------------
# 08 value comparison
# ---------------------------------------------------------------------------

def diagram_value_comparison() -> str:
    out = [title_block(
        "场景价值：从 2–3.5 小时到 3–4 分钟",
        "同一主题、同一周；人工方式是保守估算，Sextant 数字来自真实运行",
        [("人工", MUTED), ("Sextant", BLUE), ("可核验产物", TEAL)],
    )]
    out.append(f'<rect x="60" y="130" width="620" height="520" rx="16" fill="{WHITE}" stroke="{GRID}"/>')
    out.append(text(85, 168, "现在：人工筛选", 16, 700, MUTED))
    out.append(text(85, 192, "每周 1–2 次，4–6 个入口来回切换", 11.5, 400, MUTED))
    steps = [
        ("01", "发现", "在 Scholar / arXiv / 数据库之间换关键词搜", "20–30 min"),
        ("02", "初筛", "逐条读标题和摘要，100 条左右", "70–100 min"),
        ("03", "整理", "把作者、年份、链接粘进文档", "30–60 min"),
        ("04", "汇报", "再写一段方向趋势", "20–30 min"),
        ("05", "复现", "搜索词和筛选理由通常没留下", "—"),
    ]
    for i, (num, name, desc, time) in enumerate(steps):
        y = 225 + i * 80
        out.append(f'<circle cx="102" cy="{y + 18}" r="15" fill="{SLATE_BG}"/>')
        out.append(text(102, y + 23, num, 10.5, 700, MUTED, "middle"))
        out.append(text(130, y + 14, name, 13, 700, INK))
        out.append(text(130, y + 36, desc, 11, 400, MUTED))
        out.append(text(650, y + 18, time, 12, 700, MUTED, "end"))
    out.append(f'<line x1="85" y1="620" x2="655" y2="620" stroke="{GRID}"/>')
    out.append(text(85, 645, "合计：2–3.5 小时；不可追溯、不可复现", 12, 700, RED))

    out.append(f'<rect x="740" y="130" width="700" height="520" rx="16" fill="{WHITE}" stroke="{BLUE}" stroke-opacity="0.25"/>')
    out.append(text(765, 168, "Sextant：一条链路交付", 16, 700, BLUE))
    out.append(text(765, 192, "输入主题，目录先可用；报告按需生成", 11.5, 400, MUTED))
    sx_steps = [
        ("01", "检索 + 去重", "4 个来源并行，DOI / 标题指纹去重", "约 128 s"),
        ("02", "可解释排序", "Top 20 带分数构成、来源、日期、摘要", "即时"),
        ("03", "按需报告", "六个分析小节 + 方法家族卡片", "1–2 min"),
    ]
    for i, (num, name, desc, time) in enumerate(sx_steps):
        y = 235 + i * 90
        out.append(f'<circle cx="782" cy="{y + 18}" r="15" fill="{BLUE_BG}"/>')
        out.append(text(782, y + 23, num, 10.5, 700, BLUE, "middle"))
        out.append(text(810, y + 14, name, 13, 700, INK))
        out.append(text(810, y + 36, desc, 11, 400, MUTED))
        out.append(text(1415, y + 18, time, 12, 700, BLUE, "end"))
    out.append(f'<line x1="765" y1="520" x2="1415" y2="520" stroke="{GRID}"/>')
    out.append(text(765, 548, "合计：约 3–4 分钟；每一步都有产物和日志", 12, 700, TEAL))
    out.append(text(765, 580, "120 条原始 → 102 条去重 → Top 20 进入报告", 11.5, 400, MUTED))
    out.append(text(765, 606, "每条记录可点回来源；重跑留下新的 run_id", 11.5, 400, MUTED))
    out.append(text(765, 632, "报告卡片必须归属子方向，否则拒绝产出", 11.5, 400, MUTED))

    # metrics strip
    metrics = [("4", "来源"), ("120 → 102", "原始 → 去重"), ("Top 20", "进入报告"), ("100%", "记录带来源")]
    for i, (value, label) in enumerate(metrics):
        x = 60 + i * 350
        out.append(f'<rect x="{x}" y="690" width="320" height="90" rx="14" fill="{WHITE}" stroke="{GRID}"/>')
        out.append(text(x + 20, 730, value, 24, 700, BLUE if i % 2 == 0 else TEAL))
        out.append(text(x + 20, 756, label, 11.5, 400, MUTED))

    out.append(text(60, 825, "人工耗时按每条摘要 40–60 秒估算；Sextant 的 128 秒包含检索、去重、中文增强和目录渲染。两者口径不同，只用于说明数量级差异。", 11, 400, MUTED))
    return svg_doc(1500, 860, "".join(out))


# ---------------------------------------------------------------------------
# 09 deployment topology
# ---------------------------------------------------------------------------

def diagram_deployment_topology() -> str:
    out = [title_block(
        "部署拓扑",
        "一条命令启动；Web 与 MCP 使用同一镜像、同一份工作区",
        [("请求", BLUE), ("挂载", TEAL), ("外部调用", PURPLE)],
        width=1300,
    )]
    out.append(card(70, 150, 260, 90, "浏览器", ["http://localhost:8000"], BLUE))
    out.append(card(970, 150, 260, 90, "MCP Agent", ["http://localhost:8001/mcp"], PURPLE))

    out.append(panel(70, 300, 520, 250, "api 容器", BLUE))
    out.append(card(100, 345, 460, 80, "api :8000", ["FastAPI + 控制台静态页"], BLUE))
    out.append(card(100, 445, 460, 80, "pipeline + report", ["与 MCP 同一套代码路径"], TEAL))

    out.append(panel(620, 300, 520, 250, "mcp 容器", PURPLE))
    out.append(card(650, 345, 460, 80, "mcp :8001", ["Streamable HTTP"], PURPLE))
    out.append(card(650, 445, 460, 80, "MCP tools", ["run_topic / generate_report"], PURPLE))

    out.append(panel(70, 600, 1070, 130, "共享工作区", TEAL))
    out.append(card(100, 635, 1010, 70, "volume ./workspace", [
        "主题目录 / JSONL 日志 / catalog.html / 报告 HTML；两个进程看到同一份文件",
    ], TEAL))

    out.append(card(230, 780, 340, 80, "学术源", ["OpenAlex · arXiv · Crossref · DOAJ"], PURPLE))
    out.append(card(650, 780, 340, 80, "LLM（可选）", ["OpenAI 兼容接口"], PURPLE))

    out.append(arrow(200, 240, 200, 345, BLUE, label="HTTP", lx=230, ly=292))
    out.append(arrow(1100, 240, 1100, 345, PURPLE, label="MCP", lx=1130, ly=292))
    out.append(arrow(330, 425, 330, 445, BLUE))
    out.append(arrow(880, 425, 880, 445, PURPLE))
    out.append(path_arrow("M 330 525 L 330 635", TEAL, dash="5 5", label="挂载", lx=350, ly=580))
    out.append(path_arrow("M 880 525 L 880 635", TEAL, dash="5 5", label="挂载", lx=900, ly=580))
    out.append(text(70, 575, "同一镜像，不同启动命令", 11.5, 400, MUTED))
    # external calls run around the shared volume, not through it
    out.append(path_arrow("M 100 485 L 40 485 L 40 820 L 230 820", PURPLE,
                          label="检索", lx=40, ly=655))
    out.append(path_arrow("M 1100 485 L 1260 485 L 1260 820 L 990 820", PURPLE,
                          label="enrich / report", lx=1125, ly=820))

    out.append('<rect x="70" y="890" width="1070" height="55" rx="12" fill="#0F172A"/>')
    out.append(text(90, 924, "docker compose up --build   #  8000 Web / 8001 MCP / ./workspace 挂载", 12, 500, "#E2E8F0", mono=True))
    return svg_doc(1300, 980, "".join(out))


# ---------------------------------------------------------------------------
# entrypoint
# ---------------------------------------------------------------------------

DIAGRAMS = {
    "01-architecture": diagram_architecture,
    "02-agent-flow": diagram_agent_flow,
    "03-report-sequence": diagram_report_sequence,
    "04-state-machine": diagram_state_machine,
    "05-error-handling": diagram_error_handling,
    "06-observability": diagram_observability,
    "07-cost-performance": diagram_cost_performance,
    "08-value-comparison": diagram_value_comparison,
    "09-deployment-topology": diagram_deployment_topology,
}


def main() -> None:
    target = Path(__file__).resolve().parent.parent / "docs" / "diagrams"
    target.mkdir(parents=True, exist_ok=True)
    for name, render in DIAGRAMS.items():
        path = target / f"{name}.svg"
        path.write_text(render(), encoding="utf-8")
        print(f"wrote {path.relative_to(target.parent.parent)}")


if __name__ == "__main__":
    main()
