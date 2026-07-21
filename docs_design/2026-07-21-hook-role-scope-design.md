# 2026-07-21 Hook 角色作用域设计

> 状态：角色与有效权限豁免均已实现并通过测试；Part 12 保持关闭
>
> 当前活文档：`docs_design/zhice-agent-part12-hooks-design.md`
>
> 承接：`docs_design/2026-07-20-hook-runtime-boundary-design.md`

## 1. 背景

Part 12 已完成真实 pre/post Tool Hook Runtime。当前 Hook 按 stage 和 Tool name 匹配，对 owner、admin 和普通角色默认一视同仁；pre Hook 已能读取 `actor_type` 与 `role_keys`，但常见的整 Hook 角色豁免只能由每个脚本重复实现，容易产生不一致。

本次确认：Hook 仍是核心 RBAC 之外的额外业务限制，不让 owner/admin 自动绕过全部 Hook；每个 Hook 可以在配置中显式声明 `exempt_roles` 和 `exempt_permissions`。owner 可以按 Hook 整体豁免；admin 不按角色整体豁免，而是根据 `ActorContext.permission_keys` 中已经生效的角色权限与直接授权做细粒度豁免。未来对 owner 权限的整体调整属于独立权限设计，不在本次修改。

## 2. 目标

1. Hook 默认继续对所有角色生效，兼容现有配置并保持安全默认值。
2. pre/post Hook 都可以通过 `exempt_roles` 或 `exempt_permissions` 显式跳过指定身份。
3. 角色豁免只跳过当前 Hook，不能跳过 Tool schema、RBAC、危险确认、workspace guard、timeout、脱敏、SSRF 或具体 Tool 安全检查。
4. 角色豁免产生不含完整参数的稳定 `hook.skipped` 诊断日志。

## 3. 配置与校验

```yaml
version: 1
hooks:
  - name: restrict-exec
    stage: pre_tooluse
    script: extends/hooks/restrict_exec.py
    tools: [exec]
    exempt_roles: [owner]
    exempt_permissions: [tool.exec.dangerous]
    timeout_seconds: 2
    max_output_chars: 16384
```

规则：

- 省略 `exempt_roles` 或配置空列表表示无豁免，Hook 对所有角色生效。
- `exempt_roles` 必须是字符串列表，去重后保存；角色 key 使用与 RBAC 一致的安全标识格式。
- 省略 `exempt_permissions` 或配置空列表表示不按权限豁免；配置项与 `ActorContext.permission_keys` 精确匹配，支持角色继承权限和 owner 授予的直接权限。
- admin 不因角色名自动豁免；只有命中当前 Hook 显式声明的有效权限才跳过。默认 admin 没有 `tool.exec.dangerous`、`skill.sync` 等权限，因此不会获得相应豁免。
- 未认证或无角色/权限上下文时按空集合处理，不获得豁免。
- 配置角色只控制 Hook 是否运行，不形成新的 RBAC allow decision。

## 4. 运行链

```text
Tool schema 初验
  -> 选择 stage + tool 匹配的 Hook
  -> role_keys 命中 exempt_roles，或 permission_keys 命中 exempt_permissions？
       是：记录 hook.skipped 与命中类型，继续下一个 Hook
       否：运行 Hook
  -> pre 修改后再次 Tool schema 校验
  -> 核心 RBAC / 危险确认 / Tool guard
  -> ToolResult
  -> post Hook 使用相同角色豁免语义生成受限展示
```

多个 Hook 独立判断。例如 owner 可以跳过业务目录 Hook，但仍执行无豁免的敏感数据治理 Hook。

## 5. 模块变更

```text
agent/hooks/config.py
agent/hooks/loader.py
agent/hooks/runtime.py
agent/protocols/hook.py
agent/core/loop.py
config/hooks.example.yml
tests/unit_test/hooks/
tests/unit_test/agent_loop/
docs_design/zhice-agent-part12-hooks-design.md
docs_design/README.md
```

## 6. 测试方案

- 配置加载：角色/权限的缺省、空列表、去重、非法类型和非法 key。
- pre Hook：owner 显式豁免时不启动 Runner；admin 未豁免时仍执行；无角色时仍执行。
- admin 权限：命中 `exempt_permissions` 时跳过，缺少该权限时仍执行；直接授予权限与角色继承权限使用同一有效集合。
- 多 Hook：只跳过命中的 Hook，后续无豁免 Hook 继续执行。
- post Hook：使用同一角色豁免语义；跳过不改变真实 ToolResult。
- AgentLoop：Hook 豁免后核心 RBAC 与危险确认仍按原链执行。
- 回归：无 `exempt_roles` 的现有 Hook 行为不变。

## 7. 验收标准

1. owner/admin 没有全局自动 Hook 豁免。
2. `exempt_roles` 与 `exempt_permissions` 只在显式配置的单个 Hook 上生效。
3. 豁免仅跳过 Hook Runner，不降低任何核心安全判断。
4. pre/post、无角色、多 Hook 和非法配置测试通过。
5. ruff、相关测试和全量 pytest 通过，或明确记录无关历史失败。

最终验证：`python -m ruff check .` 与两个前端脚本的 `node --check` 通过；角色/权限作用域与 Tool policy 专项测试 49 passed；全量 `python -m pytest -rs --basetemp .tmp/pytest_hook_permission_scope_full` 为 500 passed、1 skipped，跳过项是当前 Windows 环境不支持创建 symlink 的既有只读工具用例。
