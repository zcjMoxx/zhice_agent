# QQ 返回入口与本地用户删除设计

## 背景

QQ 移动绑定成功页只提供进入 ZhiCe-Agent 的入口，用户无法显式返回 QQ。管理后台创建用户表单又会被 Edge 密码管理器误判成登录表单，把当前 Owner 用户名和密码自动填入显示名称与初始密码。当前用户管理只有停用，没有物理删除；直接删除 `users` 行会遗留或破坏 Session、渠道身份、运行记录和用户文件。

## 目标

1. QQ 绑定成功后提供“关闭并返回 QQ”主操作，并保留进入 ZhiCe-Agent 的备用入口。
2. 创建用户表单不再复用当前登录凭据，用户名、显示名称和初始密码始终由管理员显式输入。
3. 为本地普通账号提供有界的永久删除流程。

## 范围边界

- Owner 永久保护，不能删除。
- 只能删除已停用账号，避免删除过程中继续产生 Turn 或 Session 写入。
- 删除前必须输入目标用户名进行二次确认。
- 仍存在微信 `channel_accounts` 的账号拒绝删除，要求先恢复账号并完成微信解绑，避免旁路删除凭据文件和 sidecar 运行态。
- QQ `external_identities` 随用户删除；账号独立目录中的 Session、Memory、文件和派生索引整体删除。
- 安全审计保留最终 `user.deleted` 管理事件，但删除目标账号此前作为操作者或资源的审计记录及运行详情，避免保留已删除账号的用户数据。
- 不删除 Owner/CLI 的全局 workspace、全局 Session 或全局 Memory。

## 模块设计

### QQ 返回

`QqBindingPage.vue` 在成功态将“关闭并返回 QQ”作为主按钮。点击后先调用 `window.close()`；浏览器不允许脚本关闭当前窗口时，再尝试 `history.back()`。页面仍可见时展示“请使用右上角关闭返回 QQ”的降级提示。“进入 ZhiCe-Agent”保留为次操作。

### 创建用户表单

表单及三个输入框声明独立字段名与 autocomplete 语义：用户名、显示名称关闭当前账号自动填充，初始密码使用 `new-password`。创建成功后仍清空本地 reactive state。

### 用户删除

新增 `DELETE /api/admin/users/{user_id}`，请求体必须包含目标用户名确认值。路由继续要求 `auth.users.manage`，并额外限制操作者必须是 Owner。

删除数据分两部分：

1. 文件系统先把 `${contexts}/users/{user_id}` 原子移动到同级隔离临时目录；数据库失败时移回原目录。
2. SQLite 在 `BEGIN IMMEDIATE` 事务中按外键顺序删除认证 Session、QQ 身份与 token、Session route/index、Turn/Tool/confirmation、用户审计数据、角色与权限，最后删除 `users` 行。事务成功后清理隔离目录。

数据库删除前检查目标仍存在、不是 Owner、状态为 `disabled`，并且没有 `channel_accounts`。所有校验都在写事务中重复执行，防止并发状态变化绕过。

## 数据流

```text
Owner 点击删除
  -> 输入目标用户名确认
  -> DELETE /api/admin/users/{id}
  -> 校验 Owner + target disabled + 无微信账号
  -> 隔离 contexts/users/{id}
  -> SQLite 原子删除用户关联数据
  -> 清理隔离目录
  -> 写入 user.deleted 审计
  -> 刷新用户列表
```

## 变更文件

- `web/frontend/src/pages/QqBindingPage.vue`
- `web/frontend/src/pages/QqBindingPage.test.ts`
- `web/frontend/src/layouts/AdminLayout.vue`
- `web/frontend/src/layouts/AdminLayout.test.ts`
- `web/frontend/src/api/client.ts`
- `agent/app/api/schemas.py`
- `agent/app/api/routes.py`
- `agent/app/auth.py`
- `agent/auth/store.py`
- `agent/auth/user_context.py`
- 对应测试说明与当前活文档

## 测试方案

- QQ 成功态包含两个入口，关闭失败时出现明确降级提示。
- 创建用户输入框使用正确 autocomplete，提交值不包含浏览器当前账号。
- 删除普通已停用用户后，数据库关联记录和用户目录均消失。
- active 用户、Owner、未匹配用户名、仍绑定微信和非 Owner 管理员删除均失败。
- 数据库删除失败时恢复隔离的用户目录。

## 验收标准

- 手机 QQ 内点击主按钮能够返回上一页或关闭 WebView；不支持关闭的浏览器给出可执行提示。
- Edge 不再自动把当前 Owner 凭据填入创建用户表单。
- 用户列表对已停用非 Owner 账号显示“永久删除”，且必须经过用户名确认。
- 删除后不能登录、不能解析原 QQ 身份、不能看到原 Session，用户隔离目录不再存在。
