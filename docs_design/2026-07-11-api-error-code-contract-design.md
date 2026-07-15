# API 业务错误码与 HTTP 错误响应契约设计

## 背景

当前 HTTP API 使用 `{error: {code, message}}`，但 `FORBIDDEN`、`INVALID_REQUEST` 等 HTTP 大类被同时当成业务错误码。客户端无法稳定区分“未委派管理员管理权”“Owner 受保护”“参数缺失”等不同场景，只能解析 message。

本方案参考 RFC 9457、Google AIP-193、Microsoft Azure API Guidelines、Stripe 和 GitHub 的错误模型，明确区分 HTTP 状态与业务原因。

## 目标

- HTTP 状态继续通过真实响应状态表达，并在 body 中以数字 `status` 重复提供。
- `code` 使用稳定、领域化、可编程判断的 `UPPER_SNAKE_CASE` 业务码。
- `message` 是可读提示，不作为客户端判断依据。
- `request_id` 关联 HTTP、audit 和 trace。
- `details` 承载权限 key、字段名等动态上下文，客户端不解析 message。
- 前端收到 401/403 后按数字状态刷新登录态和权限。

## 响应结构

```json
{
  "error": {
    "status": 403,
    "code": "AUTH_ADMIN_MANAGEMENT_NOT_DELEGATED",
    "message": "Administrator management is not delegated",
    "request_id": "req-...",
    "details": {
      "required_permission": "auth.admin.manage"
    }
  }
}
```

规则：

- `status` 必须与真实 HTTP status 相同。
- `code` 一经对外使用，不改变原有语义，也不复用于另一种错误。
- `details` 只允许安全结构化字段，不放 secret、完整参数或堆栈。
- HTTP 以外的 WebSocket/SSE 事件继续携带业务 `code`，并在可确定时增加 `status`。

## 命名规则

```text
{DOMAIN}_{RESOURCE_OR_ACTION}_{REASON}
```

首批领域：`REQUEST`、`AUTH`、`USER`、`SESSION`、`MODEL`、`TOOL`、`CHAT`、`CONFIG`、`LLM`、`INTERNAL`。

首批迁移：

| 旧码 | 新码 |
|---|---|
| `INVALID_REQUEST` | `REQUEST_VALIDATION_FAILED` |
| `FORBIDDEN` + 通用权限拒绝 | `AUTH_PERMISSION_DENIED` |
| `FORBIDDEN` + 未委派管理员管理 | `AUTH_ADMIN_MANAGEMENT_NOT_DELEGATED` |
| `FORBIDDEN` + Owner 受保护 | `AUTH_OWNER_ACCOUNT_PROTECTED` |
| `FORBIDDEN` + 禁止分配 Owner | `AUTH_OWNER_ROLE_ASSIGNMENT_FORBIDDEN` |
| `INVALID_CREDENTIALS` | `AUTH_INVALID_CREDENTIALS` |
| `INVALID_SETUP_CREDENTIAL` | `AUTH_INVALID_SETUP_CREDENTIAL` |
| `USERNAME_ALREADY_EXISTS` | `USER_USERNAME_ALREADY_EXISTS` |
| Session `NOT_FOUND` | `SESSION_NOT_FOUND` |
| Session `CONFLICT` | `SESSION_ID_CONFLICT` |
| `CONFIG_ERROR` | `CONFIG_INVALID` |

## 模块设计

- `agent/protocols/errors.py`：定义跨层稳定错误码，auth/core 不反向依赖 FastAPI。
- `AuthHttpError`、`SessionAccessError`、`ApiError`：增加安全 `details`。
- Gateway：统一构造错误 body，写入 `status/code/message/request_id/details`。
- API 映射：按异常领域映射业务码，参数校验统一为 `REQUEST_VALIDATION_FAILED`。
- 前端：只根据 HTTP status 和业务 code 判断，不解析 message。

## 测试方案

- 403 未委派管理员管理权返回数字 403 和专用业务码。
- 403 通用权限拒绝包含 `required_permission` details。
- 400 请求校验返回 `REQUEST_VALIDATION_FAILED`。
- 401 登录失败返回 `AUTH_INVALID_CREDENTIALS`。
- Session 不存在与冲突返回独立业务码。
- body `status` 与真实 HTTP status 一致，`request_id` 与响应头一致。
- 前端仍能从新结构读取 message/code，并按 401/403 刷新权限。

## 验收标准

1. 新 HTTP 错误响应不再只有 `{code, message}`。
2. 明确业务场景不再返回裸 `FORBIDDEN` 或 `INVALID_REQUEST`。
3. 客户端无需解析 message 即可决定刷新权限、定位字段或展示提示。
4. 现有成功响应、会话 JSONL 和数据库 schema 不变。
