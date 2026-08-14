import { fileURLToPath, URL } from "node:url";
import path from "node:path";

import vue from "@vitejs/plugin-vue";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const repoRoot = fileURLToPath(new URL("../..", import.meta.url));
  const workspace = process.env.ZHICE_AGENT_WORKSPACE?.trim()
    || (process.env.USERPROFILE ? path.join(process.env.USERPROFILE, ".zhice") : "");
  const privateEnv = loadEnv(mode, path.join(repoRoot, "deploy", "private"), "VITE_AMAP_");
  const workspaceEnv = workspace
    ? loadEnv(mode, path.join(workspace, "config"), "VITE_AMAP_")
    : {};
  const amapKey = process.env.VITE_AMAP_JS_API_KEY
    || workspaceEnv.VITE_AMAP_JS_API_KEY
    || privateEnv.VITE_AMAP_JS_API_KEY
    || "";
  const amapSecurityCode = process.env.VITE_AMAP_JS_SECURITY_CODE
    || workspaceEnv.VITE_AMAP_JS_SECURITY_CODE
    || privateEnv.VITE_AMAP_JS_SECURITY_CODE
    || "";

  return {
    base: "/static/",
    plugins: [vue()],
    define: {
      "import.meta.env.VITE_AMAP_JS_API_KEY": JSON.stringify(amapKey),
      "import.meta.env.VITE_AMAP_JS_SECURITY_CODE": JSON.stringify(amapSecurityCode),
    },
    resolve: {
      alias: {
        "@": fileURLToPath(new URL("./src", import.meta.url)),
      },
    },
    build: {
      outDir: "../../agent/web/static",
      emptyOutDir: true,
      sourcemap: true,
    },
  };
});
