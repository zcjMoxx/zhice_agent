from agent.presentation import markdown_to_plain_text


def test_markdown_to_plain_text_preserves_readable_structure():
    source = """# 计算结果

**总时间：** `265` 天，~~旧值~~

- 普通项目
- [x] 已完成
- [ ] 待处理
1. 第一项
2. 第二项

> 注意事项

[日期说明](https://example.com/date)
![截图](https://example.com/image.png)

---

```python
print("ok")
```

| 名称 | 值 |
| --- | ---: |
| 天数 | 265 |
"""

    rendered = markdown_to_plain_text(source)

    assert rendered == """计算结果

总时间： 265 天，旧值

• 普通项目
☑ 已完成
☐ 待处理
1. 第一项
2. 第二项

│ 注意事项

日期说明：https://example.com/date
[图片：截图] https://example.com/image.png

────────

[代码：python]
print("ok")

名称：天数；值：265"""


def test_markdown_to_plain_text_keeps_unclosed_code_and_placeholders():
    source = "发送 /bind <绑定码>。\r\n\r\n```text\r\n**not styling**"

    rendered = markdown_to_plain_text(source)

    assert rendered == "发送 /bind <绑定码>。\n\n[代码：text]\n**not styling**"


def test_markdown_to_plain_text_is_empty_safe_and_plain_text_idempotent():
    assert markdown_to_plain_text("") == ""
    assert markdown_to_plain_text("普通文本。\n\n下一段。") == "普通文本。\n\n下一段。"
