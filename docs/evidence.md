# 可观测性与测试证据

> 对应赛道 B 的「可观测性证据」与「测试数据」要求：给出原始运行记录、测试条件和关键配置，而不是只给汇总数字。
> 本文的日志片段来自本地 `workspace/`，绝对路径已脱敏；原始 JSONL 保留在演示机器上，可按第 5 节复现。

---

## 1. 测试条件

| 项 | 值 |
|---|---|
| 运行日期 | 2026-10-24 |
| 环境 | Windows 11、Python 3.11、`sextant 0.1.0` |
| 主题 | `Agent Memory Systems Acceptance` |
| 来源 | OpenAlex、arXiv、Crossref、DOAJ（4 个） |
| 每源每查询上限 | 20 条 |
| 查询计划 | 6 条检索式（OpenAlex 2、arXiv 2、Crossref 1、DOAJ 1） |
| 模型 | `deepseek-flash`（OpenAI 兼容接口） |
| 增强上限 | 40 条记录，10 条一批 |
| 报告输入 | Top 20 篇，5 个研究方向 |
| 工作区 | 本地磁盘 `workspace/<topic>/` |

---

## 2. 检索运行证据

`run_id = 20261024T173630-b15ef5`，`logs/20261024T173630-b15ef5.jsonl`。

### 2.1 阶段耗时

| 阶段 | 耗时 | 结果 |
|---|---:|---|
| plan | 5 922 ms | 选定 4 个来源，生成 6 条检索式 |
| retrieve | 11 819 ms | 120 条原始记录（OpenAlex 40 / arXiv 40 / Crossref 20 / DOAJ 20） |
| normalize | 5 ms | 去重后 118 篇，其中 16 篇待核实，可用 102 篇 |
| enrich | 109 786 ms | 40 篇中文增强，降级批次 0 |
| rank | 28 ms | 102 条记录，首位得分 55.0，7 个方向 |
| render | 187 ms | 自包含目录 686 606 字节 |
| **检索链路合计** | **约 127.7 s** | 目录可用；报告是独立任务，不计入 |

### 2.2 原始日志摘录

以下为原始 JSONL 的关键事件。为便于阅读，只保留关键字段并删除绝对路径；事件顺序未改：

```json
{"seq": 2, "stage": "plan", "status": "completed", "message": "选定 4 个来源，生成 6 条检索式", "data": {"planner": "llm+heuristic", "sources": ["openalex", "arxiv", "crossref", "doaj"]}}
{"seq": 3, "stage": "retrieve", "status": "started", "message": "并行检索 4 个来源", "data": {"sources": {"openalex": 2, "arxiv": 2, "crossref": 1, "doaj": 1}}}
{"seq": 10, "stage": "retrieve", "status": "completed", "message": "共取得 120 条原始记录", "duration_ms": 11814, "data": {"total_records": 120, "per_source": {"openalex": 40, "arxiv": 40, "crossref": 20, "doaj": 20}}}
{"seq": 11, "stage": "normalize", "status": "completed", "message": "去重后 118 篇，其中 16 篇待核实", "duration_ms": 5, "data": {"usable": 102, "pending": 16, "raw": 120}}
{"seq": 13, "stage": "enrich", "status": "completed", "message": "完成 40 篇中文增强", "duration_ms": 109786, "data": {"enriched": 40, "degraded_batches": 0, "reasons": []}}
{"seq": 14, "stage": "rank", "status": "completed", "message": "排序完成，首位得分 55.0", "duration_ms": 28, "data": {"records": 102, "top_score": 55.0, "directions": 7}}
{"seq": 15, "stage": "render", "status": "completed", "message": "目录已生成", "duration_ms": 187, "data": {"output": "workspace/agent-memory-systems-acceptance/output/catalog.html", "strict_chinese": true, "size_bytes": 686606}}
```

> 这次运行的日志里还包含旧版把报告作为第 7 段的 `seq 16–17`。现行版本报告已拆成独立任务，见下一节；检索阶段本身未变。

---

## 3. 报告任务证据（独立任务）

`run_id = 20261024T174151-2e2f57`，`logs/20261024T174151-2e2f57.jsonl`。

### 3.1 事件序列

| seq | stage | status | 内容 |
|---:|---|---|---|
| 1 | run | started | 开始生成调研报告 |
| 2 | report | started | 正在分析 20 篇文献并综合写作（5 个方向） |
| 3 | report | running | 正在分析 20 篇文献 |
| 4 | report | running | 文献分析 5/20 |
| 5 | report | running | 文献分析 10/20 |
| 6 | report | running | 文献分析 15/20 |
| 7 | report | running | 文献分析 20/20 |
| 8 | report | running | 正在综合写作 |
| 9 | report | running | 正在渲染报告 |
| 10 | report | completed | 20 篇文献，134 599 ms，225 797 字节 |
| 11 | run | completed | 报告任务结束 |

