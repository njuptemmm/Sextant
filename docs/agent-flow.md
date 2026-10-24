# Agent 执行流程

本文说明一次运行从输入到产物的完整链路：任务如何拆解、每一步调用什么工具、上下文与状态放在哪里、失败后如何恢复。

## 1. 总览

```mermaid
sequenceDiagram
    participant U as 用户 / Agent
    participant API as FastAPI
    participant J as JobRegistry
    participant P as Pipeline
    participant S as 学术源
    participant L as LLM（可选）
    participant W as 工作区
    participant E as SSE

    U->>API: POST /api/topics
    API->>W: 写入 topic.json / runs.json
    API->>J: start(topic_id)
    J-->>API: run_id（202）
    API-->>U: topic + job
    J->>P: run_pipeline()
    P->>E: run.started
    P->>W: queries.json（plan）
    P->>S: 并行检索（每源一线程）
    S-->>P: 原始记录
    P->>W: 02_raw/search/*.jsonl
    P->>P: 去重 + 质量门控
    P->>L: 批量中文增强（可选）
    L-->>P: 标题 / 摘要 / 方向
    P->>W: ranked_articles.jsonl
    P->>W: catalog.html
    P->>E: run.completed
    U->>API: GET /events
    API-->>U: 历史 + 实时事件
```

## 2. 阶段契约

每个阶段都有明确的输入、输出和失败语义。阶段之间只通过工作区文件或内存中的记录列表传递数据。

### 2.1 plan — 查询规划

| | |
|---|---|
| 输入 | `topic.json`：主题名、关键词、问题、时间窗 |
| 决策 | 哪些来源与主题相关；每个来源用什么检索式 |
| 工具 | 可选 `llm.plan_queries` |
| 输出 | `01_queries/queries.json` |

来源选择规则（`sources_registry.py`）：

- 始终启用：OpenAlex（全学科）、arXiv（预印本）。
- 默认追加：Crossref（DOI 元数据）、DOAJ（开放获取）。
- 命中生物医学词表时追加：PubMed、Europe PMC。
- 配置了 `SEMANTIC_SCHOLAR_API_KEY` 时追加 Semantic Scholar。
- 单次运行最多 5 个来源，每源最多 2 条检索式。

检索式生成：

- 普通来源：`keyword1 AND keyword2`。
- arXiv：`all:"keyword1" AND all:"keyword2"`。
- 模型可额外提供同义词和更精确的布尔式，但必须落在已选来源内，且受每源 2 条的上限约束。

### 2.2 retrieve — 多源检索

| | |
|---|---|
| 输入 | `queries.json` |
| 工具 | `engine.sources.*_search` |
| 输出 | `02_raw/search/<source>.jsonl`、`manifests/retrieval.json` |
| 失败语义 | 单条查询失败记 `warning`；单源全失败记 `warning`；所有源都无结果才终止 |

并发模型：**每源一个线程，源内查询串行**。原因：`RateLimit` 是进程级的，串行才能保证 arXiv 的 3 秒间隔不被并发破坏。

### 2.3 normalize — 去重与门控

| | |
|---|---|
| 输入 | 全部原始记录 |
| 处理 | DOI 优先、标题指纹兜底去重；同组保留摘要最完整的记录；合并来源、查询、引用数 |
| 输出 | 可用记录（内存）、`03_processed/pending_verification_articles.jsonl` |
| 失败语义 | 没有可用记录时抛 `PipelineError`，提示放宽时间窗或更换关键词 |

质量门控复用 `content_quality.assess_abstract_quality`：摘要缺失、过短、模板页、标题重复、增补材料都会被拒，并写入 `pending_verification_reason`。

### 2.4 enrich — 中文增强（可选）

| | |
|---|---|
| 输入 | 前 `SEXTANT_LLM_MAX_CANDIDATES`（默认 40）条可用记录 |
| 工具 | OpenAI 兼容 `/chat/completions` |
| 输出 | `title_zh`、`abstract_zh`、`plain_summary_zh`、`research_direction` |
| 失败语义 | 超时、非 JSON、字段缺失 → 返回空 dict，链路继续 |

