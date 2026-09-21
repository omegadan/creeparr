# Patreon private API notes

What Creeparr relies on, verified against gallery-dl, yt-dlp and patreon-dl source in
September 2026. Field names may change without notice; `post.raw_json` keeps everything so
media can be re-resolved after a resolver fix.

| Purpose | Request |
| --- | --- |
| Base | `https://www.patreon.com/api`, `Content-Type: application/vnd.api+json`, `json-api-version=1.0` |
| Auth | Cookie `session_id=…` (plus any extra patreon.com cookies from cookies.txt, e.g. `cf_clearance`) |
| User-Agent | `Patreon/126.9.0.15 (Android; Android 14; Scale/2.10)` (needed for Mux `.m3u8` access) |
| Current user | `GET /current_user?fields[user]=full_name,email,vanity,image_url` |
| Pledges | `GET /current_user?include=active_memberships.campaign&fields[campaign]=avatar_photo_image_urls,name,published_at,url,vanity,is_nsfw,url_for_current_user&fields[member]=is_free_member,is_free_trial` |
| Vanity → id | `GET /search?q={vanity}&page[size]=5` → `data[]` with `type == "campaign-document"` and `attributes.url` ending in `/{vanity}`; id is `campaign_NNN`. Fallback: creator page HTML, regex on bootstrap/Next.js JSON. |
| Campaign | `GET /campaigns/{id}?include=creator&fields[campaign]=…&fields[user]=…` |
| Posts | `GET /posts?include=campaign,access_rules,attachments_media,audio,images,media,user,user_defined_tags&fields[post]=…&filter[campaign_id]=X&filter[contains_exclusive_posts]=true&filter[is_draft]=false&sort=-published_at` — follow `links.next` (carries `page[cursor]`). `fields[media]` is deliberately omitted to receive all media attributes. |
| Single post | `GET /posts/{id}` with the same include/fields (used to refresh signed URLs). |

Post attributes used: `title, content, teaser_text, published_at, edited_at, url, post_type,
current_user_can_view, embed{provider,url}, post_file{name,url}, image{url,large_url},
post_metadata{image_order}`.

Media resolution (`patreon/media_resolver.py`):

- `embed.url` → `embed_youtube` / `embed_vimeo` / `embed_other`
- `post_file.url` → `native_hls` when the URL is `.m3u8`/`stream.mux.com`, else `native_direct`
  (video for `video_external_file`, audio for `audio_file`/`podcast`; ignored for `image_file`)
- `images` (ordered by `post_metadata.image_order`), `audio`, `attachments_media`, `media`
  relationships → `media_download` using `download_url` (or `image_urls.original`)

DRM: not flagged by the API. The Mux master/variant playlist is fetched and checked for
`#EXT-X-KEY`/`#EXT-X-SESSION-KEY` with `METHOD=SAMPLE-AES*` or Widevine/FairPlay/PlayReady
`KEYFORMAT`. Plain `AES-128` is not DRM.

Known issue: yt-dlp's own Patreon extractor returned 403 with exported cookie files in mid-2026
(yt-dlp #17010) while live browser cookies worked; suspected TLS fingerprinting. Creeparr
avoids the extractor entirely and offers a `curl_cffi` backend.
