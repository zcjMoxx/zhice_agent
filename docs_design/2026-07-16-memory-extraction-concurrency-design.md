# Memory 自动提取并发设计

> 状态：已完成设计并落地代码。

## 1. 背景

当前后台 Memory 提取在每个普通聊天 Turn 完成后，为 `(actor, session)` 创建一个 `threading.Timer`。同一个 Session 的新 Turn 会取消旧 Timer 并重新计时，空闲五分钟后直接在线程中调用 LLM。

该实现对本地少量用户足够直接，但用户和 Session 增多后存在三个问题：

- 每个等待中的 Session 占用一个 Timer 线程，等待任务数会直接转化为线程数。
- 多个 Session 同时到期时会无界并发调用提取模型，可能触发 Provider 限流和资源突发。
- 同一用户的多个 Session 同时完成提取时，会并发修改同一个 Memory 和 `pending_notification.json`；Memory 正文有文件锁，但通知文件当前没有用户级串行保证。

本次设计用进程内轻量调度器替换“一 Session 一个 Timer”，不引入 Redis、Celery、消息队列服务或新的任务数据库。

## 2. 目标

- 无论等待多少 Session，始终只保留一个调度器线程。
- 全局 Memory 提取并发有明确上限，默认最多两个 LLM 调用。
- 同一用户在同一时间最多执行一个提取任务。
- 同一 Session 重复完成 Turn 时只保留最新到期时间，不生成重复任务。
- 新 Turn、Session 清空或删除时可以取消尚未执行的任务。
- Provider 临时失败时有限重试，格式、安全和证据失败不重试。
- 保持普通聊天非阻塞，调度失败不影响当前回答。

## 3. 非目标

- 不建设跨进程分布式任务队列。
- 不支持多个 Gateway 实例共同消费任务。
- 不把 Memory 提取任务作为必须交付的业务事务。
- 不为任务增加 Web 管理页面。
- 不引入新的第三方运行时依赖。
- Gateway 重启后不恢复尚未到期的内存任务；用户下一次完成 Turn 后会重新调度。Memory 检查点仍防止已经审查的 Turn 被重复处理。

## 4. 总体结构

新增 `MemoryExtractionScheduler`：

```text
WebRuntime
  -> scheduler.schedule(actor, session_id, due_at)
       -> active_jobs[(actor_key, session_id)] = latest job
       -> min-heap stores due order
       -> Condition wakes one coordinator thread

coordinator thread
  -> waits for nearest due_at
  -> checks global worker capacity
  -> checks actor_key is not running
  -> submits to bounded ThreadPoolExecutor

worker pool, max_workers=2
  -> WebRuntime extraction callback
  -> one authorized Session read
  -> one bounded LLM extraction
  -> actor-scoped Memory write and notification
  -> completion callback releases actor_key
```

## 5. 任务身份与合并

任务键固定为：

```python
MemoryExtractionJobKey = tuple[str, str]
# (actor_key, session_id)
```

`actor_key` 沿用 `_active_turn_key()` 的稳定身份：数据库用户使用内部 `user_id`；无数据库身份的本地操作者使用 actor type 与 username。

每个任务包含：

```python
@dataclass(frozen=True)
class MemoryExtractionJob:
    key: MemoryExtractionJobKey
    actor: ActorContext
    session_id: str
    due_at: float
    generation: int
    attempt: int = 0
```

调度规则：

- 同一 key 再次 `schedule()` 时覆盖 `active_jobs` 中的旧任务，生成更大的 generation。
- heap 中允许短暂存在旧节点；弹出时 generation 与 `active_jobs` 不一致就丢弃。
- heap 长度大于 `active_jobs * 2 + 100` 时执行一次压缩，避免高频 Turn 留下过多失效节点。
- 不为每次重排创建线程。

## 6. 并发边界

默认参数：

```text
idle_seconds = 300
max_workers = 2
max_inflight_per_actor = 1
max_pending_jobs = 1000
max_retries = 2
retry_delays_seconds = [30, 120]
```

