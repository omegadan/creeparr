export interface CreatorStats {
  posts_total: number;
  posts_completed: number;
  posts_pending: number;
  posts_no_access: number;
  posts_unsupported: number;
  media_total: number;
  media_completed: number;
  active_jobs: number;
  bytes: number;
}

export interface Creator {
  id: number;
  provider: string;
  campaign_id: string;
  vanity: string | null;
  name: string;
  url: string | null;
  avatar_url: string | null;
  cover_url: string | null;
  is_nsfw: boolean | null;
  monitored: boolean;
  auto_download: boolean;
  include_images: boolean;
  include_audio: boolean;
  include_attachments: boolean;
  download_since: string | null;
  scan_interval_minutes: number | null;
  folder_name: string | null;
  last_scan_at: string | null;
  last_full_scan_at: string | null;
  last_scan_status: string | null;
  last_scan_error: string | null;
  created_at: string;
  stats: CreatorStats;
  scanning: boolean;
}

export interface CreatorDefaults {
  monitored?: boolean;
  auto_download?: boolean;
  include_images?: boolean;
  include_audio?: boolean;
  include_attachments?: boolean;
  download_since?: string | null;
  scan_interval_minutes?: number | null;
  folder_name?: string | null;
}

export interface CreatorPreview {
  provider: string;
  external_id: string;
  name: string;
  handle: string | null;
  url: string | null;
  avatar_url: string | null;
  description: string | null;
  is_nsfw: boolean | null;
  already_added: boolean;
  creator_id: number | null;
}

export interface Subscription {
  provider: string;
  external_id: string;
  name: string;
  handle: string | null;
  url: string | null;
  avatar_url: string | null;
  is_free: boolean | null;
  is_trial: boolean | null;
  already_added: boolean;
  creator_id: number | null;
}

export interface ProviderInfo {
  name: string;
  label: string;
  configured: boolean;
  credential_fields: string[];
  auth: AuthStatus;
}

export interface ScanRun {
  id: number;
  creator_id: number;
  mode: string;
  trigger: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  pages_fetched: number;
  posts_seen: number;
  posts_new: number;
  posts_updated: number;
  media_queued: number;
  error: string | null;
}

export type MediaKind = "video" | "image" | "audio" | "attachment";
export type MediaStatus =
  | "discovered" | "queued" | "downloading" | "completed" | "failed" | "failed_permanent"
  | "cancelled" | "skipped" | "unsupported" | "unsupported_drm" | "no_access";
export type PostStatus = "new" | "no_access" | "no_media" | "pending" | "completed" | "partial" | "unsupported" | "skipped";

export interface MediaItem {
  id: number;
  post_id: number;
  media_key: string;
  kind: MediaKind;
  source: string;
  status: MediaStatus;
  status_reason: string | null;
  wanted: boolean;
  remote_file_name: string | null;
  mimetype: string | null;
  remote_size_bytes: number | null;
  attempts: number;
  next_retry_at: string | null;
  last_error: string | null;
  file_path: string | null;
  file_size_bytes: number | null;
  completed_at: string | null;
  order_index: number;
}

export interface MediaSummary {
  total: number;
  completed: number;
  pending: number;
  failed: number;
  unsupported: number;
  skipped: number;
}

export interface Post {
  id: number;
  post_id: string;
  creator_id: number;
  creator_name: string | null;
  title: string;
  post_type: string | null;
  url: string | null;
  published_at: string | null;
  edited_at: string | null;
  current_user_can_view: boolean;
  thumbnail_url: string | null;
  embed_provider: string | null;
  status: PostStatus;
  status_reason: string | null;
  folder_path: string | null;
  first_seen_at: string;
  media_summary: MediaSummary;
}

