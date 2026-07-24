// ZhiCe patch: the vendored API only needs optional wire metadata config.
// Account ownership and credentials remain in the Python binding service.
export function loadConfigBotAgent() {
  return "ZhiCe-Agent/0.1";
}

export function loadConfigRouteTag() {
  return undefined;
}
