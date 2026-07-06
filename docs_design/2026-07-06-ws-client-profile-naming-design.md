# WebSocket client/profile naming design

## Background

The WebSocket entrypoint is one transport: `/ws`. The previous names mixed three concepts:

- `browser`: an implementation detail of the built-in frontend.
- `external`: a client type.
- `external_ws`: an internal command channel name that leaked into tests and responses.

This made it unclear how a non-browser UI or third-party client should connect.

## Goal

Use one small vocabulary for the public WS handshake and the internal runtime command profile.

## Naming Rule

- Transport is always `/ws`; do not encode transport into client/profile names.
- Public hello frame uses `client`.
- Supported client values are only `web` and `external`.
- `web` means a normal UI client with conservative command permissions. It is not limited to a browser; the built-in frontend, Electron, a mobile shell, or another Web UI should all use `web`.
- `external` means an automation, CLI, script, or service client that needs connection-level commands.
- Runtime receives a `command_profile` with the same values: `web` or `external`.
- Omitted `client` defaults to `web`.
- Unknown explicit `client` values return `INVALID_REQUEST`.

## Protocol

Built-in or third-party UI:

```json
{"type":"hello","client":"web"}
```

Automation/CLI/script:

```json
{"type":"hello","client":"external"}
```

Hello response:

```json
{
  "event": "hello",
  "data": {
    "client": "web",
    "command_profile": "web",
    "capabilities": {
      "history_command": false,
      "exit_command": false
    }
  }
}
```

## Capability Matrix

| Command | web | external |
|---|---:|---:|
| `/help` | yes | yes |
| `/model...` | yes | yes |
| `/stop` | yes | yes |
| `/reset` | yes | yes |
| `/sessions...` | yes | yes |
| `/history` | no | yes |
| `/exit` | no | yes |

## Change Scope

- Rename runtime constants from browser/external_ws channel names to web/external command profiles.
- Change the built-in frontend hello frame to `client="web"`.
- Change WS hello responses from `command_channel` to `command_profile`.
- Keep omitted hello client as `web`.
- Reject unknown explicit hello client values.
- Update focused WS/runtime tests and the app test case note.

## Acceptance

- Built-in frontend connects with `client="web"`.
- A third-party UI can connect with `client="web"` and gets conservative capabilities.
- An external automation client can connect with `client="external"` and use `/history` and `/exit`.
- No public response or test expectation uses `browser` or `external_ws` as the command profile.
