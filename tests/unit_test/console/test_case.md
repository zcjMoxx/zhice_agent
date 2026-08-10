# Console 单元测试用例

## 测试目标

验证终端输出辅助能力在真实 CLI 场景中保持稳定，尤其是动态 spinner 输出不会在 TTY 中留下旧帧残留，避免用户看到错乱的命令行提示。

## 用例覆盖

### Case 0: supervisor PIPE 中恢复原版 Console 配色

- 输入：受控 child 设置 `ZHICE_FORCE_TERMINAL_COLOR=1`，stdout 为 PIPE。
- 预期：地址和路径继续使用 ANSI `36` 青色；设置 `NO_COLOR` 时仍输出纯文本。
- 检查点：内部 override 只恢复原版 TTY 语义，不改变用户关闭颜色的优先级。

### Case 1: 自动配色检测不跨输出环境污染

- 输入：同一进程先通过强制配色输出，再切换到非 TTY 捕获输出。
- 预期：第二阶段重新按当前 stdout 和环境变量检测，输出不包含 ANSI。
- 检查点：`_COLOR_ENABLED` 只用于显式覆盖，自动检测结果不缓存为进程全局真值。

### Case 2: spinner 结束行清理

- 输入：spinner 动画帧比最终输出行更长。
- 预期：最终输出会清理上一帧尾部残留字符。
- 检查点：写入 TTY buffer 的内容包含足够的清行控制，最终行不会和旧动画帧混在一起。
