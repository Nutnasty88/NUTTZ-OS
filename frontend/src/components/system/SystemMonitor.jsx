import { useEffect, useState } from "react";
import { getSystem } from "../../services/api";

export default function SystemMonitor() {
  const [system, setSystem] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    async function loadSystem() {
      try {
        const data = await getSystem();
        setSystem(data);
        setError("");
      } catch (err) {
        console.error("System monitor error:", err);
        setError("Unable to load system information.");
      }
    }

    loadSystem();

    const timer = setInterval(loadSystem, 5000);
    return () => clearInterval(timer);
  }, []);

  if (error) {
    return (
      <section className="panel-card">
        <h2>System Monitor</h2>
        <p>{error}</p>
      </section>
    );
  }

  if (!system) {
    return (
      <section className="panel-card">
        <h2>System Monitor</h2>
        <p>Loading system data...</p>
      </section>
    );
  }

  const metrics = [
    {
      label: "CPU",
      value: system.cpu?.usage_percent ?? 0,
      detail: `${system.cpu?.physical_cores ?? 0} physical / ${
        system.cpu?.logical_cores ?? 0
      } logical cores`,
      className: "cpu",
    },
    {
      label: "Memory",
      value: system.memory?.usage_percent ?? 0,
      detail: `${system.memory?.used_gib ?? 0} GiB used of ${
        system.memory?.total_gib ?? 0
      } GiB`,
      className: "ram",
    },
    {
      label: "Storage",
      value: system.storage?.usage_percent ?? 0,
      detail: `${system.storage?.used_gib ?? 0} GiB used of ${
        system.storage?.total_gib ?? 0
      } GiB`,
      className: "disk",
    },
  ];

  return (
    <section className="panel-card system-monitor">
      <div className="system-monitor-heading">
        <div>
          <p className="card-eyebrow">LIVE TELEMETRY</p>
          <h2>System Monitor</h2>
        </div>

        <span className="system-online">Online</span>
      </div>

      <div className="system-metrics">
        {metrics.map((metric) => (
          <div className="metric" key={metric.label}>
            <div className="metric-header">
              <span>{metric.label}</span>
              <strong>{Number(metric.value).toFixed(1)}%</strong>
            </div>

            <div
              className="progress-bar"
              role="progressbar"
              aria-label={`${metric.label} usage`}
              aria-valuemin="0"
              aria-valuemax="100"
              aria-valuenow={metric.value}
            >
              <div
                className={`progress-fill ${metric.className}`}
                style={{
                  width: `${Math.min(Math.max(metric.value, 0), 100)}%`,
                }}
              />
            </div>

            <p className="metric-detail">{metric.detail}</p>
          </div>
        ))}
      </div>

      <div className="system-details">
        <div>
          <span>Host</span>
          <strong>{system.hostname}</strong>
        </div>

        <div>
          <span>Operating system</span>
          <strong>
            {system.operating_system} {system.os_release}
          </strong>
        </div>

        <div>
          <span>Architecture</span>
          <strong>{system.architecture}</strong>
        </div>

        <div>
          <span>Uptime</span>
          <strong>{system.uptime?.formatted ?? "Unknown"}</strong>
        </div>
      </div>
    </section>
  );
}
