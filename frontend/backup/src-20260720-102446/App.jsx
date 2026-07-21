import { useEffect, useState } from "react";
import Header from "./components/Header";
import ChatPanel from "./chat/ChatPanel";
import SystemOverview from "./components/SystemOverview"; 
import ContainerList from "./components/ContainerList";
import QuickActions from "./components/QuickActions";
import ActivityFeed from "./components/ActivityFeed";
import "./App.css";

const API_BASE = "http://127.0.0.1:8000";

function App() {
  const [system, setSystem] = useState(null);
  const [docker, setDocker] = useState(null);
  const [lastUpdated, setLastUpdated] = useStae(null);

  async function loadDashboard() {
    try {
      setError("");

      const [systemResponse, dockerResponse] = await Promise.all([
        fetch(`${API_BASE}/api/system`),
        fetch(`${API_BASE}/api/docker`),
      ]);

      if (!systemResponse.ok) {
        throw new Error(`System API returned ${systemResponse.status}`);
      }

      if (!dockerResponse.ok) {
        throw new Error(`Docker API returned ${dockerResponse.status}`);
      }

      const systemData = await systemResponse.json();
      const dockerData = await dockerResponse.json();

      setSystem(systemData);
      setDocker(dockerData);
      setLastUpdated(new Date());
    } catch (err) {
      setError(err.message || "Unable to reach NUTTZ Core.");
    }
  }

  useEffect(() => {
    loadDashboard();

    const timer = setInterval(loadDashboard, 5000);

    return () => clearInterval(timer);
  }, []);

  return (
    <div className="app-shell">
      <Header
        online={!error}
        hostname={system?.hostname}
        lastUpdated={lastUpdated}
      />

      <main className="dashboard">
        {error && (
          <div className="error-banner">
            <strong>NUTTZ Core connection failed.</strong>
            <span>{error}</span>
          </div>
        )}

        <SystemOverview system={system} />

        <section className="dashboard-grid">
          <ContainerList docker={docker} />
          <QuickActions onRefresh={loadDashboard} />
        </section>

        <ActivityFeed
          online={!error}
          docker={docker}
          lastUpdated={lastUpdated}
        />
      </main>
    </div>
  );
}

export default App;