Prompt 明确禁止编造原文没有的结论；返回的 `record_id` 必须命中输入集合，否则丢弃。

### 2.5 rank — 相关性排序

| 分数项 | 上限 | 计算 |
|---|---:|---|
| 关键词覆盖 | 40 | 命中去重关键词数 / 关键词总数 × 40 |
| 标题命中 | 10 | 任一关键词出现在标题 |
| 引用影响力 | 20 | `min(20, log10(1+citations) × 6.5)` |
| 时效性 | 20 | ≤1 年 20；≤2 年 12；≤5 年 6；更早 1 |
| 跨源印证 | 5 | 两个及以上来源返回同一条 |
| 摘要质量 | 5 | 通过质量门控 |

结果按总分降序写入 `03_processed/ranked_articles.jsonl`，每条带 `rank`、`ranking_score`、`ranking_reasons`、`research_direction`。

方向归类：模型给出则采用；否则取「标题权重 2、摘要权重 1」计数最高的主题关键词；都没有则归入「其他相关研究」。

### 2.6 render — 目录生成

| | |
|---|---|
| 输入 | `ranked_articles.jsonl` + `topic.json` |
| 输出 | `output/catalog.html` |
| 失败语义 | 严格中文校验失败时自动降级重渲染，并记 `warning` |

产物是自包含 HTML：无外部 JS/CSS 依赖，按研究方向分组，支持搜索、来源/年份/类型筛选和中英对照。

## 3. 上下文与状态管理

系统的「上下文」不是一段不断增长的对话，而是**分层的文件状态**：

| 层 | 位置 | 生命周期 | 谁写 | 谁读 |
|---|---|---|---|---|
| 主题定义 | `00_intake/topic.json` | 跨运行 | API / MCP | 所有阶段 |
| 本次计划 | `01_queries/queries.json` | 单次运行 | plan | retrieve、审计 |
| 原始证据 | `02_raw/search/*.jsonl` | 单次运行 | retrieve | normalize、审计 |
| 归一结果 | `03_processed/*.jsonl` | 跨运行 | normalize、rank | render、API |
| 交付物 | `output/catalog.html` | 跨运行 | render | 用户 |
| 事件流 | `logs/<run_id>.jsonl` | 跨运行 | runner | SSE、审计 |
| 运行历史 | `runs.json` | 跨运行 | runner / jobs | API、控制台 |

这样设计的原因：

- **可重入**：每个阶段只依赖文件，失败后可以从任意阶段重跑。
- **可对比**：两次运行的计划、原始返回、排序结果都在，能解释「为什么这次结果变了」。
- **可审计**：答辩时可以逐层下钻，而不是只看最终 HTML。

## 4. 异常与恢复路径

| 场景 | 系统行为 | 用户看到 |
|---|---|---|
| 单个来源超时 | 该源记 `warning`，其他源继续 | 时间线出现黄色「已降级」 |
| 全部来源无结果 | `PipelineError`，保留 `queries.json` | 红色错误 + 建议放宽时间窗/换关键词 |
| 摘要质量不足 | 进入待核实区，不参与排序 | 目录底部「待核实」分组 |
| 模型超时 | 降级为规则模式 | 「中文增强」阶段显示「跳过/已降级」 |
| 模型返回非法 JSON | 丢弃增强结果，继续 | 同上 |
| 中文校验不通过 | 降级渲染并告警 | 目录仍可用，日志有 `chinese_gate` |
| 进程重启 | 内存任务丢失，磁盘结果保留 | 主题列表仍能打开已有目录 |

人工介入点：失败后用户可以选择「重跑」、修改关键词新建主题，或直接查看 `logs/<run_id>.jsonl` 定位阶段。

## 5. 与 MCP Agent 的协作

MCP 暴露的是同一套 pipeline，而不是另一套实现：

- `create_topic` 适合需要异步推进的 Agent：拿到 `run_id` 后轮询 `get_run_status`。
- `run_topic` 适合一次性任务：阻塞直到目录生成，直接拿到路径与统计。
- `search_papers` / `score_records` 适合自定义循环：Agent 自己决定检索式和排序策略，只复用归一化与评分函数。

工具描述里写明了推荐链路，减少 Agent 的试错成本。
