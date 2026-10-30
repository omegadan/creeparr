import assert from "node:assert/strict";
import { test } from "node:test";
import { postTypeLabel, postTypeLabels, providerLabel, settingsForm } from "./providers.ts";

const providers = [
  { name: "patreon", label: "Patreon", post_types: { video_embed: "Embed" } },
  { name: "instagram", label: "Instagram", post_types: { instagram_story: "Story" } },
];

test("post types are labelled from every provider's description", () => {
  const labels = postTypeLabels(providers);
  assert.equal(postTypeLabel(labels, "instagram_story"), "Story");
  assert.equal(postTypeLabel(labels, "video_embed"), "Embed");
  assert.equal(postTypeLabel(labels, "audio_embed"), "audio embed"); // undeclared: readable
  assert.equal(postTypeLabel(labels, null), "–");
});

test("a provider is named by its label", () => {
  assert.equal(providerLabel(providers, "instagram"), "Instagram");
  assert.equal(providerLabel(providers, "unknown"), "unknown");
});

test("a provider without a hand-made form gets the generic settings tab", () => {
  const forms = { patreon: "PatreonForm" };
  assert.equal(settingsForm("patreon", forms, "Generic"), "PatreonForm");
  assert.equal(settingsForm("newsite", forms, "Generic"), "Generic");
  assert.equal(settingsForm("toString", forms, "Generic"), "Generic"); // not inherited keys
});
