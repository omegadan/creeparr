# End-to-end checklist (real account)

Run against a real Patreon account with at least one paid pledge. Tick each item.

- [ ] Settings → Patreon: paste `session_id` → **Test** shows your account name; **Save & connect**
      sets the status pill green.
- [ ] Creators → **Import pledges** lists your memberships with paid/free/trial badges.
- [ ] **Add creator** works with: vanity name, `https://www.patreon.com/c/<vanity>`,
      `https://www.patreon.com/<vanity>`, numeric campaign id, `/user?u=<id>`.
- [ ] After adding, a full scan runs; post count matches what Patreon shows for that creator.
- [ ] A native mp4 post downloads and plays; file lands under
      `Creator/YYYY-MM-DD - Title [id]/` with `post.json`, `post.md`, `post.html`.
- [ ] A Mux (HLS) video post downloads via yt-dlp and plays (check merged mp4, audio present).
- [ ] A YouTube embed post and a Vimeo embed post download.
- [ ] A DRM-protected post ends as **DRM** (unsupported), no retries scheduled.
- [ ] A creator you do **not** pledge to: posts show as **No access**; no downloads attempted.
- [ ] Toggle **Images** on for a creator → gallery images are queued and downloaded in order.
- [ ] Post a test post (or wait for a creator's next post): the scheduled scan picks it up and
      queues it within the interval.
- [ ] Kill the container mid-download → on restart the job is re-queued, the `.part` resumes
      or restarts, and no stray `.part`/`.patreonarr-tmp-*` remain after completion.
- [ ] Replace the session cookie with garbage → red banner appears, scans refuse to run,
      Patreon-hosted downloads wait; fixing the cookie recovers everything.
- [ ] Set `downloads.min_free_mb` above the free space → queue pauses with "disk_full";
      lowering it resumes.
- [ ] Files on the host are owned by `PUID:PGID`.
- [ ] Naming template change is reflected in **preview** and in newly downloaded files.
- [ ] System → Status shows correct counts, ffmpeg path and yt-dlp version; **Run** on a task works.

Record the API responses used (sanitised) into `backend/tests/fixtures/patreon/` if any
resolver assumptions turned out wrong.
