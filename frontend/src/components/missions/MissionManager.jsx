import { useState } from "react";

export default function MissionManager({
  open,
  onClose,
  onCreated,
}) {
  const [title, setTitle] = useState("");
  const [agent, setAgent] = useState("Planner");
  const [priority, setPriority] = useState("Normal");
  const [loading, setLoading] = useState(false);

  if (!open) return null;

  async function createMission(e) {
    e.preventDefault();

    setLoading(true);

    try {
      const response = await fetch(
        "http://127.0.0.1:8000/api/missions",
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            title,
            assigned_agent: agent,
            priority,
          }),
        }
      );

      if (!response.ok) {
        throw new Error("Failed to create mission");
      }

      setTitle("");
      setAgent("Planner");
      setPriority("Normal");

      if (onCreated) onCreated();

      onClose();
    } catch (err) {
      alert(err.message);
    }

    setLoading(false);
  }

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,.65)",
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        zIndex: 9999,
      }}
    >
      <div
        style={{
          width: "520px",
          background: "#1e293b",
          color: "white",
          borderRadius: "12px",
          padding: "24px",
        }}
      >
        <h2>Create Mission</h2>

        <form onSubmit={createMission}>
          <div style={{ marginBottom: 16 }}>
            <label>Mission Title</label>

            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              required
              style={{
                width: "100%",
                marginTop: 6,
                padding: 10,
              }}
            />
          </div>

          <div style={{ marginBottom: 16 }}>
            <label>Assigned Agent</label>

            <select
              value={agent}
              onChange={(e) => setAgent(e.target.value)}
              style={{
                width: "100%",
                marginTop: 6,
                padding: 10,
              }}
            >
              <option>Planner</option>
              <option>Researcher</option>
              <option>Builder</option>
              <option>Tester</option>
              <option>Reporter</option>
            </select>
          </div>

          <div style={{ marginBottom: 20 }}>
            <label>Priority</label>

            <select
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
              style={{
                width: "100%",
                marginTop: 6,
                padding: 10,
              }}
            >
              <option>Low</option>
              <option>Normal</option>
              <option>High</option>
              <option>Critical</option>
            </select>
          </div>

          <div
            style={{
              display: "flex",
              justifyContent: "flex-end",
              gap: 10,
            }}
          >
            <button
              type="button"
              onClick={onClose}
            >
              Cancel
            </button>

            <button
              type="submit"
              disabled={loading}
            >
              {loading ? "Creating..." : "Create Mission"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
