import { useState } from "react";
import { useNavigate } from "react-router";
import { Search } from "lucide-react";
import { useAddCreator, useLookupCreator, useProviders } from "../../api/hooks/useCreators";
import type { CreatorPreview, CreatorDefaults } from "../../api/types";
import { useSettings } from "../../api/hooks/useSettings";
import { Modal } from "../ui/Modal";
import { Button } from "../ui/Button";
import { Avatar } from "../ui/Misc";
import { CreatorOptions } from "./CreatorOptions";
import { useToast } from "../ui/Toast";

const PLACEHOLDER: Record<string, string> = {
  patreon: "https://www.patreon.com/c/somecreator",
  onlyfans: "https://onlyfans.com/somecreator",
  youtube: "https://www.youtube.com/@channel  or  @handle",
  instagram: "https://www.instagram.com/username  or  @username",
};

export function AddCreatorModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const providers = useProviders();
  const [provider, setProvider] = useState("patreon");
  const [query, setQuery] = useState("");
  const [preview, setPreview] = useState<CreatorPreview | null>(null);
  const settings = useSettings();
  const [opts, setOpts] = useState<CreatorDefaults | null>(null);
  const lookup = useLookupCreator();
  const add = useAddCreator();
  const nav = useNavigate();
  const { toast, error } = useToast();

  const defaults: CreatorDefaults = opts ?? {
    monitored: true,
    auto_download: settings.data?.scan.default_auto_download ?? true,
    include_images: settings.data?.scan.default_include_images ?? false,
    include_audio: settings.data?.scan.default_include_audio ?? false,
    include_attachments: settings.data?.scan.default_include_attachments ?? false,
  };

  const reset = () => {
    setQuery("");
    setPreview(null);
    setOpts(null);
    onClose();
  };

  const doLookup = () => {
    if (!query.trim()) return;
    lookup.mutate({ query: query.trim(), provider }, { onSuccess: setPreview, onError: (e) => error(e, "Lookup failed") });
  };

  const doAdd = () => {
    add.mutate(
      { query: preview?.external_id ?? query.trim(), provider, ...defaults },
      {
        onSuccess: (c) => {
          toast(`Added ${c.name}; full scan started`, "success");
          reset();
          nav(`/creators/${c.id}`);
        },
        onError: (e) => error(e, "Could not add creator"),
      },
    );
  };

  const providerList = providers.data ?? [{ name: "patreon", label: "Patreon" }, { name: "onlyfans", label: "OnlyFans" }];

  return (
    <Modal
      open={open}
      onClose={reset}
      title="Add creator"
      footer={
        <>
          <Button variant="ghost" onClick={reset}>Cancel</Button>
          <Button variant="primary" onClick={doAdd} disabled={!preview || preview.already_added} loading={add.isPending}>
            Add creator
          </Button>
        </>
      }
    >
      <div className="grid gap-4">
        {providerList.length > 1 && (
          <div>
            <label className="label">Provider</label>
            <div className="flex gap-2">
              {providerList.map((p) => (
                <button
                  key={p.name}
                  onClick={() => { setProvider(p.name); setPreview(null); }}
                  className={`rounded-md border px-3 py-1.5 text-sm ${provider === p.name ? "border-accent bg-accent/15 text-fg" : "border-line text-fg-muted hover:text-fg"}`}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>
        )}
        <div>
          <label className="label">Profile URL, handle or id</label>
          <div className="flex gap-2">
            <input
              className="input"
              autoFocus
              placeholder={PLACEHOLDER[provider] ?? "creator URL or handle"}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && doLookup()}
            />
            <Button onClick={doLookup} loading={lookup.isPending} icon={<Search className="h-4 w-4" />}>
              Look up
            </Button>
          </div>
        </div>
        {preview && (
          <div className="card flex items-center gap-3 p-3">
            <Avatar src={preview.avatar_url} name={preview.name} size={44} />
            <div className="min-w-0 flex-1">
              <div className="truncate font-semibold">{preview.name}</div>
              <div className="truncate text-xs text-fg-muted">
                {preview.handle ? `@${preview.handle}` : preview.external_id} · id {preview.external_id}
              </div>
            </div>
            {preview.already_added && <span className="text-xs text-warn">Already added</span>}
          </div>
        )}
        {preview && !preview.already_added && (
          <div className="border-t border-line pt-4">
            <CreatorOptions value={defaults} onChange={setOpts} compact />
          </div>
        )}
      </div>
    </Modal>
  );
}
