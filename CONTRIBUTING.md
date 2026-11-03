# Contributing

Thanks for helping. Bug reports, fixes and provider updates are all welcome.

- **Bugs and ideas:** open an [issue](https://github.com/omegadan/creeparr/issues/new/choose).
  For anything bigger than a small fix, open one before writing code, so we can agree on
  the approach first.
- **Security problems:** report them privately, never in an issue; see
  [SECURITY.md](SECURITY.md).
- **Behaviour:** everyone taking part follows the [code of conduct](CODE_OF_CONDUCT.md).

## What fits

Creeparr archives what the user's own account can already see. Changes that get around
paywalls, DRM or other access controls, or that share or redistribute content, won't be
accepted; see the [disclaimer](DISCLAIMER.md). Instagram and Reddit are switched off
because they block automated access; please don't turn them back on in a PR.

## Setting up

You need Python 3.12 with [uv](https://docs.astral.sh/uv/), and Node 22.

```bash
# backend
cd backend && uv sync --all-extras   # plain `uv sync` removes curl_cffi (the `impersonate` extra the image ships)
cd .. && make backend                # API on :7979, data in ./config and ./downloads

# frontend, in another terminal
cd frontend && npm ci && npm run dev # proxies /api to :7979
```

`make docker` builds the image the way CI does.

## Before you open a pull request

```bash
make lint                     # ruff, and tsc + oxlint
make test                     # backend tests
cd frontend && npm test       # frontend tests
```

CI runs the same checks and builds the image; a PR needs them to pass.

- **Tests:** add or update a test for each behaviour you change. Provider tests run
  against recorded API responses (`backend/tests/fixtures/`); never call the real sites
  from a test.
- **No real data:** fixtures, screenshots and logs in a PR must not contain real
  cookies, account names or other people's content. Replace them with made-up values.
- **API changes:** after changing a response or event model, run `make api-types` and
  commit the regenerated `frontend/src/api/schema.ts`.
- **Database changes:** add a migration with `make migration m="what changed"`.
- **Changelog:** add a line under **Unreleased** in [CHANGELOG.md](CHANGELOG.md) for each
  change users will notice, in plain words.
- **Words:** the domain terms (provider, creator, post, scan and so on) are defined in
  [GLOSSARY.md](GLOSSARY.md); use them in code and docs. Architecture notes are in
  [docs/architecture.md](docs/architecture.md).

Keep a PR to one change. Releases are made by the maintainer with the **Release**
workflow; see the end of the README.

## When a site changes

The Patreon and OnlyFans APIs are private and change without notice. A fix usually
means re-recording a fixture and adjusting the parser. Describe what changed in the PR,
with the real values replaced as above.
