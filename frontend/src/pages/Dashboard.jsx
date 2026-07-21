import ChatPanel from "../chat/ChatPanel";
import AICenter from "../components/AICenter";
import MissionQueue from "../components/dashboard/MissionQueue";
import SystemMonitor from "../components/system/SystemMonitor";

export default function Dashboard({ backendOnline }) {
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

        <MissionQueue />

        <SystemMonitor />
      </aside>
    </div>
  );
}