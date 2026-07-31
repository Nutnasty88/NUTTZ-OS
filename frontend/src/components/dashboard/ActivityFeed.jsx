export default function ActivityFeed({ online, docker, missions = [] }) {
  const running = docker?.running ?? 0;
  const total = docker?.total ?? 0;

  const journal = [];

  journal.push({
    time: new Date().toLocaleTimeString(),
    text: online
      ? "✅ Backend connected."
      : "❌ Backend offline.",
  });

  journal.push({
    time: new Date().toLocaleTimeString(),
    text: `🤖 Ollama model detected: qwen3:8b`,
  });

  journal.push({
    time: new Date().toLocaleTimeString(),
    text: `🐳 Docker: ${running}/${total} containers running.`,
  });

  missions.forEach((mission) => {
    journal.push({
      time: new Date().toLocaleTimeString(),
      text: `📋 ${mission.name} • ${mission.status}`,
    });
  });

  return (
    <section className="panel activity-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">SYSTEM JOURNAL</p>
          <h2>Live Activity</h2>
        </div>
      </div>

      <div className="activity-list">
        {journal.map((entry, index) => (
          <article className="activity-item" key={index}>
            <span
              className="activity-indicator good"
              style={{ marginTop: 6 }}
            />
            <div>
              <strong>{entry.time}</strong>
              <p>{entry.text}</p>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
