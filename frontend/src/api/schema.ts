// Generated from the backend's API description by `make api-types`. Do not edit;
// change the pydantic models in backend/creeparr and regenerate.

/** Credential fields for one provider; unknown keys are rejected by the endpoint. */
export type AuthBody = Record<string, never>;

export type AuthState = "unknown" | "unconfigured" | "valid" | "invalid" | "challenge" | "error";

/** A provider's session, as last checked. */
export interface AuthStatus {
  provider: string;
  state: AuthState;
  checked_at?: string | null;
  user_name?: string | null;
  user_id?: string | null;
  error?: string | null;
}

export interface AuthStatusEvent {
  provider: string;
  state: AuthState;
  checked_at?: string | null;
  user_name?: string | null;
  user_id?: string | null;
  error?: string | null;
}

/** Whether a provider's credentials work. Only the fields that apply are sent. */
export interface AuthTestResultOut {
  ok: boolean;
  reason?: ("not_configured" | "auth_invalid" | "cloudflare_challenge" | "network" | "unexpected") | null;
  detail?: string | null;
  user?: AuthUserOut | null;
}

export interface AuthUserOut {
  id: string;
  full_name: string | null;
  vanity: string | null;
}

/** Why a provider's queue is or isn't moving, as the Queue page shows it. */
export type ClaimState = "disabled" | "paused" | "downloading" | "idle" | "creators_off" | "throttled" | "pacing" | "blocked" | "waiting";

export interface ClearedOut {
  cleared: number;
}

export interface CountsOut {
  creators: number;
  posts: number;
  media_completed: number;
  media_missing: number;
  media_bytes: number;
  provider_bytes: { [key: string]: number };
  queued: number;
  running: number;
  failed: number;
}

export interface CreatorChangedEvent {
  id: number;
  deleted?: boolean;
}

export interface CreatorCreate {
  enabled?: boolean | null;
  monitored?: boolean | null;
  auto_download?: boolean | null;
  include_images?: boolean | null;
  include_audio?: boolean | null;
  include_attachments?: boolean | null;
  download_since?: string | null;
  scan_interval_minutes?: number | null;
  /** URL, handle or id at the provider */
  query: string;
  provider?: string;
}

export interface CreatorDefaults {
  enabled?: boolean | null;
  monitored?: boolean | null;
  auto_download?: boolean | null;
  include_images?: boolean | null;
  include_audio?: boolean | null;
  include_attachments?: boolean | null;
  download_since?: string | null;
  scan_interval_minutes?: number | null;
}

export interface CreatorOut {
  id: number;
  provider: string;
  campaign_id: string;
  vanity: string | null;
  name: string;
  url: string | null;
  avatar_url: string | null;
  cover_url: string | null;
  is_nsfw: boolean | null;
  enabled: boolean;
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
  last_scan_status: ScanStatus | null;
  last_scan_error: string | null;
  created_at: string;
  stats: CreatorStats;
  scanning: boolean;
}

