import { useEffect, useState } from "react";
import "./App.css";

import Sidebar from "./components/layout/Sidebar";
import TopBar from "./components/layout/TopBar";
import Dashboard from "./pages/Dashboard";

export default function App() {
  const [backendOnline, setBackendOnline] = useState(false);

  useEffect(() => {
    const checkBackend = async () => {
      try {
        console.log("================================");
        console.log("Checking backend...");

        const response = await fetch("http://127.0.0.1:8000/api/system", {
          method: "GET",
          headers: {
            Accept: "application/json",
          },
        });

        console.log("Response object:", response);
        console.log("Status:", response.status);
        console.log("OK:", response.ok);

        const data = await response.json();

        console.log("Backend data:", data);

        setBackendOnline(response.ok);
      } catch (err) {
        console.error("FETCH ERROR:");
        console.error(err);

        setBackendOnline(false);
      }
    };

    checkBackend();

    const timer = setInterval(checkBackend, 5000);

    return () => clearInterval(timer);
  }, []);

  return (
    <div className="app-shell">
      <Sidebar />

      <div className="main-content">
        <TopBar />

        <div
          style={{
            padding: "8px 16px",
            fontWeight: "bold",
            color: backendOnline ? "#4CAF50" : "#FF5555",
          }}
        >
          backendOnline = {String(backendOnline)}
        </div>

        <main className="dashboard">
          <Dashboard backendOnline={backendOnline} />
        </main>
      </div>
    </div>
  );
}
