import { useEffect, useMemo, useState } from "react";
import { useImportSubscriptions, useProviders, useSubscriptions } from "../../api/hooks/useCreators";
import { useSettings } from "../../api/hooks/useSettings";
import type { CreatorDefaults } from "../../api/types";
import { Modal } from "../ui/Modal";
import { Button } from "../ui/Button";
import { Avatar, Spinner } from "../ui/Misc";
import { Badge } from "../ui/Badge";
import { CreatorOptions } from "./CreatorOptions";
import { useToast } from "../ui/Toast";
import { cx } from "../../lib/format";

export function ImportSubscriptionsModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const providers = useProviders();
  const configured = (providers.data ?? []).filter((p) => p.configured);
  const [provider, setProvider] = useState<string>("patreon");
  useEffect(() => {
    if (open && configured.length && !configured.some((p) => p.name === provider)) setProvider(configured[0].name);
  }, [open, configured, provider]);

  const subs = useSubscriptions(provider, open);
  const settings = useSettings();
  const importMut = useImportSubscriptions();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [opts, setOpts] = useState<CreatorDefaults | null>(null);
  const { toast, error } = useToast();

  const defaults: CreatorDefaults = opts ?? {
    monitored: true,
    auto_download: settings.data?.scan.default_auto_download ?? true,
    include_images: settings.data?.scan.default_include_images ?? false,
    include_audio: settings.data?.scan.default_include_audio ?? false,
    include_attachments: settings.data?.scan.default_include_attachments ?? false,
  };

  const available = useMemo(() => (subs.data ?? []).filter((p) => !p.already_added), [subs.data]);
  useEffect(() => {
    if (subs.data) setSelected(new Set(available.map((p) => p.external_id)));
  }, [subs.data, available]);

  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });

  const doImport = () =>
    importMut.mutate(
      { provider, ids: [...selected], defaults },
      {
        onSuccess: (created) => {
          toast(`Imported ${created.length} creator${created.length === 1 ? "" : "s"}`, "success");
          onClose();
        },
        onError: (e) => error(e, "Import failed"),
      },
    );

  const providerLabel = (name: string) => (providers.data ?? []).find((p) => p.name === name)?.label ?? name;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Import subscriptions"
      wide
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={doImport} disabled={selected.size === 0} loading={importMut.isPending}>
            Import {selected.size || ""}
          </Button>
        </>
      }
    >
      {configured.length === 0 ? (
        <div className="text-sm text-fg-muted">No provider is connected yet. Connect one in Settings first.</div>
      ) : (
        <div className="grid gap-4">
          {configured.length > 1 && (
            <div className="flex gap-2">
              {configured.map((p) => (
                <button
                  key={p.name}
                  onClick={() => setProvider(p.name)}
                  className={cx("rounded-md border px-3 py-1.5 text-sm", provider === p.name ? "border-accent bg-accent/15 text-fg" : "border-line text-fg-muted hover:text-fg")}
                >
                  {p.label}
                </button>
              ))}
            </div>
          )}
          {subs.isLoading ? (
            <div className="flex justify-center py-10"><Spinner /></div>
          ) : subs.isError ? (
            <div className="text-sm text-danger">Could not load {providerLabel(provider)} subscriptions: {(subs.error as Error).message}</div>
          ) : (
            <div className="max-h-80 overflow-y-auto rounded-md border border-line">
              <table className="table">
                <thead>
                  <tr>
                    <th className="w-8">
                      <input type="checkbox" checked={selected.size === available.length && available.length > 0} onChange={(e) => setSelected(e.target.checked ? new Set(available.map((p) => p.external_id)) : new Set())} />
                    </th>
                    <th>Creator</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {(subs.data ?? []).map((p) => (
                    <tr key={p.external_id} className={cx(p.already_added && "opacity-50")}>
                      <td><input type="checkbox" disabled={p.already_added} checked={selected.has(p.external_id)} onChange={() => toggle(p.external_id)} /></td>
                      <td>
                        <div className="flex items-center gap-2">
                          <Avatar src={p.avatar_url} name={p.name} size={28} />
                          <div>
                            <div className="font-medium">{p.name}</div>
                            <div className="text-xs text-fg-dim">{p.handle ? `@${p.handle}` : p.external_id}</div>
                          </div>
                        </div>
                      </td>
                      <td>{p.already_added ? <Badge tone="muted">added</Badge> : p.is_free ? <Badge tone="warn">free</Badge> : p.is_trial ? <Badge tone="info">trial</Badge> : <Badge tone="ok">paid</Badge>}</td>
                    </tr>
                  ))}
                  {(subs.data ?? []).length === 0 && (
                    <tr><td colSpan={3} className="py-6 text-center text-fg-muted">No active subscriptions found on this {providerLabel(provider)} account.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
          <div className="border-t border-line pt-4">
            <div className="label">Defaults for imported creators</div>
            <CreatorOptions value={defaults} onChange={setOpts} compact />
          </div>
        </div>
      )}
    </Modal>
  );
}
