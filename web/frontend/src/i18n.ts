export type UiLanguage = "zh-CN" | "en";

export function uiText(language: UiLanguage, chinese: string, english: string): string {
  return language === "en" ? english : chinese;
}
