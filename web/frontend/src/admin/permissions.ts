import type { UiLanguage } from "@/i18n";

export const ROLE_NAMES: Record<string, string> = {
  owner: "系统所有者",
  admin: "管理员",
  developer: "开发者",
  viewer: "普通用户",
  auditor: "审计员",
};

const ROLE_NAMES_EN: Record<string, string> = {
  owner: "Owner",
  admin: "Administrator",
  developer: "Developer",
  viewer: "Viewer",
  auditor: "Auditor",
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
  "diagnostics.system.use": { name: "使用系统诊断", description: "查看跨用户事故与脱敏运行时间线", group: "运行与诊断" },
  "tool.exec.dangerous": { name: "请求高风险执行", description: "在明确确认后请求高风险 exec", group: "Tool 与安全" },
  "skill.sources.read": { name: "查看 Skill source", description: "查看 Skill source 状态、健康和安全错误摘要", group: "Skill 管理" },
  "skill.sync": { name: "同步 Skill", description: "同步配置的 Skill source", group: "Tool 与安全" },
  "audit.read": { name: "查看安全审计", description: "查看登录、权限与危险操作审计", group: "审计" },
  "audit.export": { name: "导出安全审计", description: "导出筛选后的安全审计记录", group: "审计" },
};

export const BASE_CAPABILITIES = ["与 Agent 对话", "管理本人 Session", "使用本人 Memory", "使用低风险 Tool"];

const BASE_CAPABILITIES_EN = ["Chat with Agent", "Manage own Sessions", "Use own Memory", "Use low-risk Tools"];

const PERMISSION_META_EN: Record<string, { name: string; group: string }> = {
  "auth.users.read": { name: "View users", group: "Users and accounts" },
  "auth.users.manage": { name: "Manage users", group: "Users and accounts" },
  "auth.admin.manage": { name: "Delegate administrator management", group: "Users and accounts" },
  "auth.roles.read": { name: "View roles", group: "Roles and permissions" },
  "auth.roles.manage": { name: "Manage roles", group: "Roles and permissions" },
  "session.manage.any": { name: "Manage all Sessions", group: "Sessions and chat" },
  "chat.stop.any": { name: "Stop any Turn", group: "Sessions and chat" },
  "turn.read.any": { name: "View runtime activity", group: "Runtime and diagnostics" },
  "diagnostics.system.use": { name: "Use system diagnostics", group: "Runtime and diagnostics" },
  "tool.exec.dangerous": { name: "Request high-risk execution", group: "Tools and security" },
  "skill.sources.read": { name: "View Skill sources", group: "Skill management" },
  "skill.sync": { name: "Sync Skills", group: "Tools and security" },
  "audit.read": { name: "View security audit", group: "Audit" },
  "audit.export": { name: "Export security audit", group: "Audit" },
};

export function roleName(key: string, language: UiLanguage = "zh-CN"): string {
  return (language === "en" ? ROLE_NAMES_EN : ROLE_NAMES)[key] ?? key;
}

export function baseCapabilities(language: UiLanguage = "zh-CN"): string[] {
  return language === "en" ? BASE_CAPABILITIES_EN : BASE_CAPABILITIES;
}

export function permissionLabel(key: string, language: UiLanguage = "zh-CN"): string {
  return (language === "en" ? PERMISSION_META_EN[key]?.name : PERMISSION_META[key]?.name) ?? key;
}

export function groupedPermissions(keys: string[], language: UiLanguage = "zh-CN"): Record<string, string[]> {
  return keys.reduce<Record<string, string[]>>((groups, key) => {
    const group = language === "en"
      ? PERMISSION_META_EN[key]?.group ?? "Technical permissions"
      : PERMISSION_META[key]?.group ?? "技术权限";
    (groups[group] ??= []).push(key);
    return groups;
  }, {});
}
