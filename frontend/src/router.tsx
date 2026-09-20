import { createBrowserRouter, Navigate } from "react-router";
import { AppShell } from "./components/layout/AppShell";
import { CreatorsPage } from "./pages/CreatorsPage";
import { CreatorDetailPage } from "./pages/CreatorDetailPage";
import { QueuePage } from "./pages/QueuePage";
import { HistoryPage } from "./pages/HistoryPage";
import { SettingsPage } from "./pages/SettingsPage";
import { SystemStatusPage } from "./pages/SystemStatusPage";
import { LogsPage } from "./pages/LogsPage";
import { NotFoundPage } from "./pages/NotFoundPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/creators" replace /> },
      { path: "creators", element: <CreatorsPage /> },
      { path: "creators/:id", element: <CreatorDetailPage /> },
      { path: "activity", element: <Navigate to="/activity/queue" replace /> },
      { path: "activity/queue", element: <QueuePage /> },
      { path: "activity/history", element: <HistoryPage /> },
      { path: "settings", element: <Navigate to="/settings/accounts" replace /> },
      { path: "settings/:tab", element: <SettingsPage /> },
      { path: "system", element: <Navigate to="/system/status" replace /> },
      { path: "system/status", element: <SystemStatusPage /> },
      { path: "system/logs", element: <LogsPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);
