# Creeparr

Creeparr archives creators' posts and media from subscription and social platforms into a local media library.

## Archive

**Provider**:
A platform Creeparr archives from: Patreon, OnlyFans, YouTube, Instagram or Reddit.
_Avoid_: site, platform, source

**Creator**:
An account on a provider whose posts are archived: a Patreon campaign, an OnlyFans or Instagram account, a YouTube channel, a subreddit or a Reddit user.
_Avoid_: channel, campaign, artist

**Post**:
One thing a creator published, holding zero or more media.

**Media**:
One downloadable item of a post: a video, image, audio file or attachment.
_Avoid_: asset, file (the file is what a download produces)

**Scan**:
Reading a creator's posts from its provider to record new and changed posts and their media.

## Media lifecycle

**Media lifecycle**:
The rules for how media and posts change status in response to actions, scans and downloads.
_Avoid_: queue bookkeeping, status logic

**Action**:
Something the user asks for on a post or its media: retry, skip, unskip, force download, remove from queue, retry all failed, clear failed.
_Avoid_: command, operation

**Post-level action**:
An action applied to all of a post's media; it respects the creator preferences.

**Item-level action**:
An action applied to one media item; it overrides the creator preferences.

**Creator preferences**:
A creator's switches for including images, audio and attachments. Videos are always wanted.

**Wanted**:
Whether a media item should be archived.

**Attempts**:
How many download tries a media item has used since the user last asked for it.

**Skipped**:
Media the user or the creator preferences chose not to archive.

**Cancelled**:
Media whose download the user stopped: still wanted, but never retried on its own.
_Avoid_: removed, aborted

**Missing**:
Media that was archived but whose file is no longer on disk.

**Failed**:
The media that need attention: failed and waiting to retry, failed permanently, or missing.
_Avoid_: errored, broken

**Post status**:
A summary derived from the statuses of a post's wanted media; never set directly.
