# Changelog

What changed in each release, newest first. Add a line under **Unreleased** with every
change people will notice; the Release workflow turns it into the next version's notes.

## Unreleased

- Images now carry a signed build provenance attestation and an SBOM; see SECURITY.md
  to verify one. Everything the image is built from is pinned to an exact version.
- ffmpeg comes from a different static build and is now version 9.0.2 (was 7.0.2). The
  image is about 180 MB larger.
- OnlyFans signing rules that do anything but fill in the signature are refused.

## 0.2.0 (2026-11-03)

- First-run setup: a new install asks you to set a password, or to continue without
  one on a trusted local network. Setting a password signs you in, and the sidebar
  shows "No password set" until you have one. New passwords need 8 characters.
- Releases: the `latest` image now means the last release. Every merge to `main` is
  published as `edge` instead. Each release is tagged `X.Y.Z` and `X.Y`, with notes.
- Instagram and Reddit are switched off for now: both block automated access too
  aggressively. Creators, posts and downloads you already have are kept and left alone;
  their queued downloads show as "Unavailable" on the Queue page.
- A scan queued before you switched off a creator or provider, or before its session
  expired, is now skipped, with a notice, instead of running.
- A creator of an unavailable provider no longer stops the scheduled scans of the
  creators after it.
- Adding several creators no longer stops queuing their first scans at the first one
  that can't scan.
- The naming preview in Settings uses the same path rules as downloads.
- Fixed: a YouTube download whose size yt-dlp only estimates no longer fails (it failed on reporting its progress).

## 0.1.13 and earlier

Not recorded here; see the commit history.
