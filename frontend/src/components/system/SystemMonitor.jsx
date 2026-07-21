import { useEffect, useState } from "react";
import { getSystem } from "../../services/api";

export default function SystemMonitor() {
  const [system, setSystem] = useState(null);

  useEffect(() => {
    async function load() {
      try {
        const data = await getSystem();
        setSystem(data);
      } catch (err) {
        console.error(err);
      }
    }

    load();

    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="panel-card">
      <h2>💻 System Monitor</h2>

      {!system ? (
        <p>Loading...</p>
      ) : (
        <pre>{JSON.stringify(system, null, 2)}</pre>
      )}
    </div>
  );
}