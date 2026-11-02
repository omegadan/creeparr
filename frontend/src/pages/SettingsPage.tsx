import { Navigate, NavLink, useParams } from "react-router";
import type { ReactNode } from "react";
import type { Settings } from "../api/types";
import { useSettings } from "../api/hooks/useSettings";
import { useProviders } from "../api/hooks/useCreators";
import { PageHeader } from "../components/layout/AppShell";
import { Spinner } from "../components/ui/Misc";
import { OnlyFansSettings } from "../components/settings/OnlyFansSettings";
import { InstagramSettings } from "../components/settings/InstagramSettings";
import { RedditSettings } from "../components/settings/RedditSettings";
import { YouTubeSettings } from "../components/settings/YouTubeSettings";
import { NotificationSettings } from "../components/settings/NotificationSettings";
import { SecuritySettings } from "../components/settings/SecuritySettings";
import { PatreonSettings } from "../components/settings/PatreonSettings";
import { GeneralSettings } from "../components/settings/GeneralSettings";
import { DownloadSettings } from "../components/settings/DownloadSettings";
import { ScanSettings } from "../components/settings/ScanSettings";
import { GenericProviderSettings } from "../components/settings/GenericProviderSettings";
import { settingsForm } from "../lib/providers";
import { cx } from "../lib/format";

type ProviderForm = (settings: Settings) => ReactNode;

// Providers with a hand-made settings form; any other gets the generic tab.
const PROVIDER_FORMS: Record<string, ProviderForm> = {
  patreon: (s) => <PatreonSettings settings={s} />,
  onlyfans: (s) => <OnlyFansSettings settings={s} />,
  youtube: (s) => <YouTubeSettings settings={s} />,
  instagram: (s) => <InstagramSettings settings={s} />,
  reddit: (s) => <RedditSettings settings={s} />,
};

const GENERAL_TABS = [
  { id: "scanning", label: "Scanning" },
  { id: "downloads", label: "Downloads" },
  { id: "notifications", label: "Notifications" },
  { id: "security", label: "Security" },
  { id: "general", label: "Naming & general" },
];

export function SettingsPage() {
  const providers = useProviders();
  const tabs = [...(providers.data ?? []).map((p) => ({ id: p.name, label: p.label })), ...GENERAL_TABS];
  const tab = useParams().tab;
  const provider = providers.data?.find((p) => p.name === tab);
  const providerForm =
    provider && settingsForm<ProviderForm>(provider.name, PROVIDER_FORMS, (s) => <GenericProviderSettings name={provider.name} settings={s} />);
  const settings = useSettings();
  // /settings opens the first tab, a provider's once they're known.
  if (!tab && !providers.isLoading) return <Navigate to={`/settings/${tabs[0].id}`} replace />;
  return (
    <>
      <PageHeader title="Settings" />
      <div className="mb-5 flex gap-1 border-b border-line">
        {tabs.map((t) => (
          <NavLink key={t.id} to={`/settings/${t.id}`} className={({ isActive }) => cx("-mb-px border-b-2 px-3 py-2 text-sm", isActive ? "border-accent text-fg" : "border-transparent text-fg-muted hover:text-fg")}>
            {t.label}
          </NavLink>
        ))}
      </div>
      {settings.isLoading || !settings.data ? (
        <div className="flex justify-center py-20"><Spinner /></div>
      ) : (
        <div className="max-w-3xl">
          {providerForm?.(settings.data)}
          {tab === "scanning" && <ScanSettings settings={settings.data} />}
          {tab === "downloads" && <DownloadSettings settings={settings.data} />}
          {tab === "notifications" && <NotificationSettings settings={settings.data} />}
          {tab === "security" && <SecuritySettings />}
          {tab === "general" && <GeneralSettings settings={settings.data} />}
        </div>
      )}
    </>
  );
}
