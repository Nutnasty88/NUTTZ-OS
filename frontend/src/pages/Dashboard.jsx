import { useState } from "react";
import MissionManager from "../components/missions/MissionManager";
import ChatPanel from "../chat/ChatPanel";
import AICenter from "../components/AICenter";
import MissionQueue from "../components/dashboard/MissionQueue";
import ActivityFeed from "../components/dashboard/ActivityFeed";
import SystemMonitor from "../components/system/SystemMonitor";

export default function Dashboard({ backendOnline }) {
  const [showMissionManager, setShowMissionManager] = useState(false);
  return (
    <div className="dashboard-grid">
      <section className="chat-section">
        <ChatPanel />
      </section>

      <aside className="right-panel">
        <div className="panel-card">
          <h2>Backend Status</h2>
          <p>{backendOnline ? "🟢 Online" : "🔴 Offline"}</p>
        </div>

        <div className="panel-card">
          <AICenter />
        </div>

        <div className="panel-card">
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "12px",
            }}
          >
            <h2>📋 Mission Control</h2>

            <button
              type="button"
              onClick={() => setShowMissionManager(true)}
              style={{
                padding: "8px 14px",
                borderRadius: "8px",
                border: "none",
                cursor: "pointer",
                fontWeight: "bold",
              }}
            >
              + New Mission
            </button>
          </div>

          <MissionQueue />
        </div>

        <ActivityFeed />
        <SystemMonitor />
      </aside>
      <MissionManager
  open={showMissionManager}
  onClose={() => setShowMissionManager(false)}
  onCreated={() => window.location.reload()}
/>
    </div>
  );
}
