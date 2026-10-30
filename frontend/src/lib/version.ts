/** Watches the server version each event-stream connection reports. A tab's code is the
 * version it loaded; when a reconnect reaches a different one (the container restarted
 * on a new image), the tab is out of date until reloaded. */
export function serverVersionWatcher(): (version: string) => boolean {
  let loaded: string | null = null;
  return (version) => {
    loaded ??= version;
    return version !== loaded;
  };
}
