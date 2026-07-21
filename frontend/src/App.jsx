import { useEffect, useState } from "react";
import "./App.css";

import Sidebar from "./components/layout/Sidebar";
import TopBar from "./components/layout/TopBar";
import Dashboard from "./pages/Dashboard";

function App() {
  const [backendOnline, setBackendOnline] = useState(false);

  useEffect(() => {
    fetch("http://127.0.0.1:8000/api/system")
      .then((res) => {
        if (res.ok) setBackendOnline(true);
      })
      .catch(() => setBackendOnline(false));
  }, []);

  return (
    <div className="app-shell">
      <Sidebar />

      <div className="main-content">
        <TopBar />

        <main className="dashboard">
          <Dashboard backendOnline={backendOnline} />
        </main>
      </div>
    </div>
  );
}

export default App;