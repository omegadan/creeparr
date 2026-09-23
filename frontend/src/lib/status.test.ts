import assert from "node:assert/strict";
import { test } from "node:test";
import { mediaStatus, postStatus } from "./status.ts";

test("known statuses keep their label and tone", () => {
  assert.deepEqual(mediaStatus("completed"), { label: "Done", tone: "ok" });
  assert.deepEqual(postStatus("partial"), { label: "Partial", tone: "warn" });
});

test("a status this UI doesn't know yet renders instead of crashing the page", () => {
  assert.deepEqual(mediaStatus("brand_new_state"), { label: "brand_new_state", tone: "muted" });
  assert.deepEqual(postStatus("archived_elsewhere"), { label: "archived_elsewhere", tone: "muted" });
});
