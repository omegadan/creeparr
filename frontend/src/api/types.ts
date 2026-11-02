// The API's shapes, by the names the UI uses. They're generated from the backend's models
// into schema.ts (`make api-types`); add a name here rather than writing a shape by hand.
import type * as S from "./schema";

export type { AuthStatus, ItemAction, MediaKind, MediaStatus, PostAction, PostStatus, ServerEvents } from "./schema";

export type Creator = S.CreatorOut;
export type CreatorStats = S.CreatorStats;
/** Creator switches, as sent when adding creators (defaults) or editing one. */
export type CreatorDefaults = S.CreatorPatch;
export type CreatorPreview = S.CreatorPreview;
export type Subscription = S.SubscriptionOut;
export type ScanRun = S.ScanRunOut;

/** A provider as its description says, plus its account state. */
export type ProviderInfo = S.ProviderOut;
export type CredentialField = S.CredentialFieldOut;

export type MediaItem = S.MediaItemOut;
export type MediaSummary = S.MediaSummary;
export type Post = S.PostOut;
export type PostDetail = S.PostDetailOut;
export type PostPage = S.Page_PostOut;

export type Job = S.JobOut;
export type FailedMedia = S.FailedMediaOut;
export type ProviderQueueStatus = S.ProviderQueueStatus;
export type Queue = S.QueueOut;

export type HistoryEvent = S.HistoryOut;
export type HistoryPage = S.Page_HistoryOut;

export type Settings = S.SettingsOut;
export type SettingsPatch = {
  [G in Exclude<keyof Settings, "env">]?: Partial<Settings[G]>;
};
export type AuthTestResult = S.AuthTestResultOut;

export type SystemStatus = S.SystemStatusOut;
export type TaskInfo = S.TaskInfoOut;
export type LogLine = S.LogLineOut;
