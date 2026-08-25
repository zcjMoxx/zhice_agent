# 注册账号占用预检与多域名备案验收设计

## 背景

注册页当前只校验账号格式，并把“格式合法”显示为“可用”；账号实际已存在时，只有提交后才由后端返回英文错误与内部错误码。云端公安备案配置同时只允许 `chat.zouzhou.xyz`，导致指向同一服务的正式域名 `agent.zouzhou.xyz` 无法获得备案展示数据。部署外部烟测中的 12306 请求还沿用旧版中文站名参数，与当前 MCP 的车站代码参数不一致。

## 目标

- 注册账号格式合法后，匿名预检账号是否已占用，并在提交前显示“可用”“已存在”或“检查中”。
- 认证页面向用户的错误不展示内部错误码，常见注册和登录失败使用明确中文文案。
- `agent.zouzhou.xyz` 与 `chat.zouzhou.xyz` 都能展示同一份 private 公安备案信息。
- 12306 部署烟测按当前工具 schema 使用车站代码，避免把参数过期误报为集成故障。

## 范围边界

- 账号占用接口只返回布尔可用性，不返回用户 ID、状态、角色或其他账号资料。
- 前端预检用于交互反馈，最终注册仍以数据库唯一约束为准，避免并发竞态绕过。
- 预检网络失败时允许用户继续提交，由注册接口给出最终结果。
- 备案真实编号仍只保存在已忽略的本地和 `deploy/private` 运行配置中，仓库示例不写入真实信息。
- 外部集成失败仍为非回滚告警；本次只修正 12306 烟测参数，不改变发布回滚边界。

## 模块设计

### 注册可用性接口

- 新增匿名 `GET /api/auth/username-availability?username=...`。
- 响应只包含 `available`；账号格式非法时返回 `available=false`，不暴露内部校验文案。
- 接口只读查询，注册提交继续依赖现有唯一索引和 409 错误。

### 注册页交互

- 账号格式合法后进行短延迟预检，并用请求序号丢弃过期响应。
- 标签状态区分“需调整 / 检查中 / 可用 / 已存在”。
- 已存在时输入框显示错误态并禁用提交；预检失败时回退到最终提交校验。
- 认证相关错误由页面按稳定错误码映射为用户文案，不拼接内部标识。

### 多域名备案

- `deploy/private/config.yml` 的 `allowed_hosts` 同时加入两个正式域名。
- 本地 workspace 配置继续只允许 `localhost` 与 `127.0.0.1`。
- 部署后分别通过两个正式域名请求 `/api/site` 并用真实移动 viewport 检查备案页脚。

### 12306 烟测

- 使用北京南 `VNP` 到天津南 `TIP`，参数名改为 `fromStation`、`toStation`。
- 日期继续使用部署当天计算出的次日 ISO 日期。
- 单元测试固定断言当前参数，防止再次与 MCP schema 漂移。
- XHS 单次瞬时超时允许一次有界重试；重试恢复记为 `passed_after_retry`，持续失败仍记非回滚 warning。

## 数据流

```text
注册页账号输入
  -> 本地格式检查
  -> 匿名可用性查询
  -> 可用/已存在提示
  -> 最终注册提交与数据库唯一约束

正式域名 Host
  -> /api/site
  -> private allowed_hosts 精确匹配
  -> 备案页脚展示
```

## 变更文件

- `agent/app/api/schemas.py`
- `agent/app/api/routes.py`
- `agent/app/gateway.py`
- `web/frontend/src/api/types.ts`
- `web/frontend/src/api/client.ts`
- `web/frontend/src/layouts/AuthLayout.vue`
- `web/frontend/src/layouts/AuthLayout.test.ts`
- `deploy/scripts/deployment_smoke.py`
- `tests/unit_test/app/test_auth_routes.py`
- `tests/unit_test/deploy/test_deployment_smoke.py`
- `tests/unit_test/app/test_case.md`
- `tests/unit_test/deploy/test_case.md`
- `deploy/private/config.yml`（Git ignore）

## 测试方案

- 后端覆盖未占用、已占用、大小写等价和非法账号的匿名查询。
- 前端覆盖预检状态、已存在禁用提交、竞态结果丢弃、预检失败回退以及中文错误映射。
- 部署烟测测试断言 12306 使用当前车站代码字段。
- 运行 Ruff、后端全量 pytest、前端全量 test、typecheck、lint 和生产 build。
- 部署后在两个正式域名分别验证 `/health`、`/api/site` 与移动端认证页备案展示，并重新调用 12306/XHS。

## 验收标准

- 输入已存在账号后无需提交即可显示“已存在”，且不出现 `USER_USERNAME_ALREADY_EXISTS`。
- 注册接口竞态失败也只显示友好文案。
- 两个正式域名都返回同一合法备案对象并显示页脚。
- 12306 部署烟测真实调用成功，不再产生旧参数误告警。
- private 真实备案信息不进入 Git 跟踪文件。