export interface PostDetail extends Post {
  teaser_text: string | null;
  media_items: MediaItem[];
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface Job {
  id: number;
  media_item_id: number;
  post_id: number;
  creator_id: number;
  creator_name: string | null;
  post_title: string | null;
  media_kind: MediaKind | null;
  source: string | null;
  file_name: string | null;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  priority: number;
  attempt: number;
  progress_percent: number | null;
  bytes_downloaded: number | null;
  total_bytes: number | null;
  speed_bps: number | null;
  eta_seconds: number | null;
  stage: string | null;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  error_class: string | null;
  created_at: string;
}

export interface FailedMedia {
  media_item_id: number;
  post_id: number;
  creator_id: number;
  creator_name: string | null;
  post_title: string | null;
  media_kind: MediaKind;
  source: string;
  status: MediaStatus;
  status_reason: string | null;
  attempts: number;
  next_retry_at: string | null;
  last_error: string | null;
}

export interface Queue {
  paused: boolean;
  paused_reason: string | null;
  jobs: Job[];
  failed: FailedMedia[];
}

export interface HistoryEvent {
  id: number;
  occurred_at: string;
  event_type: string;
  level: "info" | "warning" | "error";
  creator_id: number | null;
  creator_name: string | null;
  post_id: number | null;
  post_title: string | null;
  media_item_id: number | null;
  message: string;
  data: Record<string, unknown> | null;
}

export interface AuthStatus {
  provider?: string;
  state: "unknown" | "unconfigured" | "valid" | "invalid" | "challenge" | "error";
  checked_at: string | null;
  user_name: string | null;
  user_id?: string | null;
  error: string | null;
}

export interface Settings {
  patreon: {
    session_id: string;
    cookies_txt: string;
    has_cookies_txt: boolean;
    user_agent: string;
    requests_per_second: number;
    random_delay_min: number;
    random_delay_max: number;
    http_backend: "httpx" | "curl_cffi";
    impersonate_target: string;
  };
  onlyfans: {
    sess: string;
    auth_id: string;
    x_bc: string;
    cookies_txt: string;
    has_cookies_txt: boolean;
    user_agent: string;
    requests_per_second: number;
    random_delay_min: number;
    random_delay_max: number;
    dynamic_rules_url: string;
    include_archived: boolean;
    include_messages: boolean;
    http_backend: "httpx" | "curl_cffi";
    impersonate_target: string;
  };
  scan: {
    interval_minutes: number;
    overlap_posts: number;
    full_rescan_days: number;
    default_auto_download: boolean;
    default_include_images: boolean;
    default_include_audio: boolean;
    default_include_attachments: boolean;
  };
  downloads: {
    concurrency: number;
    max_per_creator: number;
    min_free_mb: number;
    max_attempts: number;
    retry_base_seconds: number;
    retry_cap_seconds: number;
    url_max_age_minutes: number;
    hls_fragment_concurrency: number;
    compute_sha256: boolean;
    deduplicate: boolean;
    video_format: string;
    ytdlp_remote_components: boolean;
  };
  naming: {
    post_folder_template: string;
    file_template: string;
    max_component_length: number;
    write_sidecars: boolean;
    write_nfo: boolean;
    embed_metadata: boolean;
  };
  notifications: {
    webhook_url: string;
    discord_webhook: string;
    ntfy_url: string;
    notify_auth_invalid: boolean;
    notify_scan_failed: boolean;
    notify_download_failed: boolean;
    notify_scan_completed: boolean;
  };
  history: { retention_days: number; job_retention_days: number };
  env: { config_dir: string; download_dir: string; onlyfans_download_dir: string | null; port: number; log_level: string; ffmpeg: string | null };
}

export type SettingsPatch = {
  [G in Exclude<keyof Settings, "env">]?: Partial<Settings[G]>;
};

export interface AuthTestResult {
  ok: boolean;
  reason?: string;
  detail?: string;
  user?: { id: string; full_name: string | null; vanity: string | null };
}

export interface SystemStatus {
  version: string;
  started_at: string;
  uptime_seconds: number;
  auth: AuthStatus;
  providers: ProviderInfo[];
  scan: { running: { creator_id: number; mode: string } | null; pending: { creator_id: number; mode: string; trigger: string }[] };
  next_scan_at: string | null;
  downloads: { paused: boolean; paused_reason: string | null; workers: number; running_jobs: number[]; free_bytes: number };
  counts: { creators: number; posts: number; media_completed: number; media_bytes: number; provider_bytes: Record<string, number>; queued: number; running: number; failed: number };
  disk: { free_bytes: number | null; total_bytes: number | null; used_bytes: number | null };
  paths: { config_dir: string; download_dir: string; onlyfans_download_dir: string | null };
  ffmpeg: string | null;
  ytdlp_version: string;
  http_backend: string;
  curl_cffi_available: boolean;
}

export interface TaskInfo {
  name: string;
  description: string;
  interval_seconds: number | null;
  next_run_at: string | null;
  last_run_at: string | null;
  last_status: string | null;
  last_error: string | null;
  running: boolean;
}

export interface LogLine {
  time: string;
  level: string;
  logger: string;
  message: string;
}
