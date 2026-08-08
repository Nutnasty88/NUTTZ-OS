import { useCallback, useEffect, useState } from "react";


const API_BASE = "http://127.0.0.1:8000/api";

const EMPTY_WORKER = {
  status: "Idle",
  mission_id: null,
  current_task_id: null,
  total_tasks: 0,
  completed_tasks: 0,
  last_message: "Autonomous Worker is idle.",
  last_error: "",
  started_at: null,
  updated_at: null,
  thread_alive: false,
  stop_requested: false,
};


function taskStatusColor(status) {
  if (status === "Completed") {
    return "#4de3a5";
  }

  if (status === "Running") {
    return "#55a7ff";
  }

  if (status === "Error") {
    return "#ff7b7b";
  }

  return "#aab8c8";
}


function workerStatusColor(status) {
  if (status === "Completed") {
    return "#4de3a5";
  }

  if (status === "Running" || status === "Starting") {
    return "#55a7ff";
  }

  if (status === "Pausing" || status === "Paused") {
    return "#ffd166";
  }

  if (status === "Error") {
    return "#ff7b7b";
  }

  return "#aab8c8";
}


export default function MissionQueue() {
  const [missions, setMissions] = useState([]);
  const [runningMissionId, setRunningMissionId] = useState(null);
  const [executingMissionId, setExecutingMissionId] =
    useState(null);
  const [workerActionMissionId, setWorkerActionMissionId] =
    useState(null);

  const [openPlanId, setOpenPlanId] = useState(null);
  const [openTasksId, setOpenTasksId] = useState(null);

  const [plans, setPlans] = useState({});
  const [tasksByMission, setTasksByMission] = useState({});
  const [errors, setErrors] = useState({});
  const [worker, setWorker] = useState(EMPTY_WORKER);


  const loadMissions = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/missions/`);
      const data = await response.json().catch(() => []);

      if (!response.ok) {
        throw new Error("Failed to load missions.");
      }

      setMissions(
        data.map((mission) => ({
          id: mission.id,
          name: mission.title,
          status: mission.status,
          agent: mission.agent,
          priority: mission.priority,
          progress: mission.progress ?? 0,
        })),
      );
    } catch (error) {
      console.error("Mission loading error:", error);
    }
  }, []);


  const loadTasks = useCallback(async (missionId) => {
    const response = await fetch(
      `${API_BASE}/missions/${missionId}/tasks`,
    );

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(
        data.detail || "Failed to load mission tasks.",
      );
    }

    const tasks = data.tasks || [];

    setTasksByMission((current) => ({
      ...current,
      [missionId]: tasks,
    }));

    return tasks;
  }, []);


  const loadWorkerStatus = useCallback(async (missionId) => {
    const response = await fetch(
      `${API_BASE}/missions/${missionId}/worker/status`,
    );

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(
        data.detail || "Failed to load worker status.",
      );
    }

    const nextWorker = data.worker || EMPTY_WORKER;
    setWorker(nextWorker);

    return nextWorker;
  }, []);


  useEffect(() => {
    loadMissions();

    const timer = setInterval(loadMissions, 5000);

    return () => clearInterval(timer);
  }, [loadMissions]);


  const statusProbeMissionId = missions[0]?.id;


  useEffect(() => {
    if (!statusProbeMissionId) {
      return undefined;
    }

    let cancelled = false;

    async function refreshWorker() {
      try {
        const nextWorker = await loadWorkerStatus(
          statusProbeMissionId,
        );

        if (cancelled) {
          return;
        }

        if (nextWorker.mission_id) {
          await Promise.all([
            loadMissions(),
            loadTasks(nextWorker.mission_id),
          ]);
        }
      } catch (error) {
        console.error("Worker status error:", error);
      }
    }

    refreshWorker();

    const timer = setInterval(refreshWorker, 3000);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [
    loadMissions,
    loadTasks,
    loadWorkerStatus,
    statusProbeMissionId,
  ]);


  async function runMission(missionId) {
    setRunningMissionId(missionId);

    setErrors((current) => ({
      ...current,
      [missionId]: "",
    }));

    try {
      const response = await fetch(
        `${API_BASE}/missions/${missionId}/run`,
        {
          method: "POST",
        },
      );

      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          data.detail || "Planner Agent failed to start.",
        );
      }

      if (data.planner) {
        setPlans((current) => ({
          ...current,
          [missionId]: data.planner,
        }));
      }

      if (data.tasks) {
        setTasksByMission((current) => ({
          ...current,
          [missionId]: data.tasks,
        }));
      }

      setOpenPlanId(null);
      setOpenTasksId(missionId);
    } catch (error) {
      setErrors((current) => ({
        ...current,
        [missionId]: error.message,
      }));
    } finally {
      setRunningMissionId(null);
      await loadMissions();
    }
  }


  async function togglePlan(missionId) {
    if (openPlanId === missionId) {
      setOpenPlanId(null);
      return;
    }

    setErrors((current) => ({
      ...current,
      [missionId]: "",
    }));

    setOpenTasksId(null);

    if (plans[missionId]) {
      setOpenPlanId(missionId);
      return;
    }

    try {
      const response = await fetch(
        `${API_BASE}/missions/${missionId}/plan`,
      );

      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          data.detail || "No saved plan was found.",
        );
      }

      setPlans((current) => ({
        ...current,
        [missionId]: data,
      }));

      setOpenPlanId(missionId);
    } catch (error) {
      setErrors((current) => ({
        ...current,
        [missionId]: error.message,
      }));
    }
  }


  async function toggleTasks(missionId) {
    if (openTasksId === missionId) {
      setOpenTasksId(null);
      return;
    }

    setErrors((current) => ({
      ...current,
      [missionId]: "",
    }));

    setOpenPlanId(null);

    try {
      await loadTasks(missionId);
      setOpenTasksId(missionId);
    } catch (error) {
      setErrors((current) => ({
        ...current,
        [missionId]: error.message,
      }));
    }
  }


  async function executeNextTask(missionId) {
    setExecutingMissionId(missionId);

    setErrors((current) => ({
      ...current,
      [missionId]: "",
    }));

    try {
      const response = await fetch(
        `${API_BASE}/missions/${missionId}/execute-next`,
        {
          method: "POST",
        },
      );

      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          data.detail || "Executor Agent failed.",
        );
      }

      setTasksByMission((current) => ({
        ...current,
        [missionId]: data.tasks || [],
      }));

      setOpenPlanId(null);
      setOpenTasksId(missionId);

      await loadMissions();
    } catch (error) {
      setErrors((current) => ({
        ...current,
        [missionId]: error.message,
      }));

      await loadTasks(missionId).catch(() => {});
      await loadMissions();
    } finally {
      setExecutingMissionId(null);
    }
  }


  async function startAutonomousWorker(missionId) {
    setWorkerActionMissionId(missionId);

    setErrors((current) => ({
      ...current,
      [missionId]: "",
    }));

    try {
      const response = await fetch(
        `${API_BASE}/missions/${missionId}/worker/start?delay_seconds=2`,
        {
          method: "POST",
        },
      );

      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          data.detail || "Autonomous Worker failed to start.",
        );
      }

      setWorker(data.worker || EMPTY_WORKER);
      setOpenPlanId(null);
      setOpenTasksId(missionId);

      await Promise.all([
        loadMissions(),
        loadTasks(missionId),
      ]);
    } catch (error) {
      setErrors((current) => ({
        ...current,
        [missionId]: error.message,
      }));
    } finally {
      setWorkerActionMissionId(null);
    }
  }


  async function pauseAutonomousWorker(missionId) {
    setWorkerActionMissionId(missionId);

    setErrors((current) => ({
      ...current,
      [missionId]: "",
    }));

    try {
      const response = await fetch(
        `${API_BASE}/missions/${missionId}/worker/pause`,
        {
          method: "POST",
        },
      );

      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          data.detail || "Autonomous Worker failed to pause.",
        );
      }

      setWorker(data.worker || EMPTY_WORKER);
      setOpenPlanId(null);
      setOpenTasksId(missionId);
    } catch (error) {
      setErrors((current) => ({
        ...current,
        [missionId]: error.message,
      }));
    } finally {
      setWorkerActionMissionId(null);
    }
  }


  return (
    <div>
      {missions.length === 0 && (
        <div
          style={{
            padding: "16px",
            opacity: 0.7,
          }}
        >
          No missions in the queue.
        </div>
      )}

      {missions.map((mission) => {
        const isPlanning = runningMissionId === mission.id;
        const isExecuting = executingMissionId === mission.id;
        const isWorkerAction =
          workerActionMissionId === mission.id;

        const plan = plans[mission.id];
        const tasks = tasksByMission[mission.id] || [];

        const isPlanOpen = openPlanId === mission.id;
        const areTasksOpen = openTasksId === mission.id;

        const completedTasks = tasks.filter(
          (task) => task.status === "Completed",
        ).length;

        const allTasksComplete =
          tasks.length > 0 &&
          completedTasks === tasks.length;

        const isWorkerMission =
          worker.mission_id === mission.id;

        const workerRunning =
          isWorkerMission && worker.thread_alive;

        const anotherWorkerRunning =
          worker.thread_alive && !isWorkerMission;

        const workerProgress =
          worker.total_tasks > 0
            ? Math.round(
                (worker.completed_tasks / worker.total_tasks) * 100,
              )
            : 0;

        return (
          <div key={mission.id} className="mission-item">
            <div className="mission-header">
              <strong>{mission.name}</strong>
              <span>{mission.progress}%</span>
            </div>

            <div className="mission-status">
              {mission.status}
            </div>

            <div
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: "8px",
                marginTop: "10px",
              }}
            >
              <button
                type="button"
                disabled={
                  isPlanning ||
                  isExecuting ||
                  worker.thread_alive
                }
                onClick={() => runMission(mission.id)}
                style={{
                  padding: "7px 13px",
                  background: isPlanning
                    ? "#56606f"
                    : "#2d8cff",
                  color: "white",
                  border: "none",
                  borderRadius: "4px",
                  cursor: isPlanning ? "wait" : "pointer",
                  opacity:
                    isPlanning || worker.thread_alive ? 0.65 : 1,
                }}
              >
                {isPlanning ? "Planner working..." : "▶ Run"}
              </button>

              <button
                type="button"
                onClick={() => togglePlan(mission.id)}
                style={{
                  padding: "7px 13px",
                  background: isPlanOpen
                    ? "#17a673"
                    : "#253246",
                  color: "white",
                  border: "1px solid #3c506b",
                  borderRadius: "4px",
                  cursor: "pointer",
                }}
              >
                {isPlanOpen ? "Hide Plan" : "View Plan"}
              </button>

              <button
                type="button"
                onClick={() => toggleTasks(mission.id)}
                style={{
                  padding: "7px 13px",
                  background: areTasksOpen
                    ? "#8b5cf6"
                    : "#253246",
                  color: "white",
                  border: "1px solid #5f4a98",
                  borderRadius: "4px",
                  cursor: "pointer",
                }}
              >
                {areTasksOpen ? "Hide Tasks" : "View Tasks"}
              </button>
            </div>

            {errors[mission.id] && (
              <div
                style={{
                  marginTop: "10px",
                  padding: "9px",
                  color: "#ff8c8c",
                  background: "rgba(255, 70, 70, 0.1)",
                  border:
                    "1px solid rgba(255, 70, 70, 0.35)",
                  borderRadius: "5px",
                }}
              >
                {errors[mission.id]}
              </div>
            )}

            <div className="progress-bar">
              <div
                className="progress-fill"
                style={{
                  width: `${mission.progress}%`,
                }}
              />
            </div>

            {isPlanOpen && plan && (
              <div
                style={{
                  marginTop: "14px",
                  padding: "14px",
                  background: "rgba(8, 20, 34, 0.92)",
                  border: "1px solid #17a673",
                  borderRadius: "7px",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    gap: "12px",
                    marginBottom: "10px",
                  }}
                >
                  <strong style={{ color: "#4de3a5" }}>
                    Planner Agent v1
                  </strong>

                  <span
                    style={{
                      color: "#9fb4c9",
                      fontSize: "12px",
                    }}
                  >
                    {plan.model} · {plan.status}
                  </span>
                </div>

                <div
                  style={{
                    color: "#dce8f4",
                    whiteSpace: "pre-wrap",
                    lineHeight: 1.55,
                    fontSize: "13px",
                  }}
                >
                  {plan.plan}
                </div>
              </div>
            )}

            {areTasksOpen && (
              <div
                style={{
                  marginTop: "14px",
                  padding: "14px",
                  background: "rgba(14, 12, 31, 0.94)",
                  border: "1px solid #8b5cf6",
                  borderRadius: "7px",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    flexWrap: "wrap",
                    gap: "10px",
                    marginBottom: "12px",
                  }}
                >
                  <div>
                    <strong style={{ color: "#b79cff" }}>
                      Executor Agent v1
                    </strong>

                    <div
                      style={{
                        color: "#aab8c8",
                        fontSize: "12px",
                        marginTop: "3px",
                      }}
                    >
                      {completedTasks}/{tasks.length} tasks completed
                    </div>
                  </div>

                  <div
                    style={{
                      display: "flex",
                      flexWrap: "wrap",
                      gap: "8px",
                    }}
                  >
                    {tasks.length > 0 && (
                      <button
                        type="button"
                        disabled={
                          isExecuting ||
                          allTasksComplete ||
                          worker.thread_alive
                        }
                        onClick={() =>
                          executeNextTask(mission.id)
                        }
                        style={{
                          padding: "8px 14px",
                          background: allTasksComplete
                            ? "#176c50"
                            : "#8b5cf6",
                          color: "white",
                          border: "none",
                          borderRadius: "4px",
                          cursor: isExecuting
                            ? "wait"
                            : "pointer",
                          opacity:
                            isExecuting ||
                            worker.thread_alive
                              ? 0.6
                              : 1,
                        }}
                      >
                        {isExecuting
                          ? "Executor working..."
                          : allTasksComplete
                            ? "All Tasks Complete"
                            : "Execute Next Task"}
                      </button>
                    )}

                    <button
                      type="button"
                      disabled={
                        isWorkerAction ||
                        worker.thread_alive ||
                        tasks.length === 0 ||
                        allTasksComplete
                      }
                      onClick={() =>
                        startAutonomousWorker(mission.id)
                      }
                      style={{
                        padding: "8px 14px",
                        background: allTasksComplete
                          ? "#176c50"
                          : "#2d8cff",
                        color: "white",
                        border: "none",
                        borderRadius: "4px",
                        cursor: isWorkerAction
                          ? "wait"
                          : "pointer",
                        opacity:
                          isWorkerAction ||
                          worker.thread_alive ||
                          tasks.length === 0 ||
                          allTasksComplete
                            ? 0.6
                            : 1,
                      }}
                    >
                      {isWorkerAction
                        ? "Starting..."
                        : workerRunning
                          ? "Auto Worker Running"
                          : "▶ Start Auto Worker"}
                    </button>

                    <button
                      type="button"
                      disabled={
                        isWorkerAction || !workerRunning
                      }
                      onClick={() =>
                        pauseAutonomousWorker(mission.id)
                      }
                      style={{
                        padding: "8px 14px",
                        background: "#d39220",
                        color: "white",
                        border: "none",
                        borderRadius: "4px",
                        cursor: isWorkerAction
                          ? "wait"
                          : "pointer",
                        opacity:
                          isWorkerAction || !workerRunning
                            ? 0.5
                            : 1,
                      }}
                    >
                      ⏸ Pause Worker
                    </button>
                  </div>
                </div>

                {anotherWorkerRunning && (
                  <div
                    style={{
                      marginBottom: "12px",
                      padding: "10px",
                      color: "#ffd166",
                      background: "rgba(255, 209, 102, 0.1)",
                      border:
                        "1px solid rgba(255, 209, 102, 0.4)",
                      borderRadius: "6px",
                    }}
                  >
                    Autonomous Worker is currently running mission{" "}
                    {worker.mission_id}.
                  </div>
                )}

                {isWorkerMission && (
                  <div
                    style={{
                      marginBottom: "12px",
                      padding: "12px",
                      background: "rgba(5, 19, 35, 0.92)",
                      border: `1px solid ${workerStatusColor(
                        worker.status,
                      )}`,
                      borderRadius: "7px",
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        flexWrap: "wrap",
                        gap: "10px",
                      }}
                    >
                      <strong style={{ color: "#55a7ff" }}>
                        Autonomous Worker v1
                      </strong>

                      <span
                        style={{
                          color: workerStatusColor(worker.status),
                          fontSize: "12px",
                        }}
                      >
                        {worker.status}
                        {worker.thread_alive ? " · Active" : ""}
                      </span>
                    </div>

                    <div
                      style={{
                        marginTop: "8px",
                        color: "#cbd7e4",
                        fontSize: "12px",
                      }}
                    >
                      {worker.completed_tasks}/{worker.total_tasks}{" "}
                      tasks completed
                    </div>

                    <div
                      style={{
                        height: "7px",
                        marginTop: "7px",
                        background: "#28364a",
                        borderRadius: "999px",
                        overflow: "hidden",
                      }}
                    >
                      <div
                        style={{
                          width: `${workerProgress}%`,
                          height: "100%",
                          background:
                            "linear-gradient(90deg, #2d8cff, #4de3a5)",
                          transition: "width 0.4s ease",
                        }}
                      />
                    </div>

                    <div
                      style={{
                        marginTop: "9px",
                        color: "#dce8f4",
                        fontSize: "12px",
                      }}
                    >
                      {worker.last_message}
                    </div>

                    {worker.stop_requested && (
                      <div
                        style={{
                          marginTop: "7px",
                          color: "#ffd166",
                          fontSize: "12px",
                        }}
                      >
                        Pause requested. The active task will finish
                        before the worker stops.
                      </div>
                    )}

                    {worker.last_error && (
                      <div
                        style={{
                          marginTop: "7px",
                          color: "#ff7b7b",
                          fontSize: "12px",
                        }}
                      >
                        {worker.last_error}
                      </div>
                    )}
                  </div>
                )}

                {tasks.length === 0 && (
                  <div
                    style={{
                      color: "#aab8c8",
                      padding: "10px 0",
                    }}
                  >
                    No tasks are available. Run this mission to create
                    a plan and task list.
                  </div>
                )}

                {tasks.map((task) => (
                  <div
                    key={task.id}
                    style={{
                      marginTop: "9px",
                      padding: "11px",
                      background: "rgba(26, 27, 54, 0.9)",
                      border: `1px solid ${taskStatusColor(
                        task.status,
                      )}`,
                      borderRadius: "6px",
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        gap: "10px",
                      }}
                    >
                      <strong style={{ color: "#f0ecff" }}>
                        {task.position}. {task.title}
                      </strong>

                      <span
                        style={{
                          color: taskStatusColor(task.status),
                          fontSize: "12px",
                        }}
                      >
                        {task.status}
                      </span>
                    </div>

                    <div
                      style={{
                        color: "#b9c3d0",
                        fontSize: "12px",
                        marginTop: "6px",
                        lineHeight: 1.45,
                      }}
                    >
                      {task.instructions}
                    </div>

                    {task.result && (
                      <div
                        style={{
                          marginTop: "9px",
                          padding: "9px",
                          color: "#dce8f4",
                          background: "rgba(4, 10, 20, 0.7)",
                          borderRadius: "4px",
                          whiteSpace: "pre-wrap",
                          lineHeight: 1.5,
                          fontSize: "12px",
                        }}
                      >
                        {task.result}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
