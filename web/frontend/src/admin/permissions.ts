export const ROLE_NAMES: Record<string, string> = {
  owner: "系统所有者",
  admin: "管理员",
  developer: "开发者",
  viewer: "普通用户",
  auditor: "审计员",
};

export const PERMISSION_META: Record<string, { name: string; description: string; group: string }> = {
  "auth.users.read": { name: "查看用户", description: "查看本地账号和状态", group: "用户与账号" },
  "auth.users.manage": { name: "管理用户", description: "创建账号并更新用户资料、状态和角色", group: "用户与账号" },
  "auth.admin.manage": { name: "委派管理员管理", description: "提升或撤销其他管理员", group: "用户与账号" },
  "auth.roles.read": { name: "查看角色", description: "查看角色及其附加权限", group: "角色与权限" },
  "auth.roles.manage": { name: "管理角色", description: "修改允许编辑的角色权限", group: "角色与权限" },
  "session.manage.any": { name: "管理全部 Session", description: "跨用户管理 Session", group: "Session 与聊天" },
  "chat.stop.any": { name: "停止任意 Turn", description: "停止其他用户的活动 Turn", group: "Session 与聊天" },
  "turn.read.any": { name: "查看运行活动", description: "查看跨用户 Turn 运行摘要与系统监控", group: "运行与诊断" },
  "tool.exec.dangerous": { name: "请求高风险执行", description: "在明确确认后请求高风险 exec", group: "Tool 与安全" },
  "skill.sync": { name: "同步 Skill", description: "同步配置的 Skill source", group: "Tool 与安全" },
  "audit.read": { name: "查看安全审计", description: "查看登录、权限与危险操作审计", group: "审计" },
  "audit.export": { name: "导出安全审计", description: "导出筛选后的安全审计记录", group: "审计" },
};

export const BASE_CAPABILITIES = ["与 Agent 对话", "管理本人 Session", "使用本人 Memory", "使用低风险 Tool"];

export function permissionLabel(key: string): string { return PERMISSION_META[key]?.name ?? key; }

export function groupedPermissions(keys: string[]): Record<string, string[]> {
  return keys.reduce<Record<string, string[]>>((groups, key) => {
    const group = PERMISSION_META[key]?.group ?? "技术权限";
    (groups[group] ??= []).push(key);
    return groups;
  }, {});
}
