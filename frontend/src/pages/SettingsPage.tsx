import { NavLink, useParams } from "react-router";
import { useSettings } from "../api/hooks/useSettings";
import { PageHeader } from "../components/layout/AppShell";
import { Spinner } from "../components/ui/Misc";
import { PatreonSettings } from "../components/settings/PatreonSettings";
import { GeneralSettings } from "../components/settings/GeneralSettings";
import { DownloadSettings } from "../components/settings/DownloadSettings";
import { ScanSettings } from "../components/settings/ScanSettings";
import { cx } from "../lib/format";

const TABS = [
  { id: "patreon", label: "Patreon" },
  { id: "scanning", label: "Scanning" },
  { id: "downloads", label: "Downloads" },
  { id: "general", label: "Naming & general" },
];

export function SettingsPage() {
  const tab = useParams().tab ?? "patreon";
  const settings = useSettings();
  return (
    <>
      <PageHeader title="Settings" />
      <div className="mb-5 flex gap-1 border-b border-line">
        {TABS.map((t) => (
          <NavLink key={t.id} to={`/settings/${t.id}`} className={({ isActive }) => cx("-mb-px border-b-2 px-3 py-2 text-sm", isActive ? "border-accent text-fg" : "border-transparent text-fg-muted hover:text-fg")}>
            {t.label}
          </NavLink>
        ))}
      </div>
      {settings.isLoading || !settings.data ? (
        <div className="flex justify-center py-20"><Spinner /></div>
      ) : (
        <div className="max-w-3xl">
          {tab === "patreon" && <PatreonSettings settings={settings.data} />}
          {tab === "scanning" && <ScanSettings settings={settings.data} />}
          {tab === "downloads" && <DownloadSettings settings={settings.data} />}
          {tab === "general" && <GeneralSettings settings={settings.data} />}
        </div>
      )}
    </>
  );
}
