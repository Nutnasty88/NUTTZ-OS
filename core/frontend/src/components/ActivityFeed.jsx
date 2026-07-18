function ActivityFeed({ online, docker, lastUpdated }) {
  const running = docker?.running ?? 0;
  const total = docker?.total ?? 0;

  const events = [
    {
      title: online ? "NUTTZ Core responding" : "NUTTZ Core unavailable",
      detail: online
        ? "System telemetry is flowing normally."
        : "Check that FastAPI is running on port 8000.",
      level: online ? "good" : "bad",
    },
    {
      title: `${running} Docker containers running`,
      detail: `${total} total containers registered by the engine.`,
      level: running > 0 ? "good" : "neutral",
    },
    {
      title: "Dashboard synchronization",
      detail: lastUpdated
        ? `Last successful refresh at ${lastUpdated.toLocaleTimeString()}.`
        : "Waiting for the first successful refresh.",
      level: "neutral",
    },
  ];

  return (
    <section className="panel activity-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">SYSTEM JOURNAL</p>
          <h2>Live Activity</h2>
        </div>
      </div>

      <div className="activity-list">
        {events.map((event) => (
          <article className="activity-item" key={event.title}>
            <span className={`activity-indicator ${event.level}`} />

            <div>
              <strong>{event.title}</strong>
              <p>{event.detail}</p>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

export default ActivityFeed;
