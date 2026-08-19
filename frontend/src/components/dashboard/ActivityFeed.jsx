import { useEffect, useState } from "react";


const EVENTS_URL =
  "http://127.0.0.1:8000/api/events?limit=50";


function formatTime(value) {
  if (!value) {
    return "--:--:--";
  }

  const normalized = value.includes("T")
    ? value
    : `${value.replace(" ", "T")}Z`;

  const date = new Date(normalized);

  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleTimeString();
}


function indicatorColor(eventType) {
  if (eventType === "completed") {
    return "#4de3a5";
  }

  if (eventType === "error" || eventType === "failed") {
    return "#ff7b7b";
  }

  if (eventType === "started" || eventType === "running") {
    return "#55a7ff";
  }

  return "#aab8c8";
}


export default function ActivityFeed({ online }) {
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;

    async function loadEvents() {
      try {
        const response = await fetch(EVENTS_URL);
        const data = await response.json().catch(() => ({}));

        if (!response.ok) {
          throw new Error(
            data.detail || "Failed to load agent activity.",
          );
        }

        if (!cancelled) {
          setEvents(data.events || []);
          setError("");
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError.message);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    loadEvents();

    const timer = setInterval(loadEvents, 3000);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return (
    <section className="panel activity-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">AGENT JOURNAL</p>
          <h2>Live Activity</h2>
        </div>

        <span
          style={{
            color: online ? "#4de3a5" : "#ff7b7b",
            fontSize: "13px",
          }}
        >
          {online ? "● Live" : "● Offline"}
        </span>
      </div>

      {error && (
        <p style={{ color: "#ff7b7b" }}>{error}</p>
      )}

      <div className="activity-list">
        {loading && events.length === 0 && (
          <article className="activity-item">
            <div>
              <strong>Loading activity…</strong>
              <p>Connecting to the Agent Journal.</p>
            </div>
          </article>
        )}

        {!loading && events.length === 0 && !error && (
          <article className="activity-item">
            <div>
              <strong>No agent events yet</strong>
              <p>Run a mission to begin the journal.</p>
            </div>
          </article>
        )}

        {events.map((event) => (
          <article className="activity-item" key={event.id}>
            <span
              className="activity-indicator"
              style={{
                marginTop: 6,
                background: indicatorColor(event.event_type),
              }}
            />

            <div>
              <strong>
                {formatTime(event.created_at)}
                {" · "}
                {event.agent}
              </strong>

              <p>
                Mission #{event.mission_id ?? "System"}
                {" · "}
                {event.message}
              </p>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
