import assert from "node:assert/strict";
import { test } from "node:test";
import { serverVersionWatcher } from "./version.ts";

test("a reconnect to the same server version isn't an update", () => {
  const updated = serverVersionWatcher();
  assert.equal(updated("0.1.12"), false);
  assert.equal(updated("0.1.12"), false);
});

test("a reconnect to a different version is one", () => {
  const updated = serverVersionWatcher();
  updated("0.1.12");
  assert.equal(updated("0.1.13"), true);
  // Rolled back to the version this tab loaded: its code matches the server again.
  assert.equal(updated("0.1.12"), false);
});