### 3.2 原始日志摘录

```json
{"seq": 1, "stage": "run", "status": "started", "message": "开始生成调研报告：Agent Memory Systems Acceptance", "data": {"run_id": "20261024T174151-2e2f57"}}
{"seq": 2, "stage": "report", "status": "started", "message": "正在分析 20 篇文献并综合写作", "data": {"model": "deepseek-flash", "papers": 20, "directions": 5}}
{"seq": 7, "stage": "report", "status": "running", "message": "文献分析 20/20"}
{"seq": 8, "stage": "report", "status": "running", "message": "正在综合写作"}
{"seq": 10, "stage": "report", "status": "completed", "message": "调研报告已生成，共 20 篇文献", "duration_ms": 134599, "data": {"output": "workspace/agent-memory-systems-acceptance/output/综合调研报告：Agent Memory Systems Acceptance.html", "notes": [], "size_bytes": 225797}}
{"seq": 11, "stage": "run", "status": "completed", "message": "调研报告已生成，共 20 篇文献", "duration_ms": 134600, "data": {"papers": 20, "notes": []}}
```

**可核验点**：

- 报告有独立的 `run_id`、日志文件和 SSE 流，不写入主题的 `runs.json`。
- 事件从 `run/started` 到 `run/completed` 成对出现，中间是 `report/*`，可以单独订阅。
- 报告失败或降级时 `status=warning` 且 `notes` 说明原因，不会静默成功。

---

## 4. 降级与失败证据

### 4.1 综合写作截断（历史样本）

`run_id = 20261024T174235-969e42`，主题 `deepseek`，旧版预算策略（`max_tokens=8000`，截断后减半论文）：

```json
{"seq": 10, "stage": "report", "status": "warning", "message": "调研报告已生成，共 20 篇文献", "duration_ms": 204432, "data": {"notes": ["综合写作输出被截断（输出达到 max_tokens=8000 上限（其中 reasoning_tokens=4981），未产出完整 JSON），改用前 10 篇重试", "综合写作输出被截断（输出达到 max_tokens=8000 上限（其中 reasoning_tokens=7687），未产出完整 JSON），改用前 5 篇重试", "15 篇文献未归入任何子方向，已并入「其他相关研究」"], "size_bytes": 246074}}
```

这份记录说明两点：

- 截断没有被吞掉，而是以 `warning` + `notes` 暴露给用户，报告仍然产出。
- 旧策略「截断就减半论文」是错的方向：论文越少，推理 token 反而越高。现行策略改为**预算翻倍**（16000 → 32000 → 64000），到顶仍截断则按 `method_category` 确定性归组，全部文献保留。细节见 [cost-and-performance.md](cost-and-performance.md)。

### 4.2 单源失败隔离

一次 RAG 主题运行中，PubMed 请求在本地网络下 4.8 秒后证书校验失败。日志把该来源记为 `warning`，其余四个来源照常完成，目录正常生成。这是「来源级失败不阻断运行」的直接证据；原始记录见 `docs/cost-and-performance.md` 的场景 B。

### 4.3 任务互斥

同一主题重复点击「生成调研报告」，API 返回 `409`，错误信息指明已有运行中的报告任务；检索任务运行时点击报告同样返回 `409`。相关测试见 `tests/test_api.py::test_report_generation_returns_conflict` 等。

---

## 5. 如何复现这些证据

```bash
# 1. 启动服务
sextant

# 2. 创建主题（或直接打开已有主题重跑）
curl -X POST http://localhost:8000/api/topics \
  -H 'Content-Type: application/json' \
  -d '{"topic_name":"Agent Memory Systems Acceptance","keywords":["LLM agent","memory"]}'

# 3. 查看实时事件
curl -N "http://localhost:8000/api/topics/<topic_id>/events?run_id=<run_id>"

# 4. 运行结束后统计阶段耗时
jq -r 'select(.status=="completed") | [.stage, .duration_ms] | @tsv' \
  workspace/<topic_id>/logs/<run_id>.jsonl

# 5. 生成报告并单独观察
curl -X POST http://localhost:8000/api/topics/<topic_id>/report
```

---

## 6. 证据边界

- 当前没有 Prometheus/Grafana 等监控后端；可观测性由 **JSONL 结构化日志 + SSE + `run_summary.json`** 构成，足以定位阶段、来源和模型失败。
- 日志和 `workspace/` 在 `.gitignore` 中，不随仓库提交；本文保留了脱敏后的原始片段，完整文件在演示机器上可现场查看。
- 报告耗时随主题和模型输出长度浮动（实测 80–135 秒），表中数字是特定运行的结果，不是 SLA。
