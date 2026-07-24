import { copyFileSync, mkdirSync } from "node:fs";

mkdirSync(new URL("../dist", import.meta.url), { recursive: true });
copyFileSync(new URL("../src/main.js", import.meta.url), new URL("../dist/main.js", import.meta.url));
copyFileSync(
  new URL("../src/official-driver.js", import.meta.url),
  new URL("../dist/official-driver.js", import.meta.url),
);