### 6.1 全局并发

`ThreadPoolExecutor(max_workers=2)` 保证整个 Gateway 同时最多执行两个 Memory 提取任务。即使一千个 Session 同时到期，也只会有两个 LLM 调用进入执行，其余任务继续留在调度器中。

### 6.2 用户级串行

调度器维护：

```python
running_actor_keys: set[str]
```

如果某用户已有任务执行，属于该用户的其它到期任务继续等待；其它用户仍可使用剩余 Worker。这样可以保证同一用户的：

- `MEMORY.md` 修改；
- 重复检查；
- `pending_notification.json` 合并；
- 用户级提取顺序；

不会并发发生。

Owner 和 CLI 是同一个 workspace operator，应映射为同一个 actor concurrency key，不能因为入口不同而并发写 workspace Memory。

### 6.3 队列上限

`active_jobs` 最多保留 1000 个唯一 Session 任务。达到上限时：

- 已存在 key 仍允许更新到期时间，因为不会增加任务数量。
- 新 key 不进入队列，记录一次 `memory.extraction.queue_full` Warning。
- 不影响聊天；该 Session 下一次 Turn 完成后可以再次尝试调度。

本地部署默认不会接近该上限，它只用于防止异常客户端无限创建 Session 导致进程内存持续增长。

## 7. 到期与公平性

调度器优先处理 `due_at` 最早的任务，但必须同时满足：

- Worker 有空位；
- 对应 actor 当前没有运行任务；
- job generation 仍是最新；
- job 没有被取消。

如果最早任务的 actor 正在运行，调度器不能阻塞其它用户。它应继续查找当前可运行的其它到期任务，并把同 actor 任务留在待处理集合中。

同一用户多个 Session 按到期顺序串行，不把它们合并为一次跨 Session LLM 调用。原因是检查点、证据来源和 Session 权限仍然以单个 Session 为边界。

## 8. 失败与重试

提取错误需要区分：

### 8.1 可重试

- Provider 超时。
- Provider 限流。
- 临时网络错误。
- Provider 暂时不可用。

最多重试两次，延迟 30 秒和 120 秒。重试仍受全局并发和用户级串行限制，不在 Worker 内 `sleep()`。

### 8.2 不重试

- LLM 返回格式不合法。
- 没有高可信候选。
- 证据不足或原文不匹配。
- MemorySafetyPolicy 拒绝。
- Session 少于三个用户 Turn。
- Session 已无新增 Turn。
- Session 已删除或 actor 不再拥有该 Session。

Extractor 应保留稳定错误类型，不能把所有错误统一包装成同一个 `MEMORY_EXTRACTION_FAILED`，否则调度器无法判断是否重试。

## 9. 生命周期

### 9.1 Turn 完成

```text
turn done
  -> scheduler.schedule(actor, session_id, now + 300s)
  -> 立即返回用户响应
```

### 9.2 新 Turn

```text
new turn starts
  -> scheduler.cancel(actor, session_id)
  -> turn runs
  -> successful completion schedules a fresh due_at
```

### 9.3 Session 清空或删除

调用 `scheduler.cancel(actor, session_id)`。已经进入 Worker 的 LLM 请求不强制中断，但结果落盘前必须再次确认 Session 仍存在且 actor 仍有访问权；验证失败则丢弃结果。

### 9.4 Gateway 关闭

```text
Gateway lifespan shutdown
  -> scheduler.stop_accepting()
  -> coordinator exits
  -> cancel not-started futures
  -> wait for running extraction up to provider timeout boundary
```

不把后台任务异常抛回 FastAPI shutdown。

## 10. 日志与可观测性

正常等待、重排和取消不写 INFO，避免重新制造日志噪声。

保留事件：

```text
memory.extraction.done       DEBUG when added_count=0, INFO when added_count>0
memory.extraction.error      ERROR
memory.extraction.retry      WARNING
memory.extraction.queue_full WARNING
memory.scheduler.start       DEBUG
memory.scheduler.stop        DEBUG
```

