import assert from "node:assert/strict";
import { test } from "node:test";
import { skippedScansMessage } from "./scans.ts";

const expired = "Patreon session is not valid; fix it in Settings before scanning";

test("one skipped scan names the creator", () => {
  assert.equal(skippedScansMessage([{ creator_name: "Ann", message: expired }]), `Scan of Ann skipped: ${expired}`);
});

test("a burst is counted, with each reason once", () => {
  const burst = [
    { creator_name: "Ann", message: expired },
    { creator_name: "Bo", message: expired },
    { creator_name: "Cy", message: "this creator is disabled" },
  ];
  assert.equal(skippedScansMessage(burst), `3 scans skipped: ${expired}; this creator is disabled`);
});
