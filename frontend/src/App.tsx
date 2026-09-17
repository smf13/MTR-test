import { Suspense, lazy } from "react";
import { BrowserRouter, Route, Routes, Navigate } from "react-router-dom";
import { Layout } from "./components/Layout";
import { ToastProvider } from "./components/Toast";
import { TagColorsProvider } from "./components/Tags";
import { Dashboard } from "./pages/Dashboard";

// The dashboard is the landing page and ships in the main bundle; every other page (and, inside the
// dashboard, the Recharts-based overview chart) is fetched on demand so the first paint stays small.
const TargetDetail = lazy(() => import("./pages/TargetDetail").then((m) => ({ default: m.TargetDetail })));
const RunView = lazy(() => import("./pages/RunView").then((m) => ({ default: m.RunView })));
const Events = lazy(() => import("./pages/Events").then((m) => ({ default: m.Events })));
const Settings = lazy(() => import("./pages/Settings").then((m) => ({ default: m.Settings })));
const QuickTrace = lazy(() => import("./pages/QuickTrace").then((m) => ({ default: m.QuickTrace })));

function PageLoading() {
  return <div className="py-20 text-center text-sm text-faint">Loading…</div>;
}

export default function App() {
  return (
    <BrowserRouter>
      <ToastProvider>
        <TagColorsProvider>
          <Layout>
            <Suspense fallback={<PageLoading />}>
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/targets/:id" element={<TargetDetail />} />
                <Route path="/runs/:id" element={<RunView />} />
                <Route path="/events" element={<Events />} />
                <Route path="/trace" element={<QuickTrace />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </Suspense>
          </Layout>
        </TagColorsProvider>
      </ToastProvider>
    </BrowserRouter>
  );
}
