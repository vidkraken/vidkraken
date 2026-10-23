// The built package resolves through its own `exports` map (self-reference)
// for both ESM `import` and CJS `require`.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { test } from "node:test";

test("ESM import of the package", async () => {
  const mod = await import("vidkraken");
  assert.equal(typeof mod.VidKraken, "function");
  assert.equal(typeof mod.VidKrakenError, "function");
  assert.deepEqual([...mod.MEDIA_FORMATS], ["1080", "720", "480", "360", "audio", "audio-aac", "audio-mp3"]);
  const vk = new mod.VidKraken({ apiKey: "k", fetch: async () => new Response("{}") });
  assert.equal(vk.baseUrl, "https://vidkraken.com/api/v2");
});

test("CJS require of the package", () => {
  const require = createRequire(import.meta.url);
  const mod = require("vidkraken");
  assert.equal(typeof mod.VidKraken, "function");
  assert.equal(typeof mod.VidKrakenJobFailedError, "function");
  assert.equal(require.resolve("vidkraken").endsWith("index.cjs"), true);
});