字段只保留：

- actor/user 安全标识；
- session_id；
- attempt；
- queue_size；
- added_count；
- duration_ms；
- 安全错误码。

不记录提取内容、用户原文、证据 quote 或完整 LLM 返回。

## 11. 模块设计

### 11.1 `agent/memory/scheduler.py`

新增纯标准库调度器，负责：

- Condition + monotonic clock；
- active job map；
- due heap；
- generation 合并；
- bounded executor；
- actor 级串行；
- retry 重排；
- shutdown。

Scheduler 不 import FastAPI、SessionStore、MarkdownMemoryStore 或具体 Provider。实际提取通过构造时注入的 callback 完成。

### 11.2 `agent/app/runtime.py`

- 删除 `_memory_timers` 和 `Timer`。
- `WebRuntime.__post_init__()` 构造或接收 Scheduler。
- Turn 完成调用 `scheduler.schedule()`。
- 新 Turn、reset、delete 调用 `scheduler.cancel()`。
- `_run_memory_extraction()` 保留 actor/session 解析和授权检查，作为 Scheduler callback。
- `shutdown()` 委托 Scheduler 关闭。

### 11.3 `agent/memory/extraction.py`

- 区分 Provider 失败、格式失败和无需提取。
- 同一用户任务已经串行后，通知文件仍使用原子替换。
- Memory 条目去重、安全检查和 Session 检查点继续由现有服务负责。

## 12. 数据流

```text
many users / many sessions
  -> schedule or reschedule lightweight jobs
  -> one coordinator thread
  -> due jobs
  -> global max 2 workers
       -> user001/session-A
       -> user002/session-X
  -> user001/session-B waits until session-A finishes
  -> result writes actor-scoped Memory
  -> completion releases user slot
  -> coordinator schedules next eligible job
```

## 13. 变更文件

- `agent/memory/scheduler.py`
- `agent/memory/extraction.py`
- `agent/app/runtime.py`
- `agent/app/gateway.py`
- `agent/protocols/memory.py`，如需稳定 job/result 类型。
- `tests/unit_test/memory/test_scheduler.py`
- `tests/unit_test/memory/test_extraction.py`
- `tests/unit_test/app/test_runtime_commands.py`
- `tests/unit_test/auth/test_web_runtime_auth.py`
- Part 10 活文档、总体设计和测试说明。

## 14. 测试方案

- 一千个待处理 Session 只创建一个 coordinator 和两个 Worker，不创建一千个 Timer。
- 同一 Session 连续调度十次只执行最后 generation。
- 两个不同用户可以并行执行。
- 同一用户两个 Session 永不并行。
- 某用户任务阻塞时，不影响其它用户获得空闲 Worker。
- 全局执行数永远不超过 `max_workers`。
- 取消未执行任务后 callback 不运行。
- reset/delete 取消对应任务。
- Provider 临时错误按 30/120 秒重排，不占用 Worker sleep。
- 格式错误、证据不足和安全拒绝不重试。
- 队列达到上限后更新已有 key 仍成功，新 key 被安全拒绝调度。
- shutdown 后不接受新任务，待处理任务被取消。
- 同一用户两个 Session 完成后，Memory 和通知内容都不丢失。

## 15. 验收标准

1. 代码中不再使用一 Session 一个 `threading.Timer`。
2. 任意等待任务数量下，调度线程数量保持常量。
3. 默认全局最多两个提取 LLM 调用。
4. 同一用户最多一个提取任务执行。
5. Session 重排、取消、重试和 shutdown 都有确定行为。
6. 后台调度不增加普通 Turn 延迟。
7. 不引入第三方任务系统或新的运行时服务。
8. Memory、通知和检查点在并发测试中不丢失、不重复。
9. 日志不记录 Memory 内容或用户原文。
10. Ruff、相关单测和全量 pytest 通过。
