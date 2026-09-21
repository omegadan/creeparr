# Submitting Creeparr to Community Applications (CA)

Community Applications is curated. You cannot self-publish; a moderator adds your
template **repository** to CA's feed after you request it. Everything on the app side
is ready in this repo; the remaining steps require your Unraid forum account.

## 1. What CA needs (already done here)

- A public GitHub repo containing the template XML: `unraid/creeparr.xml`.
- A square **PNG** icon: `unraid/creeparr-icon.png` (CA renders PNG better than SVG).
- Valid template tags: `<Icon>`, `<TemplateURL>`, `<Project>`, `<Overview>`,
  `<Category>` (from CA's allowed list), and `<Support>`.
- A public image on a registry: `ghcr.io/omegadan/creeparr:latest` (already published).

## 2. Create a support thread (required by CA)

CA requires every app to have a dedicated support topic.

1. Go to the Unraid forums → **Community Applications → Docker Containers** subforum.
2. Create a new topic, e.g. "Support - Creeparr". Briefly describe the app and link
   the GitHub repo.
3. Copy the topic URL and set it as `<Support>` in `unraid/creeparr.xml`
   (replace `https://github.com/omegadan/creeparr/issues`), commit and push.

## 3. Ask CA to add your repository

1. Open the **Community Applications** support thread on the Unraid forums
   (search "Community Applications" by Squid).
2. Post a request to add your template repository, including:
   - GitHub repo: `https://github.com/omegadan/creeparr`
   - A one-line description and the support-thread link from step 2.
3. A moderator reviews and adds the repo to the CA feed. After that, "creeparr"
   is searchable in CA on every Unraid server.

Alternatively, register the repo yourself in CA's settings if that option is enabled:
**Apps → (gear) Settings → "Enable additional search results from dockerHub" / template
repositories**, then add your GitHub repo URL. Availability of this varies by CA version;
the forum request is the canonical route.

## 4. Keep it healthy

- CA re-reads the template from GitHub, so pushes to `main` propagate automatically.
- Keep the image tag (`latest`) published by the GitHub Actions workflow.
- Respond in the support thread; CA moderators may remove unmaintained apps.

## Notes

- The template's default paths are `/mnt/user/appdata/creeparr`,
  `/mnt/user/media/patreon` and `/mnt/user/media/onlyfans`; users can change them.
- `<Category>` uses CA tokens `Downloaders:`, `MediaApp:Video:`, `Tools:Utilities`.
  Adjust if a moderator asks for different ones.
