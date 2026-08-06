# QQ 公网绑定链接部署修复

## 背景

QQ 私聊用户发送裸 `/bind` 时，`agent/channels/qq/adapter.py` 直接使用当前账号的 `account.web_base_url` 拼接 `/?channel_bind=<token>`。`agent/channels/config.py` 为未显式配置的账号保留本地开发默认值 `http://127.0.0.1:10086`。

云部署使用的 Git 忽略文件 `deploy/private/config.yml` 此前没有为 QQ `main` 账号声明 `web_base_url`，因此云端 QQ Adapter 继承了 loopback 默认值，向远端 QQ 用户返回其手机无法访问的 `127.0.0.1` 链接。这里不是 Adapter 路由算法错误，而是部署账号缺少环境相关的显式公网配置。

本地与云端若同时运行同一 QQ Bot 账号，还会产生两个 WebSocket 消费实例竞争事件、重复回复或连接抖动风险。当前本地 Compose 已停止，QQ 生产实例只保留云端单实例；本次不引入多实例选主。

## 目标

- 云端 QQ `main` 账号的裸 `/bind` 链接固定生成 `https://agent.zouzhou.xyz/?channel_bind=<token>`。
- 公网启用 QQ 时，每个账号都在其实际部署配置中显式声明 `web_base_url`。
- 保留本地未配置时默认 `http://127.0.0.1:10086` 的既有语义。
- 文档、公开示例和回归测试明确区分本地默认值与公网部署值。

## 范围边界

- 不修改 `QQAccountConfig.web_base_url` 的本地默认值。
- 不新增全局 Public URL 配置，也不让 Adapter 读取 Part 17 的 `cloud-target.json`。
- 不改变 token 创建、hash 存储、有效期、单次消费或身份绑定协议。
- 不启动本地 Compose，不连接或部署云端；部署在后续显式验收中完成。
- 不提交或输出 `deploy/private/config.yml` 及其中任何 Secret。

## 模块设计与数据流

配置继续保持账号级：

```text
deploy/private/config.yml
  -> channels.qq.accounts[main].web_base_url
  -> load_channel_configuration()
  -> QQAccountConfig.web_base_url
  -> QQChannelAdapter 裸 /bind
  -> https://agent.zouzhou.xyz/?channel_bind=<opaque-token>
```

`config/config.example.yml` 只用注释和有效 URL 示例说明配置方法，不把中文占位字符串放入 YAML 值，避免用户复制后被当作有效 URL。云发布前检查 `deploy/private/config.yml` 中每个启用的 QQ 账号是否显式配置真实 HTTPS `web_base_url`。

## 变更文件

- `docs_design/2026-08-04-qq-public-binding-url-deployment-fix.md`
- `deploy/private/config.yml`（Git 忽略，只定点增加非 Secret URL）
- `config/config.example.yml`
- `deploy/README.md`
- `docs_design/zhice-agent-part14-external-channel-design.md`
- `docs_design/zhice-agent-part17-reliability-diagnostics-deployment-design.md`
- `docs_design/README.md`
- `tests/unit_test/channels/test_channels.py`
- `tests/unit_test/channels/test_case.md`
- `tests/unit_test/deploy/test_deploy_assets.py`

## 测试方案

- 加载带显式 HTTPS `web_base_url` 的 QQ 账号配置并断言原值保留。
- 保留一个未配置账号的 loopback 默认值断言，防止把本地默认语义改成生产域名。
- 使用显式公网账号触发私聊裸 `/bind`，验证 Markdown 链接和 URL 按钮均以 `https://agent.zouzhou.xyz/?channel_bind=` 开头。
- Deploy 静态测试只读取公开 README，验证云发布前检查口径；禁止读取真实 `deploy/private/config.yml`。
- 运行 channels/deploy 相关 Pytest、Ruff 和 `git diff --check`。

## 验收标准

- 云端私有配置的 QQ `main` 账号显式具有 `web_base_url: https://agent.zouzhou.xyz`，且任何 Secret 不被输出。
- 公网 QQ 裸 `/bind` 回归测试生成正确 HTTPS 域名链接。
- 未显式配置的本地账号仍使用 `http://127.0.0.1:10086`。
- 公开示例与部署 README 能阻止再次漏配账号级公网绑定地址。
