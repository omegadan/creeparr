# Unraid installation

Creeparr is not (yet) in Community Applications, so add it as a template manually.

## Option A — template URL

1. Unraid → **Docker** tab → **Add Container**.
2. In **Template**, paste:
   `https://raw.githubusercontent.com/omegadan/creeparr/main/unraid/creeparr.xml`
3. Adjust the paths (defaults use `/mnt/user/appdata/creeparr`, `/mnt/user/media/patreon`
   and `/mnt/user/media/onlyfans`) and click **Apply**.

## Option B — by hand

Add a container with:
- Repository: `ghcr.io/omegadan/creeparr:latest`
- Port: `7979` → `7979`
- Paths: `/config` and `/downloads` (the default archive root) mapped to your shares. The per-provider paths (`/downloads-patreon`, `/downloads-onlyfans`, `/downloads-youtube`, `/downloads-instagram`, `/downloads-reddit`) are optional: leave one blank and that provider archives under `/downloads`.
- Variables: `PUID=99`, `PGID=100`, `TZ=America/Los_Angeles`

Then open `http://<tower-ip>:7979`, go to **Settings → Accounts**, and connect Patreon
and/or OnlyFans.

The image is public on GHCR, so no login is needed to pull it.
