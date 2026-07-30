import { useEffect, useState } from "react";

export default function MissionQueue() {
  const [missions, setMissions] = useState([]);

  useEffect(() => {
    async function loadMissions() {
      try {
        const response = await fetch("http://127.0.0.1:8000/api/missions");
        const data = await response.json();

        setMissions(
          data.map((mission) => ({
            id: mission.id,
            name: mission.title,
            status: mission.status,
            progress:
              mission.status === "Completed"
                ? 100
                : mission.status === "Running"
                ? 60
                : 0,
          }))
        );
      } catch (err) {
        console.error(err);
      }
    }

    loadMissions();

    const timer = setInterval(loadMissions, 5000);

    return () => clearInterval(timer);
  }, []);

  return (
    <div>
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
