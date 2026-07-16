# 终端自适应耗时展示设计

> 状态：已确认，进入代码落地。

## 1. 目标

终端根据耗时量级自动显示 `ms/s/m/h`，减少长毫秒数字的认知成本。Trace 继续保留原始 `duration_ms` 数值，不能用字符串替代机器可计算字段。

## 2. 格式规则

```text
< 1s       -> 整数 ms
< 60s      -> s，最多两位小数，删除尾随 0
>= 60s     -> 四舍五入为整数秒，组合 m/s
>= 60m     -> 四舍五入为整数秒，组合 h/m/s
```

值为零的尾部单位省略：

```text
500ms      -> 500ms
1250ms     -> 1.25s
10500ms    -> 10.5s
60000ms    -> 1m
60500ms    -> 1m1s
200000ms   -> 3m20s
3600000ms  -> 1h
3905000ms  -> 1h5m5s
```

## 3. 边界

- 只转换终端 formatter 中的实际 `duration_ms`。
- 终端字段名统一显示为 `duration`。
- JSONL trace 继续写 `duration_ms` 原始整数。
- 不转换 timeout、interval 等配置值。
- Turn、LLM、Tool 和其它使用 `duration_ms` 的 Agent 事件复用同一函数。
- Uvicorn access log 不在本次范围。

## 4. 变更文件

- `agent/app/logging.py`
- `tests/unit_test/app/test_logging.py`
- Part 8 日志活文档和 README。

## 5. 验收标准

1. Tool 和普通 Agent 事件使用相同耗时格式。
2. 分钟级以上不显示小数秒。
3. Trace 原始数值不变。
4. Ruff、日志测试和全量测试通过。
