const missions = [
  {
    id: 1,
    name: "Build Dashboard",
    status: "Running",
    progress: 82,
  },
  {
    id: 2,
    name: "Research Docker API",
    status: "Working",
    progress: 41,
  },
  {
    id: 3,
    name: "Waiting for Task",
    status: "Idle",
    progress: 0,
  },
];

export default function MissionQueue() {
  return (
    <div className="panel-card">
      <h2>📋 Mission Queue</h2>

      {missions.map((mission) => (
        <div key={mission.id} className="mission-item">
          <div className="mission-header">
            <strong>{mission.name}</strong>
            <span>{mission.progress}%</span>
          </div>

          <div className="mission-status">
            {mission.status}
          </div>

          <div className="progress-bar">
            <div
              className="progress-fill"
              style={{ width: `${mission.progress}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}