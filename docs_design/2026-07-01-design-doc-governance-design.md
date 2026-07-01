# 设计文档治理与 Part 文档命名调整设计

> 关联规范：`AGENTS.md`
>
> 当前状态：已落地。本文记录本次文档中修的背景和命名规则调整。

## 1. 背景

`docs_design/` 中同时存在两类文档：

- 总体设计和 Part 文档，承担当前阶段准则和新人阅读入口。
- 带日期的专题设计，记录某次具体设计和实现的演进过程。

此前 Part 文档也带日期，例如 `2026-06-09-zhice-agent-part1-foundation-design.md`。这会让读者误以为 Part 文档也是一次性历史记录，不确定它应不应该按当前代码维护。

## 2. 目标

1. 明确无日期文档是当前活文档。
2. 明确带日期文档是当次设计记录。
3. 将 Part 文档改为无日期文件名。
4. 新增 `docs_design/README.md`，作为设计文档阅读入口和维护规则。
5. 保留日期设计记录的历史口吻，不回头重写旧方案。

## 3. 非目标

- 不合并所有历史设计文档。
- 不把日期设计记录改写成当前实现说明。
- 不删除旧方案中的历史字段或当时的权衡过程。
- 不改变代码行为。

## 4. 命名规则

当前活文档使用无日期文件名：

```text
zhice-agent-overall-design.md
zhice-agent-part1-foundation-design.md
zhice-agent-part2-no-tool-chat-design.md
zhice-agent-part3-tool-calling-design.md
zhice-agent-part4-exec-tool-design.md
zhice-agent-part5-skill-loader-design.md
```

日期设计记录继续使用：

```text
YYYY-MM-DD-{topic}-design.md
```

## 5. 变更文件

- 新增 `docs_design/README.md`
- 新增 `docs_design/2026-07-01-design-doc-governance-design.md`
- 重命名 Part 文档为无日期文件名
- 更新 Part 文档之间的承接链接
- 更新 `AGENTS.md` 中的设计先行规则
- 更新 `README.md` 中的设计文档入口说明

## 6. 维护规则

- 当前活文档应随着代码和阶段边界保持最新。
- 日期设计记录完成并落地后原则上冻结。
- 后续变化新增日期设计记录，在背景里说明旧方案不足和本次改进。
- 如果后续设计已经改变了旧日期设计记录的方案，只在旧文档标题下方补一段 `> 说明：...`，说明当前代码采用什么、旧方案哪里不再适用、应参考哪份新文档或当前活文档；旧文档正文保持当时方案原貌。
- 日期设计记录和当前代码冲突时，以当前活文档和当前代码为准。
- 允许对日期设计记录做不改变方案含义的维护，例如链接修复、错别字和排版修正。

## 7. 验收标准

- `docs_design/README.md` 能说明当前阅读顺序和维护规则。
- Part 文档文件名不再带日期。
- 仓库内不再引用旧的 Part 文档日期文件名。
- 历史日期文档仍保留，不被合并或改写。
