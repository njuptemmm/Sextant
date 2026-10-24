# 异常处理说明

设计原则：**失败要局部化、可解释、可恢复**。任何外部依赖出问题，都不能让用户只看到「出错了」。

配图：[异常处理：失败域与降级路径](diagrams/05-error-handling.svg)（[PNG](diagrams/png/05-error-handling.png)）。

## 1. 失败域划分

```
请求级 ── 参数非法、主题重复、路径越界      → 立即返回 4xx，不启动任务
运行级 ── 检索全部失败、无可用记录          → 终止运行，保留中间产物
来源级 ── 单个学术源超时 / 限流 / 解析失败  → 降级，其他来源继续
查询级 ── 同源内某条检索式失败              → 记录后继续下一条
模型级 ── 超时 / 非法 JSON / 字段缺失       → 丢弃增强，退回规则模式
渲染级 ── 中文质量门控不通过                → 降级渲染并告警
```

## 2. 参数与请求校验

| 检查 | 位置 | 返回 |
|---|---|---|
| 主题名为空 | `workspace.create_topic` | 400 |
| 日期格式非 `YYYY-MM-DD` | `workspace._clean_date` | 400 |
| 主题已存在 | `create_topic` | 409 |
| 主题 ID 非法或路径越界 | `workspace.topic_dir` | 400 |
| `max_results` 越界 | Pydantic `Field(ge=1, le=50)` | 422 |
| 同一主题已有运行 | `JobRegistry.start` | 409 |

路径校验使用 `resolve()` 后比较父目录，拒绝 `../`、绝对路径注入和非法字符。

## 3. 外部接口：超时、重试与限流

### 3.1 HTTP 层

`engine/http_client.py`：

- 默认超时 30 秒。
- 最多重试 2 次，退避 1 秒、2 秒。
- 重试只针对网络错误和明显的异常响应；4xx 语义错误不重试。
- JSON 响应必须通过「以 `{` 或 `[` 开头」的合法性检查，避免把 HTML 错误页当数据解析。

### 3.2 限流层

`engine/sources.py` 为每个来源定义最小请求间隔：

| 来源 | 间隔 | 原因 |
|---|---:|---|
| arXiv | 3.0 s | 官方要求 |
| Semantic Scholar | 1.0 s | 无 key 时容易 429 |
| bioRxiv / medRxiv | 0.5 s | 非官方接口，保守取值 |
| OpenAlex / Crossref / DOAJ | 0.25 s | 公共 API 的礼貌间隔 |

**同源查询串行执行**，保证限流器不被并发竞态破坏；不同来源之间并行。

### 3.3 重试上限之后

- 单条查询失败 → `retrieve` 事件 `status=warning`，带 `source`、`query`、`error`。
- 单源全部失败 → 该源写入空文件，`manifests/retrieval.json` 记录 `failed_queries`。
- 所有源都没有记录 → 抛 `PipelineError("所有来源均未返回结果…")`。

## 4. 模型调用

| 异常 | 处理 |
|---|---|
| 未配置 key | 直接跳过，事件 `status=skipped` |
| 超时 | `LLMError` → `enrich_records` 返回 `{}` |
| 返回带 Markdown 代码围栏 | 自动剥离 ``` 后再解析 |
| 返回非法 JSON | 尝试截取第一个 `{...}` / `[...]`；仍失败则丢弃 |
| `response_format` 不被支持 | 去掉该字段重试一次 |
| 返回的 `record_id` 不在输入集合 | 丢弃该条，防止污染其他记录 |
| 返回英文而非中文 | 渲染阶段中文校验失败 → 降级渲染并告警 |

Prompt 中明确要求「不得编造原文没有的结论、数字或方法」，这是内容正确性的第一道约束。

## 5. 状态恢复

| 故障 | 磁盘上保留什么 | 如何恢复 |
|---|---|---|
| 检索中途进程退出 | `queries.json`、已完成的 `02_raw/*.jsonl` | 重新运行，retrieve 会覆盖对应源文件 |
| 排序后渲染失败 | `ranked_articles.jsonl` | 直接重跑；render 只依赖该文件 |
| API 进程重启 | 所有已完成产物、`runs.json` | 控制台仍可打开已有目录；运行中的任务需要重跑 |
| 工作区不可写 | 无 | `/api/health` 返回 `degraded`，创建主题返回 400 |

`runs.json` 保留最近 50 次运行；每次运行的状态变化（running → completed/failed）都会落盘，因此 UI 能区分「从未运行」和「上次失败」。

## 6. 用户可见的反馈

失败事件包含两个额外字段：

```json
{
  "stage": "run",
  "status": "failed",
  "message": "RetrievalError: 所有来源均未返回结果，请检查网络或调整关键词",
  "data": {
    "error_type": "RetrievalError",
    "hint": "检查网络连通性或稍后重试；OpenAlex / arXiv 在部分网络环境下会超时。"
  }
}
```

`hint` 由 `runner._hint_for` 根据错误类型生成，覆盖：

- 网络 / 超时 → 检查连通性，稍后重试。
- 412 / 429 → 降低并发或缩短关键词。
- 质量门控 → 放宽时间范围或换更通用的英文关键词。
- 其他 → 指向本次运行的 JSONL 日志。

控制台把 `message` 显示为错误标题，把 `hint` 显示为下一步建议，而不是让用户自己猜。

## 7. 未覆盖的场景（已知限制）

- 进程内任务在重启后不会自动续跑；需要外部队列才能做到。
- 没有对单个来源设置硬性总超时，极端网络环境下整次运行可能被一个慢源拖长。
- 没有对 HTML 产物做 XSS 清洗之外的链接协议校验（外部链接均为 `http/https`）。
