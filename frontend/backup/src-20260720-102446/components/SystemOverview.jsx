import StatusCard from "./StatusCard";

function SystemOverview({ system }) {
  if (!system) {
    return (
      <section>
        <div className="section-heading">
          <div>
            <p className="eyebrow">LIVE TELEMETRY</p>
            <h2>System Overview</h2>
          </div>
        </div>

        <div className="overview-grid">
          {[1, 2, 3, 4].map((item) => (
            <div className="status-card loading-card" key={item} />
          ))}
        </div>
      </section>
    );
  }

  return (
    <section>
      <div className="section-heading">
        <div>
          <p className="eyebrow">LIVE TELEMETRY</p>
          <h2>System Overview</h2>
        </div>

        <span className="system-chip">
          {system.operating_system} · {system.architecture}
        </span>
      </div>

      <div className="overview-grid">
        <StatusCard
          label="CPU"
          value={`${system.cpu.usage_percent}%`}
          percent={system.cpu.usage_percent}
          detail={`${system.cpu.physical_cores} physical · ${system.cpu.logical_cores} logical cores`}
        />

        <StatusCard
          label="Memory"
          value={`${system.memory.usage_percent}%`}
          percent={system.memory.usage_percent}
          detail={`${system.memory.used_gib} GiB used of ${system.memory.total_gib} GiB`}
        />

        <StatusCard
          label="Storage"
          value={`${system.storage.usage_percent}%`}
          percent={system.storage.usage_percent}
          detail={`${system.storage.free_gib} GiB free of ${system.storage.total_gib} GiB`}
        />

        <StatusCard
          label="Uptime"
          value={system.uptime.formatted}
          detail={`Kernel ${system.os_release}`}
        />
      </div>
    </section>
  );
}

export default SystemOverview;
