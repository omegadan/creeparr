import type { MediaStatus, PostStatus } from "../api/types";

export interface StatusMeta {
  label: string;
  tone: "ok" | "info" | "warn" | "danger" | "muted";
}

export const POST_STATUS: Record<PostStatus, StatusMeta> = {
  new: { label: "New", tone: "info" },
  no_access: { label: "No access", tone: "muted" },
  no_media: { label: "No media", tone: "muted" },
  pending: { label: "Pending", tone: "info" },
  completed: { label: "Archived", tone: "ok" },
  partial: { label: "Partial", tone: "warn" },
  unsupported: { label: "Unsupported", tone: "danger" },
  skipped: { label: "Skipped", tone: "muted" },
};

export const MEDIA_STATUS: Record<MediaStatus, StatusMeta> = {
  discovered: { label: "Discovered", tone: "muted" },
  queued: { label: "Queued", tone: "info" },
  downloading: { label: "Downloading", tone: "info" },
  completed: { label: "Done", tone: "ok" },
  failed: { label: "Failed (retrying)", tone: "warn" },
  failed_permanent: { label: "Failed", tone: "danger" },
  cancelled: { label: "Cancelled", tone: "muted" },
  skipped: { label: "Skipped", tone: "muted" },
  unsupported: { label: "Unsupported", tone: "danger" },
  unsupported_drm: { label: "DRM", tone: "danger" },
  no_access: { label: "No access", tone: "muted" },
  missing: { label: "Missing", tone: "danger" },
};

/** Badge data for a status, including ones this UI version doesn't know yet. */
export const postStatus = (s: string): StatusMeta => POST_STATUS[s as PostStatus] ?? { label: s, tone: "muted" };
export const mediaStatus = (s: string): StatusMeta => MEDIA_STATUS[s as MediaStatus] ?? { label: s, tone: "muted" };

export const TONE_CLASS: Record<StatusMeta["tone"], string> = {
  ok: "bg-ok/15 text-ok border-ok/30",
  info: "bg-info/15 text-info border-info/30",
  warn: "bg-warn/15 text-warn border-warn/30",
  danger: "bg-danger/15 text-danger border-danger/30",
  muted: "bg-bg-3 text-fg-muted border-line",
};

export const POST_TYPES: Record<string, string> = {
  video_external_file: "Video",
  video_embed: "Embed",
  image_file: "Images",
  audio_file: "Audio",
  podcast: "Podcast",
  text: "Text",
  poll: "Poll",
  link: "Link",
  livestream_youtube: "Livestream",
  onlyfans_post: "Post",
  onlyfans_message: "Message",
  youtube_video: "Video",
  instagram_post: "Post",
  instagram_reel: "Reel",
  instagram_story: "Story",
  instagram_highlight: "Highlight",
  instagram_tagged: "Tagged",
  reddit_post: "Post",
};

export function postTypeLabel(t: string | null): string {
  if (!t) return "–";
  return POST_TYPES[t] ?? t.replace(/_/g, " ");
}

export function sourceLabel(s: string | null): string {
  switch (s) {
    case "native_direct": return "Patreon file";
    case "native_hls": return "Patreon stream";
    case "embed_youtube": return "YouTube";
    case "embed_vimeo": return "Vimeo";
    case "embed_other": return "Embed";
    case "media_download": return "Media";
    default: return s ?? "–";
  }
}
