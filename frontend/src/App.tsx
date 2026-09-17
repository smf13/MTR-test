import { BrowserRouter, Route, Routes, Navigate } from "react-router-dom";
import { Layout } from "./components/Layout";
import { ToastProvider } from "./components/Toast";
import { TagColorsProvider } from "./components/Tags";
import { Dashboard } from "./pages/Dashboard";
import { TargetDetail } from "./pages/TargetDetail";
import { RunView } from "./pages/RunView";
import { Events } from "./pages/Events";
import { Settings } from "./pages/Settings";
import { QuickTrace } from "./pages/QuickTrace";

export default function App() {
  return (
    <BrowserRouter>
      <ToastProvider>
        <TagColorsProvider>
          <Layout>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/targets/:id" element={<TargetDetail />} />
              <Route path="/runs/:id" element={<RunView />} />
              <Route path="/events" element={<Events />} />
              <Route path="/trace" element={<QuickTrace />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Layout>
        </TagColorsProvider>
      </ToastProvider>
    </BrowserRouter>
  );
}
