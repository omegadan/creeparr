import { isRouteErrorResponse, useRouteError } from "react-router";
import { Button } from "../ui/Button";

/** Shown instead of a blank page when a page crashes while rendering. */
export function RouteError() {
  const error = useRouteError();
  const message = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : error instanceof Error
      ? error.message
      : String(error);
  return (
    <div className="mx-auto mt-16 max-w-xl rounded-lg border border-danger/30 bg-danger/10 p-5">
      <h1 className="text-base font-semibold text-danger">This page hit an error</h1>
      <p className="mt-2 break-words font-mono text-xs text-fg-muted">{message}</p>
      <p className="mt-3 text-sm text-fg-muted">The rest of Creeparr keeps running; downloads and scans are not affected.</p>
      <div className="mt-4 flex gap-2">
        <Button variant="primary" onClick={() => window.location.reload()}>Reload</Button>
        <Button onClick={() => window.location.assign("/creators")}>Go to Creators</Button>
      </div>
    </div>
  );
}
