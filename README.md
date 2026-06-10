# ZC-Agent

ZC-Agent is a lightweight local Agent project built step by step.

The first development stage provides the runnable foundation:

- project configuration
- Markdown prompt loading
- common message models
- JSONL session persistence
- a minimal CLI entrypoint

## Quick Start

```bash
python -m pip install -e .
zcagent --session default
```

Type `/exit` to leave the CLI.

## Tests

```bash
python -m pytest
```
