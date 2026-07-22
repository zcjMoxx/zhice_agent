# ZhiCe-Agent Web 公式渲染与 Init 短文案设计

> 日期：2026-07-22
>
> 状态：已实现并进入当前代码基线

## 1. 背景

`zcagent init` 当前完成提示包含 endpoint 字段、token budget 默认值和扩展能力说明，信息正确但不适合作为一次性终端提示；详细配置语义已经由 README 承担。

Web 当前使用自研最小 Markdown DOM renderer，只支持标题、列表、代码块、粗体、行内代码和安全链接，没有 LaTeX renderer。`$...$`、`\(...\)`、`\bm{...}` 等公式会按普通文本显示。

## 2. 目标

1. Init 完成提示压缩为“LLM 必需、扩展可选”两句。
2. Web assistant Markdown 支持常见行内和块级 LaTeX。
3. 支持用户提到的 `\bm{...}`，映射为 KaTeX `\boldsymbol{...}`。
4. 数学渲染依赖加载失败时不阻断页面或聊天，保留原始公式文本。
5. 代码块和行内代码中的 `$`、反斜杠内容不参与公式渲染。

## 3. 方案

前端页面不引入构建链。`app.js` 在页面正常启动后异步加载固定版本 KaTeX CSS、核心脚本和 auto-render 扩展：

```text
local app starts
-> load KaTeX CSS/script asynchronously
-> load auto-render after core
-> rerender current messages once
-> each later assistant bubble calls renderMathInElement
```

支持分隔符：

- inline：`$...$`、`\(...\)`；
- display：`$$...$$`、`\[...\]`。

安全配置：

- `trust: false`；
- `throwOnError: false`；
- 忽略 `pre/code/script/style/textarea`；
- 仅在 assistant Markdown bubble 内运行；
- CDN 失败静默回退原始文本，不影响本地 JS 启动。

## 4. 变更文件

- `agent/cli.py`：缩短 init 完成提示。
- `web/static/app.js`：异步加载并调用 KaTeX。
- `web/static/styles.css`：块级公式横向滚动和间距。
- `web/static/index.html`：更新 app/styles 静态资源版本，避免已有浏览器继续命中公式支持上线前的缓存文件。
- CLI、前端静态测试与测试说明。
- README、Web 活文档和设计索引。

## 5. 测试

- Init 输出不再包含字段级长说明。
- 前端脚本固定 KaTeX 版本并配置四类分隔符、`\bm` macro、`trust=false` 和 code/pre 忽略规则。
- KaTeX 未加载时 `renderMath` 安全 no-op。
- `node --check`、Ruff、pytest 和 diff check 通过。

## 6. 验证结果

- Init 短文案与 KaTeX 静态配置专项测试：`47 passed`。
- `python -m ruff check .` 通过。
- `python -m pytest --basetemp .tmp/pytest_web_math_init_copy_full`：`616 passed, 1 skipped`。
- 两个前端 JavaScript 文件的 `node --check` 与 `git diff --check` 通过。
