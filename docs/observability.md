# 可观测性证据

系统提供三个层次的可观测性：**实时事件**、**结构化日志**、**运行汇总**。三者来自同一份事件流，数字不会互相矛盾。

## 1. 实时事件（SSE）

`GET /api/topics/{topic_id}/events?run_id=<run_id>`

- 传输：`text/event-stream`。
- 历史回放：连接时先读取该 run 的完整 JSONL，再续推实时事件。
- 去重：每条事件有单调递增的 `seq`，客户端与服务端都按 `seq` 过滤重复。
- 心跳：15 秒无事件时发送 `: ping` 注释行，避免代理断开空闲连接。
- 终止：收到 `stage=run, status=completed|failed` 后服务端关闭流，前端同时停止重连。

控制台据此渲染六段式阶段条与事件时间线。

## 2. 结构化日志

位置：`workspace/<topic_id>/logs/<run_id>.jsonl`，一行一个事件。

### 事件 Schema

| 字段 | 类型 | 说明 |
|---|---|---|
| `seq` | int | 单次运行内单调递增 |
| `ts` | ISO8601 | UTC 毫秒时间 |
| `run_id` | str | 运行标识 |
| `topic_id` | str | 主题标识 |
| `stage` | str | `plan` / `retrieve` / `normalize` / `enrich` / `rank` / `render` / `run` |
| `status` | str | `started` / `completed` / `warning` / `skipped` / `failed` |
| `message` | str | 面向人的一句话 |
| `duration_ms` | int? | 阶段或单次调用耗时 |
| `data` | object? | 阶段特定字段（见下表） |

### 各阶段的关键字段

| 阶段 | `data` 字段 |
|---|---|
| `plan` | `planner`、`keywords`、`sources`、`queries` |
| `retrieve`（单查询） | `source`、`query`、`count`、`duration_ms`、`error` |
| `retrieve`（汇总） | `total_records`、`per_source`、`duration_ms` |
| `normalize` | `raw`、`usable`、`pending` |
| `enrich` | `enriched`、`model` |
| `rank` | `records`、`top_score`、`directions` |
| `render` | `output`、`size_bytes`、`strict_chinese` |
| `run`（完成） | `records`、`pending`、`enriched`、`stage_duration_ms` |
| `run`（失败） | `error_type`、`hint`、`stage_duration_ms` |

### 常用排查命令

```bash
# 每个阶段的耗时
jq -r 'select(.duration_ms) | "\(.stage)\t\(.duration_ms)"' logs/<run_id>.jsonl

# 哪些来源失败了
jq -r 'select(.status=="warning") | "\(.data.source // "-")\t\(.message)"' logs/<run_id>.jsonl

# 本次运行的总记录数与去重情况
jq -r 'select(.stage=="normalize") | .data' logs/<run_id>.jsonl
```

原始日志也可通过 API 获取：

```
GET /api/topics/{topic_id}/logs/{run_id}
```

## 3. 运行汇总

位置：`workspace/<topic_id>/output/run_summary.json`

```json
{
  "run_id": "20261024183000-a1b2c3",
  "topic_id": "llm-agent-evaluation",
  "status": "completed",
  "finished_at": "2026-10-24T18:30:42+00:00",
  "duration_ms": 28413,
  "stage_duration_ms": {
    "plan": 12,
    "retrieve": 24110,
    "normalize": 3,
    "enrich": 3200,
    "rank": 2,
    "render": 1086,
    "total": 28413
  },
  "raw_records": 187,
  "records": 96,
  "pending": 21,
  "enriched": 40,
  "llm_model": "deepseek-chat",
  "catalog": "/app/workspace/llm-agent-evaluation/output/catalog.html"
}
```

这份文件是答辩时展示成本与性能的直接证据：原始条数、去重后条数、模型参与条数、各阶段耗时都可以和日志对上。

## 4. 健康检查

`GET /api/health`

```json
{
  "status": "ok",
  "version": "0.1.0",
  "llm_configured": true,
  "llm_model": "deepseek-chat",
  "workspace": "/app/workspace",
  "workspace_writable": true,
  "active_runs": 0
}
```

Docker 的 `HEALTHCHECK` 每 30 秒调用一次，容器状态可以直接作为部署健康度证据。

## 5. 运行状态查询

`GET /api/runs/{run_id}` 返回内存中的任务状态：

```json
{
  "run_id": "20261024183000-a1b2c3",
  "topic_id": "llm-agent-evaluation",
  "status": "completed",
  "stage": "done",
  "started_at": "2026-10-24T18:30:14+00:00",
  "finished_at": "2026-10-24T18:30:42+00:00",
  "error": null,
  "hint": null,
  "records": 96,
  "events": 31
}
```

进程重启后内存状态丢失，但磁盘上的 `runs.json` 与日志仍在，历史运行仍可审计。

## 6. 最小监控建议

当前实现不依赖 Prometheus / OpenTelemetry，满足「不要求所有项目采用相同监控方案」的要求。若需要接入：

- **指标**：从 `run_summary.json` 与事件日志派生 `run_duration_seconds`、`records_total`、`source_failures_total`。
- **追踪**：每个阶段一个 span，`run_id` 作为 trace id。
- **日志**：JSONL 已是结构化格式，可直接被 Loki / ELK 采集。
