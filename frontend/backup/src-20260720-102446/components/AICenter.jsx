import { useEffect, useState } from "react";
import { api } from "../services/api";

export default function AICenter() {
  const [status, setStatus] = useState(null);
  const [models, setModels] = useState([]);
  const [loading, setLoading] = useState(true);

  async function load() {
    try {
      setLoading(true);

      const [statusData, modelData] = await Promise.all([
        api.getOllamaStatus(),
        api.getOllamaModels(),
      ]);

      setStatus(statusData);
      setModels(modelData.models || []);
    } catch (err) {
      console.error(err);
      setStatus({
        connected: false,
        error: err.message,
      });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  return (
    <div className="status-card">
      <h2>🧠 AI Center</h2>

      {loading ? (
        <p>Loading...</p>
      ) : (
        <>
          <p>
            Status:{" "}
            {status?.connected ? (
              <span style={{ color: "limegreen" }}>● Connected</span>
            ) : (
              <span style={{ color: "red" }}>● Offline</span>
            )}
          </p>

          {status?.version && (
            <p>Version: {status.version}</p>
          )}

          <h3>Installed Models</h3>

          <ul>
            {models.length === 0 ? (
              <li>No models found.</li>
            ) : (
              models.map((model) => (
                <li key={model.name}>
                  🧠 {model.name}
                </li>
              ))
            )}
          </ul>

          <button onClick={load}>Refresh</button>
        </>
      )}
    </div>
  );
}
