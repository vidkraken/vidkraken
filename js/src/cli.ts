#!/usr/bin/env node
import { runCli } from "./cli-run.js";

runCli(process.argv.slice(2), {
  stdout: process.stdout,
  stderr: process.stderr,
  env: process.env,
}).then(
  (code) => {
    process.exitCode = code;
  },
  (err: unknown) => {
    process.stderr.write(`error: ${(err as Error)?.stack ?? String(err)}\n`);
    process.exitCode = 1;
  },
);
