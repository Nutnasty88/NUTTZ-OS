import { useEffect, useState } from "react";
import ChatPanel from "./chat/ChatPanel";
import AICenter from "./components/AICenter";

import "./App.css";

export default function App() {
  const [status, setStatus] = useState("Connecting...");
  const [time, setTime] = useState("");

  useEffect(() => {
    async function load() {
      try {
        const response = await fetch("http://127.0.0.1:8000/api/system");
        const data = await response.json();

        setStatus("Backend Online");
        setTime(new Date().toLocaleTimeString());

        console.log(data);
      } catch (err) {
        setStatus("Backend Offline");
      }
    }

    load();
  }, []);

  return (
    <div className="app">
      <header className="header">
        <h1>NUTTZ OS</h1>
        <p>{status}</p>
      </header>

      <main className="dashboard">
        <div className="card">
          <h2>System Status</h2>
          <p>{status}</p>
        </div>

        <div className="card">
          <h2>Last Check</h2>
          <p>{time}</p>
        </div>

        <ChatPanel />

        <AICenter />
      </main>
    </div>
  );
}