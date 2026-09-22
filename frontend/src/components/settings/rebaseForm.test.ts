import assert from "node:assert/strict";
import { test } from "node:test";
import { rebaseForm } from "./rebaseForm.ts";

test("keeps unsaved edits and takes other fields from the server", () => {
  const oldServer = { enabled: true, downloads_per_hour: 0, delay: 1 };
  const form = { ...oldServer, downloads_per_hour: 30 }; // typed, not saved yet
  const newServer = { ...oldServer, enabled: false }; // toggled elsewhere and saved
  assert.deepEqual(rebaseForm(form, oldServer, newServer), {
    enabled: false,
    downloads_per_hour: 30,
    delay: 1,
  });
});

test("an untouched form follows the server entirely", () => {
  const oldServer = { a: 1, tags: ["x"] };
  const newServer = { a: 2, tags: ["x", "y"] };
  assert.deepEqual(rebaseForm({ ...oldServer }, oldServer, newServer), newServer);
});

test("after a save the form matches the saved values", () => {
  const oldServer = { a: 1, b: "t" };
  const form = { a: 5, b: "t" };
  assert.deepEqual(rebaseForm(form, oldServer, { a: 5, b: "t" }), { a: 5, b: "t" });
});

test("compares nested values by content", () => {
  const oldServer = { list: [1, 2] };
  const form = { list: [1, 2] }; // equal content, different array
  assert.deepEqual(rebaseForm(form, oldServer, { list: [3] }), { list: [3] });
});
