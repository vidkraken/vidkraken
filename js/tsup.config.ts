import { readFileSync } from "node:fs";
import { defineConfig } from "tsup";

const { version } = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf8")) as {
  version: string;
};

const define = { __VERSION__: JSON.stringify(version) };

export default defineConfig([
  // The library: ESM + CJS + type declarations. No Node built-ins are imported
  // at the top level, so it runs in any runtime with a global fetch; saveFile()
  // loads node:fs lazily.
  {
    entry: { index: "src/index.ts" },
    format: ["esm", "cjs"],
    dts: true,
    target: "es2020",
    platform: "node",
    define,
    removeNodeProtocol: false,
  },
  // The CLI: ESM only. `cli-run` holds the testable runner; `cli` is the bin.
  {
    entry: { cli: "src/cli.ts", "cli-run": "src/cli-run.ts" },
    format: ["esm"],
    target: "node18",
    platform: "node",
    define,
    removeNodeProtocol: false,
  },
]);
