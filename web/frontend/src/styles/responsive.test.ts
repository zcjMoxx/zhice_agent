import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const readStyle = (name: string) => readFileSync(resolve(process.cwd(), `src/styles/${name}.css`), "utf8");
const readSource = (name: string) => readFileSync(resolve(process.cwd(), `src/${name}`), "utf8");

describe("responsive layout contracts", () => {
  it("keeps travel content full-width while the mobile plan sidebar becomes an overlay", () => {
    const css = readStyle("travel");
    expect(css).toContain(".travel-workspace, .travel-left-collapsed .travel-workspace { grid-template-columns: minmax(0, 1fr);");
    expect(css).toContain(".travel-sidebar { position: fixed; z-index: 40;");
    expect(css).toContain(".travel-form-details { width: 100vw; max-width: 100vw;");
    expect(css).toContain(".travel-route-list, .travel-day-notes, .travel-transit-legs { grid-column: 1; min-width: 0;");
    expect(css).toContain(".travel-route-list > div, .travel-transit-legs li { grid-template-columns: minmax(0, 1fr);");
    expect(css).toContain('.travel-saved-row > [role="button"] { display: grid; min-width: 40px; min-height: 40px;');
    expect(readSource("pages/TravelPlannerPage.vue")).toContain('if (window.matchMedia?.("(max-width: 760px)").matches) leftCollapsed.value = true;');
  });

  it("uses a single-column mobile workflow and a viewport-bound node detail sheet", () => {
    const css = readStyle("workflow");
    expect(css).toContain(".workflow-overview-grid, .workflow-overview-all { grid-template-columns: minmax(0, 1fr);");
    expect(css).toContain(".workflow-body, .workflow-body.inspector-collapsed { display: grid; width: 100%; height: 100%; min-width: 0; min-height: 0; grid-template-columns: minmax(0, 1fr);");
    expect(css).toContain(".workflow-canvas { width: 100%; max-width: 100%; min-width: 0; height: auto; min-height: 0;");
    expect(css).toContain(".workflow-readiness { position: fixed; z-index: 95; top: auto; right: 8px;");
    expect(css).toContain('.workflow-readiness[data-open="true"] { display: grid;');
    expect(css).toContain(".workflow-readiness-toggle, .workflow-connection-status { display: inline-flex;");
    expect(css).toContain("max-height: min(42dvh, 420px) !important;");
    expect(css).toContain(".canvas-toolbar > span, .canvas-pan-controls { display: none;");
    expect(css).toContain(".workflow-node-bubble-shell { position: fixed !important;");
    expect(css).not.toContain("max-height: min(68dvh, 620px) !important;");
    expect(css).toContain(".canvas-toolbar button { min-width: 40px; min-height: 40px; flex: 0 0 auto; white-space: nowrap;");
    expect(css).toContain(".canvas-toolbar .canvas-layout-button { width: 40px;");
    expect(css).toContain('.edge-actions { display: none; }.mobile-edge-selector { position: absolute; z-index: 5; display: grid; width: 60px; height: 60px;');
    expect(css).toContain('.mobile-edge-actions { position: fixed; z-index: 96; right: 12px;');
    expect(css).toContain('.mobile-edge-actions button { display: inline-flex; min-width: 0; min-height: 48px;');
    expect(css).toContain('.quick-add-menu { z-index: 110; top: auto !important; right: 8px; bottom: max(8px, env(safe-area-inset-bottom)); left: 8px !important;');
    expect(css).toContain(".workflow-canvas .vue-flow__controls-button { width: 40px; height: 40px;");
    expect(css).toContain(".workflow-canvas .vue-flow, .workflow-canvas .vue-flow__pane { touch-action: none; overscroll-behavior: contain; user-select: none;");
    expect(css).toContain(".workflow-canvas .vue-flow__minimap { display: none;");
    expect(css).toContain(".workflow-node .vue-flow__handle { position: absolute; display: block; width: 18px; height: 18px;");
    expect(css).toContain(".workflow-node .vue-flow__handle::before { position: absolute; top: 50%; left: 50%; width: 56px; height: 56px;");
    expect(css).toContain(".workflow-node .vue-flow__handle-left, .workflow-node .vue-flow__handle-right { top: 50% !important; bottom: auto !important;");
    expect(css).toContain(".workflow-node-edit { left: 6px; bottom: 6px; width: 36px; height: 36px; opacity: 1;");
    expect(css).toContain('.workflow-canvas[data-connection-active="true"] .workflow-node .vue-flow__handle, .workflow-canvas[data-connection-active="true"] .workflow-node .vue-flow__handle.connecting { width: 18px; height: 18px;');
    expect(css).toContain(".workflow-connection-banner, .canvas-interaction-hint { display: none;");
    expect(css).toContain(".run-summary-actions button { min-height: 40px;");
    const source = readSource("pages/WorkflowPage.vue");
    expect(source).toContain('if (window.matchMedia?.("(max-width: 760px)").matches) sidebarCollapsed.value = true;');
    expect(source).toContain('Ctrl + 滚轮缩放 · Alt + 滚轮横移');
    expect(source).toContain('@wheel.capture="handleCanvasWheel"');
    expect(source).not.toContain('if (window.matchMedia("(max-width: 760px)").matches) return;');
    expect(source).toContain('clickConnectionPreview.value = null;');
    expect(source).not.toContain('translateY(-72px)');
    expect(css).toContain('.workflow-canvas[data-connection-active="true"], .workflow-canvas[data-connection-active="true"] .vue-flow__pane { cursor: url(');
    expect(source).toContain('showConnectionToast(tr("连接方向错误"');
    expect(source).toContain("event.stopPropagation();");
    expect(source).toContain('@contextmenu.capture="handleCanvasContextMenu"');
    expect(source).toContain('@dblclick.capture="handleCanvasDoubleClick"');
    expect(source).toContain('return true;');
    expect(source).toContain('@pane-click="handlePaneClick"');
    expect(source).toContain(':pan-on-drag="!clickConnectionActive"');
    expect(source).toContain(':connect-on-click="false"');
    expect(source).toContain(':interaction-width="56"');
    expect(source).toContain('class="mobile-edge-actions"');
    expect(source).toContain('class="workflow-connection-toast"');
    expect(css).toContain(".workflow-connection-toast { position: absolute; z-index: 120; top: 14px; left: 50%;");
    expect(css).toContain(".workflow-node .vue-flow__handle { width: 24px; height: 24px;");
    expect(source).toContain('class="mobile-edge-selector"');
    expect(source).toContain('<Pencil :size="14" />');
    expect(source).toContain('if (clickConnectionActive.value) {');
    expect(source).not.toContain("translateY(-72px)");
    expect(source).toContain('拖动空白处移动连线终点，画布保持不动');
    expect(css).toContain('.workflow-canvas .vue-flow__edge-path { stroke: color-mix(in srgb, var(--success) 82%, var(--text)); stroke-width: 2.6; opacity: 1;');
  });

  it("bounds settings and admin content to the mobile viewport", () => {
    const css = readStyle("app");
    expect(css).toContain(".settings-center { width: 100%; max-width: 100vw; height: 100dvh;");
    expect(css).toContain(".admin-shell { width: 100vw; height: 100dvh;");
    expect(css).toContain("overscroll-behavior-inline: contain;");
    expect(css).toContain(".quick-preference { min-width: 40px; height: 40px;");
    expect(css).toContain(".admin-main button, .admin-main select, .admin-main input:not([type=\"checkbox\"]):not([type=\"radio\"]) { min-height: 40px;");
    expect(readSource("components/SessionSidebar.vue")).toContain('if (window.matchMedia?.("(max-width: 720px)").matches) ui.sidebarCollapsed = true;');
  });
});
