# Security

## Reporting a vulnerability

Please report it privately: on GitHub, open the repository's **Security** tab and click
**Report a vulnerability**. Don't open a public issue. You'll get credit in the release
notes if you'd like it.

Only the latest release gets fixes.

## What Creeparr holds

The session cookies you paste in give full access to those accounts. They're stored
unencrypted in the SQLite database under `/config`; see
[Security in the README](README.md#security) for how the web UI is protected and what
to keep private.

## Where the image comes from

Every image on `ghcr.io/omegadan/creeparr` is built by GitHub Actions from a commit in
this repository, after CI passes, and carries a signed build provenance attestation.
Check one with the GitHub CLI:

```bash
gh attestation verify oci://ghcr.io/omegadan/creeparr:latest --owner omegadan
```

It also carries an SBOM (software bill of materials) of what it contains:

```bash
docker buildx imagetools inspect ghcr.io/omegadan/creeparr:latest --format '{{ json .SBOM }}'
```

The build uses only pinned inputs: base and tool images by digest, GitHub Actions by
commit, and Python and npm packages from lockfiles with hashes. Dependabot proposes
updates to all of them weekly, and each goes through CI.

## Code from outside at runtime

- **yt-dlp and gallery-dl** are updated every week (see the README), because sites break
  them often.
- **OnlyFans signing rules**: OnlyFans requires a signature on each request, computed
  from rules that change every few weeks. Creeparr downloads them from a
  community-maintained file, by default
  `https://raw.githubusercontent.com/DATAHOARDERS/dynamic-rules/main/onlyfans.json`.
  They're data, not code, and Creeparr refuses rules that do anything but fill in the
  signature, but they do shape the requests made with your OnlyFans session. To use
  your own copy, change **Dynamic rules URL** in **Settings → OnlyFans**.
