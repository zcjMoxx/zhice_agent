import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const appCss = readFileSync(resolve(process.cwd(), "src/styles/app.css"), "utf8");
const tokensCss = readFileSync(resolve(process.cwd(), "src/styles/tokens.css"), "utf8");

describe("Theme family tokens", () => {
  it("keeps Ivory Obsidian as the default and maps its dark mode to mist silver", () => {
    expect(tokensCss).toContain("--bg: #fcfbf7");
    expect(tokensCss).toContain(':root[data-theme="dark"]');
    expect(tokensCss).toContain("--bg: #202326");
    expect(tokensCss).toContain("--surface: rgba(58, 62, 66, 0.72)");
    expect(tokensCss).toContain("--sidebar: rgba(49, 53, 57, 0.9)");
    expect(tokensCss).toContain("--text: #dce1e2");
    expect(tokensCss).toContain("--accent: #d7dcdd");
    expect(tokensCss).toContain("--focus: #b5c0c2");
    expect(tokensCss).not.toContain("--bg: #15171a");
  });

  it("provides light and dark token blocks for all six theme families", () => {
    for (const family of ["obsidian", "classic", "ocean", "sage", "aurora", "amber"]) {
      expect(tokensCss).toContain(`:root[data-theme-family="${family}"][data-theme="light"]`);
      expect(tokensCss).toContain(`:root[data-theme-family="${family}"][data-theme="dark"]`);
    }
  });

  it("routes ambient light, brand sheen, and avatar gradients through theme tokens", () => {
    expect(appCss).toContain("var(--ambient-primary)");
    expect(appCss).toContain("var(--ambient-secondary)");
    expect(appCss).toContain("var(--auth-brand-sheen)");
    expect(appCss).toContain("var(--avatar-tail)");
    expect(appCss).toContain(':root[data-theme="dark"] body { -webkit-font-smoothing: auto; }');
  });

  it("gives all enabled buttons a tactile pressed state and disabled feedback", () => {
    expect(appCss).toContain("button:not(:disabled):active");
    expect(appCss).toContain("cursor: not-allowed");
  });
});
