// ZhiCe patch: stdout belongs exclusively to NDJSON. Transport diagnostics use
// aggressively redacted stderr messages and never include response bodies.
function sanitize(value) {
  return String(value ?? "")
    .replace(/https?:\/\/[^\s]+/gi, "<url-redacted>")
    .replace(/(token|authorization|from|to|account|user_id|bot_id)[=:]\s*[^\s,}\]]+/gi, "$1=<redacted>")
    .replace(/[A-Za-z0-9_-]{16,}@im\.(?:wechat|bot)/g, "<platform-id-redacted>")
    .replace(/(?<![A-Za-z0-9])[A-Za-z0-9_+/=-]{32,}(?![A-Za-z0-9])/g, "<long-value-redacted>");
}

function write(level, message) {
  if (level === "debug") return;
  if (String(message).includes("AbortError: aborted")) return;
  process.stderr.write(`[weixin-transport] ${level.toUpperCase()} ${sanitize(message)}\n`);
}

export const logger = {
  debug(message) { write("debug", message); },
  info(message) { write("info", message); },
  warn(message) { write("warn", message); },
  error(message) { write("error", message); },
  withAccount() { return this; },
};
