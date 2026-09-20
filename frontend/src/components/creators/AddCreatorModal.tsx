import { useState } from "react";
import { useNavigate } from "react-router";
import { Search } from "lucide-react";
import { useAddCreator, useLookupCreator } from "../../api/hooks/useCreators";
import type { CampaignPreview, CreatorDefaults } from "../../api/types";
import { useSettings } from "../../api/hooks/useSettings";
import { Modal } from "../ui/Modal";
import { Button } from "../ui/Button";
import { Avatar } from "../ui/Misc";
import { CreatorOptions } from "./CreatorOptions";
import { useToast } from "../ui/Toast";

export function AddCreatorModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [preview, setPreview] = useState<CampaignPreview | null>(null);
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
    lookup.mutate(query.trim(), { onSuccess: setPreview, onError: (e) => error(e, "Lookup failed") });
  };

  const doAdd = () => {
    add.mutate(
      { query: preview?.campaign_id ?? query.trim(), ...defaults },
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
        <div>
          <label className="label">Patreon URL, vanity name or campaign id</label>
          <div className="flex gap-2">
            <input
              className="input"
              autoFocus
              placeholder="https://www.patreon.com/c/somecreator"
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
                {preview.creation_name ?? preview.vanity ?? preview.campaign_id} · id {preview.campaign_id}
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
