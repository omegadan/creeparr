import { useEffect, useMemo, useState } from "react";
import { useImportPledges, usePledges } from "../../api/hooks/useCreators";
import { useSettings } from "../../api/hooks/useSettings";
import type { CreatorDefaults } from "../../api/types";
import { Modal } from "../ui/Modal";
import { Button } from "../ui/Button";
import { Avatar, Spinner } from "../ui/Misc";
import { Badge } from "../ui/Badge";
import { CreatorOptions } from "./CreatorOptions";
import { useToast } from "../ui/Toast";
import { cx } from "../../lib/format";

export function ImportPledgesModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const pledges = usePledges(open);
  const settings = useSettings();
  const importMut = useImportPledges();
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

  const available = useMemo(() => (pledges.data ?? []).filter((p) => !p.already_added), [pledges.data]);
  useEffect(() => {
    if (pledges.data) setSelected(new Set(available.map((p) => p.campaign_id)));
  }, [pledges.data, available]);

  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const doImport = () =>
    importMut.mutate(
      { campaign_ids: [...selected], defaults },
      {
        onSuccess: (created) => {
          toast(`Imported ${created.length} creator${created.length === 1 ? "" : "s"}`, "success");
          onClose();
        },
        onError: (e) => error(e, "Import failed"),
      },
    );

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Import pledges"
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
      {pledges.isLoading ? (
        <div className="flex justify-center py-10"><Spinner /></div>
      ) : pledges.isError ? (
        <div className="text-sm text-danger">Could not load pledges: {(pledges.error as Error).message}</div>
      ) : (
        <div className="grid gap-4">
          <div className="max-h-80 overflow-y-auto rounded-md border border-line">
            <table className="table">
              <thead>
                <tr>
                  <th className="w-8">
                    <input type="checkbox" checked={selected.size === available.length && available.length > 0} onChange={(e) => setSelected(e.target.checked ? new Set(available.map((p) => p.campaign_id)) : new Set())} />
                  </th>
                  <th>Creator</th>
                  <th>Membership</th>
                </tr>
              </thead>
              <tbody>
                {(pledges.data ?? []).map((p) => (
                  <tr key={p.campaign_id} className={cx(p.already_added && "opacity-50")}>
                    <td>
                      <input type="checkbox" disabled={p.already_added} checked={selected.has(p.campaign_id)} onChange={() => toggle(p.campaign_id)} />
                    </td>
                    <td>
                      <div className="flex items-center gap-2">
                        <Avatar src={p.avatar_url} name={p.name} size={28} />
                        <div>
                          <div className="font-medium">{p.name}</div>
                          <div className="text-xs text-fg-dim">{p.vanity ? `@${p.vanity}` : p.campaign_id}</div>
                        </div>
                      </div>
                    </td>
                    <td>
                      {p.already_added ? <Badge tone="muted">added</Badge> : p.is_free_member ? <Badge tone="warn">free</Badge> : p.is_free_trial ? <Badge tone="info">trial</Badge> : <Badge tone="ok">paid</Badge>}
                    </td>
                  </tr>
                ))}
                {(pledges.data ?? []).length === 0 && (
                  <tr><td colSpan={3} className="py-6 text-center text-fg-muted">No active memberships found on this account.</td></tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="border-t border-line pt-4">
            <div className="label">Defaults for imported creators</div>
            <CreatorOptions value={defaults} onChange={setOpts} compact />
          </div>
        </div>
      )}
    </Modal>
  );
}