export interface CreatorPatch {
  enabled?: boolean | null;
  monitored?: boolean | null;
  auto_download?: boolean | null;
  include_images?: boolean | null;
  include_audio?: boolean | null;
  include_attachments?: boolean | null;
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

export interface CredentialFieldOut {
  key: string;
  label: string;
  hint: string;
  optional: boolean;
  multiline: boolean;
}

export interface DiskOut {
  free_bytes: number | null;
  total_bytes: number | null;
  used_bytes: number | null;
}

export interface DownloadPostBody {
  force?: boolean;
}

export interface DownloadSettingsOut {
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
  verify_files_hours: number;
  requeue_missing: boolean;
  spread_downloads: boolean;
  video_format: string;
  container: "auto" | "mp4" | "mkv";
  ytdlp_remote_components: boolean;
}

export interface DownloadsStatusOut {
  paused: boolean;
  paused_reason: string | null;
  workers: number;
  running_jobs: number[];
  free_bytes: number;
}

export interface FailedMediaOut {
  media_item_id: number;
  post_id: number;
  creator_id: number;
  creator_name: string | null;
  post_title: string | null;
  media_kind: MediaKind;
  source: MediaSource;
  status: MediaStatus;
  status_reason: string | null;
  attempts: number;
  next_retry_at: string | null;
  last_error: string | null;
}

export interface HTTPValidationError {
  detail?: ValidationError[];
}

export interface HealthOut {
  status: "ok" | "degraded";
  db: boolean;
  downloads_writable: boolean;
  ffmpeg: string | null;
  version: string;
}

/** Sent first on every connection, so a reconnect tells the UI which server it reached. */
export interface HelloEvent {
  version: string;
  providers: { [key: string]: AuthStatus };
}

export interface HistoryAddedEvent {
  id: number;
  event_type: string;
  level: string;
  message: string;
  creator_id?: number | null;
  post_id?: number | null;
}

export interface HistoryOut {
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
  data: { [key: string]: unknown } | null;
}

export interface HistorySettingsOut {
  retention_days: number;
  job_retention_days: number;
}

export interface ImportSubscriptionsRequest {
  provider?: string;
  ids: string[];
  defaults?: CreatorDefaults;
}

export interface InstagramSettingsOut {
  enabled: boolean;
  sessionid: string;
  cookies_txt: string;
  user_agent: string;
  include_reels: boolean;
  include_stories: boolean;
  include_highlights: boolean;
  include_tagged: boolean;
  max_posts: number;
  sleep_request: number;
  downloads_per_hour: number;
  has_cookies_txt: boolean;
}

export type ItemAction = "retry" | "redownload" | "skip" | "unskip";

export interface JobFinishedEvent {
  id: number;
  media_item_id: number;
  post_id: number;
  status: JobStatus;
  error?: string | null;
}

export interface JobOut {
  id: number;
  media_item_id: number;
  post_id: number;
  creator_id: number;
  creator_name: string | null;
  post_title: string | null;
  media_kind: MediaKind | null;
  source: MediaSource | null;
  file_name: string | null;
  status: JobStatus;
  priority: number;
  attempt: number;
  progress_percent: number | null;
  bytes_downloaded: number | null;
  total_bytes: number | null;
  speed_bps: number | null;
  eta_seconds: number | null;
  stage: JobStage | null;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  error_class: string | null;
  created_at: string;
}

/** A running download's progress; only the fields that are known are sent. */
export interface JobProgressEvent {
  id: number;
  progress_percent?: number | null;
  bytes_downloaded?: number | null;
  total_bytes?: number | null;
  speed_bps?: number | null;
  eta_seconds?: number | null;
  stage?: JobStage | null;
}

export interface JobRetriedOut {
  job_id: number | null;
}

export type JobStage = "resolving" | "downloading" | "merging" | "verifying" | "embedding";

export interface JobStartedEvent {
  id: number;
  media_item_id: number;
  post_id: number;
}

export type JobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface LogLineOut {
  time: string;
  level: string;
  logger: string;
  message: string;
}

export interface LoginBody {
  password: string;
}

export interface LoginStateOut {
  auth_enabled: boolean;
  authenticated: boolean;
}

export interface LogsOut {
  lines: LogLineOut[];
}

export interface LookupRequest {
  query: string;
  provider?: string;
}

export interface MediaItemOut {
  id: number;
  post_id: number;
  media_key: string;
  kind: MediaKind;
  source: MediaSource;
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
  actions: ItemAction[];
}

export type MediaKind = "video" | "image" | "audio" | "attachment";

export type MediaSource = "native_direct" | "native_hls" | "embed_youtube" | "embed_vimeo" | "embed_other" | "media_download";

export type MediaStatus = "discovered" | "queued" | "downloading" | "completed" | "failed" | "failed_permanent" | "cancelled" | "skipped" | "unsupported" | "unsupported_drm" | "no_access" | "missing";

export interface MediaSummary {
  total: number;
  completed: number;
  pending: number;
  failed: number;
  unsupported: number;
  skipped: number;
}

export interface NamingPreviewBody {
  post_folder_template: string;
  file_template?: string;
}

export interface NamingPreviewOut {
  post_folder: string;
  file: string;
  full_path: string;
}

export interface NamingSettingsOut {
  post_folder_template: string;
  file_template: string;
  max_component_length: number;
  write_sidecars: boolean;
  write_nfo: boolean;
  embed_metadata: boolean;
}

export interface NotificationSettingsOut {
  webhook_url: string;
  discord_webhook: string;
  ntfy_url: string;
  notify_auth_invalid: boolean;
  notify_scan_failed: boolean;
  notify_download_failed: boolean;
  notify_scan_completed: boolean;
}

export interface NotificationTestOut {
  ok: boolean;
  detail?: string | null;
  targets?: string[] | null;
}

export interface OkOut {
  ok: boolean;
}

export interface OnlyFansSettingsOut {
  enabled: boolean;
  sess: string;
  auth_id: string;
  x_bc: string;
  cookies_txt: string;
  user_agent: string;
  requests_per_second: number;
  random_delay_min: number;
  random_delay_max: number;
  dynamic_rules_url: string;
  downloads_per_hour: number;
  include_archived: boolean;
  include_messages: boolean;
  http_backend: "httpx" | "curl_cffi";
  impersonate_target: string;
  has_cookies_txt: boolean;
}

export interface Page_HistoryOut {
  items: HistoryOut[];
  total: number;
  page: number;
  page_size: number;
}

export interface Page_PostOut {
  items: PostOut[];
  total: number;
  page: number;
  page_size: number;
}

export interface PasswordBody {
  password: string;
  current_password?: string | null;
}

export interface PathsOut {
  config_dir: string;
  download_dir: string;
  patreon_download_dir: string | null;
  onlyfans_download_dir: string | null;
  youtube_download_dir: string | null;
  instagram_download_dir: string | null;
  reddit_download_dir: string | null;
}

export interface PatreonSettingsOut {
  enabled: boolean;
  session_id: string;
  cookies_txt: string;
  user_agent: string;
  requests_per_second: number;
  random_delay_min: number;
  random_delay_max: number;
  downloads_per_hour: number;
  http_backend: "httpx" | "curl_cffi";
  impersonate_target: string;
  has_cookies_txt: boolean;
}

export interface PendingScanOut {
  creator_id: number;
  mode: ScanMode;
  trigger: string;
}

export type PostAction = "download" | "force_download" | "skip" | "unskip";

export interface PostChangedEvent {
  id: number;
  creator_id: number;
  status: PostStatus;
}

export interface PostDetailOut {
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
  teaser_text: string | null;
  media_items: MediaItemOut[];
  actions: PostAction[];
}

export interface PostOut {
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
  actions: PostAction[];
}

export type PostStatus = "new" | "no_access" | "no_media" | "pending" | "completed" | "partial" | "unsupported" | "skipped";

export interface ProviderOut {
  name: string;
  label: string;
  intro: string;
  credentials: CredentialFieldOut[];
  post_types: { [key: string]: string };
  download_dir_variable: string;
  configured: boolean;
  credential_fields: string[];
  auth: AuthStatus;
}

export interface ProviderQueueStatus {
  provider: string;
  label: string;
  state: ClaimState;
  queued: number;
  running: number;
  hourly_limit: number;
  recent_starts: number;
  next_slot_seconds: number | null;
  next_slot_at: string | null;
}

export type QueueChangedEvent = Record<string, never>;

export interface QueueOut {
  paused: boolean;
  paused_reason: string | null;
  jobs: JobOut[];
  failed: FailedMediaOut[];
  providers: ProviderQueueStatus[];
  running_total: number;
  queued_total: number;
  failed_total: number;
  page: number;
  failed_page: number;
  page_size: number;
}

export interface QueuePausedEvent {
  reason: string;
}

export type QueueResumedEvent = Record<string, never>;

export interface RedditSettingsOut {
  enabled: boolean;
  client_id: string;
  client_secret: string;
  user_agent: string;
  max_posts: number;
  sleep_request: number;
  downloads_per_hour: number;
}

export interface RequeuedOut {
  requeued: number;
}

export interface ScanFinishedEvent {
  scan_run_id: number;
  creator_id: number;
  status: ScanStatus;
  error?: string | null;
}

export type ScanMode = "auto" | "full" | "incremental";

export interface ScanProgressEvent {
  scan_run_id: number;
  creator_id: number;
  seen: number;
  new: number;
}

export interface ScanQueueOut {
  running: ScanSlotOut | null;
  pending: PendingScanOut[];
}

export interface ScanQueuedEvent {
  creator_id: number;
  mode: ScanMode;
}

export interface ScanRequestBody {
  mode?: string;
}

export interface ScanRequestedOut {
  queued: boolean;
  status: ScanQueueOut;
}

export interface ScanRunOut {
  id: number;
  creator_id: number;
  mode: ScanMode;
  trigger: string;
  status: ScanStatus;
  started_at: string;
  finished_at: string | null;
  pages_fetched: number;
  posts_seen: number;
  posts_new: number;
  posts_updated: number;
  media_queued: number;
  error: string | null;
}

export interface ScanSettingsOut {
  interval_minutes: number;
  overlap_posts: number;
  full_rescan_days: number;
  default_auto_download: boolean;
  default_include_images: boolean;
  default_include_audio: boolean;
  default_include_attachments: boolean;
}

export interface ScanSlotOut {
  creator_id: number;
  mode: ScanMode;
}

export interface ScanStartedEvent {
  scan_run_id: number;
  creator_id: number;
  mode: ScanMode;
}

export type ScanStatus = "running" | "ok" | "error" | "cancelled";

export interface ScansQueuedOut {
  queued: number;
}

export interface SecuritySettingsOut {
  auth_enabled: boolean;
  password_hash: string;
  session_epoch: number;
}

/** The test of newly saved credentials, and the provider's settings as now stored. */
export interface SetAuthOut {
  ok: boolean;
  reason?: ("not_configured" | "auth_invalid" | "cloudflare_challenge" | "network" | "unexpected") | null;
  detail?: string | null;
  user?: AuthUserOut | null;
  settings: { [key: string]: unknown };
}

export interface SettingsChangedEvent {
  groups: string[];
}

/** What the container's environment sets; read-only in the UI. */
export interface SettingsEnvOut {
  config_dir: string;
  download_dir: string;
  patreon_download_dir: string | null;
  onlyfans_download_dir: string | null;
  youtube_download_dir: string | null;
  instagram_download_dir: string | null;
  reddit_download_dir: string | null;
  port: number;
  log_level: string;
  ffmpeg: string | null;
}

export interface SettingsOut {
  patreon: PatreonSettingsOut;
  onlyfans: OnlyFansSettingsOut;
  youtube: YouTubeSettingsOut;
  instagram: InstagramSettingsOut;
  reddit: RedditSettingsOut;
  scan: ScanSettingsOut;
  downloads: DownloadSettingsOut;
  naming: NamingSettingsOut;
  notifications: NotificationSettingsOut;
  security: SecuritySettingsOut;
  history: HistorySettingsOut;
  env: SettingsEnvOut;
}

export interface SubscriptionOut {
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

export interface SystemStatusOut {
  version: string;
  started_at: string;
  uptime_seconds: number;
  providers: ProviderOut[];
  scan: ScanQueueOut;
  next_scan_at: string | null;
  downloads: DownloadsStatusOut;
  counts: CountsOut;
  disk: DiskOut;
  paths: PathsOut;
  ffmpeg: string | null;
  ytdlp_version: string;
  http_backend: string;
  curl_cffi_available: boolean;
}

export interface TaskInfoOut {
  name: string;
  description: string;
  interval_seconds: number | null;
  next_run_at: string | null;
  last_run_at: string | null;
  last_status: ("ok" | "skipped" | "error") | null;
  last_error: string | null;
  running: boolean;
}

export interface TaskStartedOut {
  started: boolean;
}

export interface ValidationError {
  loc: (string | number)[];
  msg: string;
  type: string;
  input?: unknown;
  ctx?: { [key: string]: unknown };
}

export interface YouTubeSettingsOut {
  enabled: boolean;
  cookies_txt: string;
  max_videos: number;
  downloads_per_hour: number;
  has_cookies_txt: boolean;
}

/** Each server-sent event's name and its payload. */
export interface ServerEvents {
  "hello": HelloEvent;
  "auth.status": AuthStatusEvent;
  "scan.queued": ScanQueuedEvent;
  "scan.started": ScanStartedEvent;
  "scan.progress": ScanProgressEvent;
  "scan.finished": ScanFinishedEvent;
  "job.progress": JobProgressEvent;
  "job.started": JobStartedEvent;
  "job.finished": JobFinishedEvent;
  "queue.changed": QueueChangedEvent;
  "queue.paused": QueuePausedEvent;
  "queue.resumed": QueueResumedEvent;
  "history.added": HistoryAddedEvent;
  "creator.changed": CreatorChangedEvent;
  "post.changed": PostChangedEvent;
  "settings.changed": SettingsChangedEvent;
}
