import { useCallback, useEffect, useState } from "react";
import BuilderWorkspace from "./BuilderWorkspace";


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


function missionToneStyle(tone) {
  const tones = {
    pending: {
      color: "#aab8c8",
      background: "rgba(170, 184, 200, 0.10)",
      border: "1px solid rgba(170, 184, 200, 0.30)",
    },

    running: {
      color: "#55a7ff",
      background: "rgba(85, 167, 255, 0.10)",
      border: "1px solid rgba(85, 167, 255, 0.35)",
    },

    blocked: {
      color: "#ffd166",
      background: "rgba(255, 209, 102, 0.10)",
      border: "1px solid rgba(255, 209, 102, 0.35)",
    },

    "report-error": {
      color: "#ff9f6e",
      background: "rgba(255, 159, 110, 0.10)",
      border: "1px solid rgba(255, 159, 110, 0.35)",
    },

    completed: {
      color: "#4de3a5",
      background: "rgba(77, 227, 165, 0.10)",
      border: "1px solid rgba(77, 227, 165, 0.35)",
    },

    error: {
      color: "#ff7b7b",
      background: "rgba(255, 123, 123, 0.10)",
      border: "1px solid rgba(255, 123, 123, 0.35)",
    },

    unknown: {
      color: "#aab8c8",
      background: "rgba(170, 184, 200, 0.08)",
      border: "1px solid rgba(170, 184, 200, 0.22)",
    },
  };

  return tones[tone] || tones.unknown;
}


function missionPolicy(status) {
  const policies = {
    Pending: {
      canRun: true,
      canViewReport: false,
      canRetryReport: false,
      terminal: false,
      tone: "pending",
    },

    Running: {
      canRun: false,
      canViewReport: false,
      canRetryReport: false,
      terminal: false,
      tone: "running",
    },

    Blocked: {
      canRun: false,
      canViewReport: false,
      canRetryReport: false,
      terminal: false,
      tone: "blocked",
    },

    "Report Error": {
      canRun: false,
      canViewReport: false,
      canRetryReport: true,
      terminal: false,
      tone: "report-error",
    },

    Completed: {
      canRun: true,
      canViewReport: true,
      canRetryReport: false,
      terminal: true,
      tone: "completed",
    },

    Error: {
      canRun: true,
      canViewReport: false,
      canRetryReport: false,
      terminal: true,
      tone: "error",
    },
  };

  return (
    policies[status] || {
      canRun: false,
      canViewReport: false,
      canRetryReport: false,
      terminal: false,
      tone: "unknown",
    }
  );
}


function parseJsonEvidenceBlock(
  result,
  marker,
  nextMarkers = [],
) {
  if (
    typeof result !== "string" ||
    !result.includes(marker)
  ) {
    return null;
  }

  const start = result.indexOf(marker);

  if (start < 0) {
    return null;
  }

  const jsonStart = start + marker.length;

  let jsonEnd = result.length;

  for (const nextMarker of nextMarkers) {
    const candidate = result.indexOf(
      nextMarker,
      jsonStart,
    );

    if (
      candidate >= 0 &&
      candidate < jsonEnd
    ) {
      jsonEnd = candidate;
    }
  }

  const raw = result
    .slice(jsonStart, jsonEnd)
    .trim();

  if (!raw) {
    return null;
  }

  try {
    const parsed = JSON.parse(raw);

    if (
      parsed &&
      typeof parsed === "object"
    ) {
      return parsed;
    }
  } catch {
    return null;
  }

  return null;
}


function RepairConfidenceBadge({
  confidence,
}) {
  if (!confidence) {
    return null;
  }

  const score = Number(
    confidence.score,
  );

  const level = String(
    confidence.level || "",
  ).toUpperCase();

  const safeScore = Number.isFinite(score)
    ? score
    : "?";

  const color =
    level === "HIGH"
      ? "#4de3a5"
      : level === "MEDIUM"
        ? "#ffd166"
        : "#ff9c9c";

  return (
    <span
      title={[
        ...(Array.isArray(confidence.reasons)
          ? confidence.reasons
          : []),
        ...(Array.isArray(confidence.deductions)
          ? confidence.deductions
          : []),
      ].join("\n")}
      style={{
        padding: "2px 7px",
        borderRadius: "999px",
        fontSize: "11px",
        fontWeight: 700,
        color,
        background: `${color}18`,
        border: `1px solid ${color}33`,
      }}
    >
      REPAIR CONFIDENCE:{" "}
      {level || "UNKNOWN"}{" "}
      {safeScore}
    </span>
  );
}


function RepairConfidenceDetails({
  confidence,
}) {
  if (!confidence) {
    return null;
  }

  const reasons = Array.isArray(
    confidence.reasons,
  )
    ? confidence.reasons
    : [];

  const deductions = Array.isArray(
    confidence.deductions,
  )
    ? confidence.deductions
    : [];

  const tracebackTargets = Array.isArray(
    confidence.traceback_targets,
  )
    ? confidence.traceback_targets
    : [];

  const changedFiles = Array.isArray(
    confidence.changed_files,
  )
    ? confidence.changed_files
    : [];

  return (
    <details
      style={{
        marginTop: "8px",
        padding: "8px",
        borderRadius: "5px",
        border:
          "1px solid rgba(120, 150, 185, 0.22)",
        background:
          "rgba(7, 15, 27, 0.45)",
      }}
    >
      <summary
        style={{
          cursor: "pointer",
          fontWeight: 700,
          opacity: 0.9,
        }}
      >
        Repair Confidence Details
      </summary>

      <div
        style={{
          marginTop: "9px",
          display: "grid",
          gap: "7px",
          fontSize: "12px",
          lineHeight: 1.45,
        }}
      >
        <div>
          <strong>Score:</strong>{" "}
          {confidence.level || "Unknown"}{" "}
          {confidence.score ?? "?"}
        </div>

        <div>
          <strong>Primary target:</strong>{" "}
          <code>
            {confidence.primary_target ||
              "None"}
          </code>
        </div>

        <div>
          <strong>
            Primary target repaired:
          </strong>{" "}
          {confidence.primary_target_repaired
            ? "Yes"
            : "No"}
        </div>

        <div>
          <strong>Changed files:</strong>{" "}
          {changedFiles.length
            ? changedFiles.join(", ")
            : "None"}
        </div>

        <div>
          <strong>Traceback targets:</strong>{" "}
          {tracebackTargets.length
            ? tracebackTargets.join(" → ")
            : "None"}
        </div>

        {reasons.length > 0 && (
          <div>
            <strong>Reasons:</strong>
            <ul
              style={{
                margin:
                  "5px 0 0 18px",
                padding: 0,
              }}
            >
              {reasons.map(
                (reason, index) => (
                  <li
                    key={`reason-${index}`}
                  >
                    {reason}
                  </li>
                ),
              )}
            </ul>
          </div>
        )}

        {deductions.length > 0 && (
          <div>
            <strong>Deductions:</strong>
            <ul
              style={{
                margin:
                  "5px 0 0 18px",
                padding: 0,
              }}
            >
              {deductions.map(
                (deduction, index) => (
                  <li
                    key={`deduction-${index}`}
                  >
                    {deduction}
                  </li>
                ),
              )}
            </ul>
          </div>
        )}
      </div>
    </details>
  );
}


function parseWorkspaceExecutionResult(result) {
  if (
    typeof result !== "string" ||
    !result.includes("WORKSPACE EXECUTION:")
  ) {
    return null;
  }

  const artifactMatch = result.match(
    /^Artifact:\s*(.+)$/m,
  );

  const exitCodeMatch = result.match(
    /^Exit code:\s*(-?\d+)$/m,
  );

  const stdoutMatch = result.match(
    /^Stdout:\s*(.*)$/m,
  );

  const repairMatch = result.match(
    /AUTO REPAIR:\s*SUCCESS\s*\n([^\n]*)/,
  );

  const repairConfidence = parseJsonEvidenceBlock(
    result,
    "REPAIR CONFIDENCE:\n",
    [
      "\n\nVERIFIED EXECUTION EVIDENCE:",
    ],
  );

  return {
    verified: result.includes(
      "WORKSPACE EXECUTION: VERIFIED",
    ),
    artifact: artifactMatch
      ? artifactMatch[1].trim()
      : "",
    exitCode: exitCodeMatch
      ? Number(exitCodeMatch[1])
      : null,
    stdout: stdoutMatch
      ? stdoutMatch[1].trim()
      : "",
    repaired: Boolean(repairMatch),
    repairSummary: repairMatch
      ? repairMatch[1].trim()
      : "",
    repairConfidence,
    raw: result,
  };
}


function parseBuilderAutoExecutionResult(result) {
  if (
    typeof result !== "string" ||
    !result.includes("AUTO PROJECT EXECUTION:")
  ) {
    return null;
  }

  const verified = result.includes(
    "AUTO PROJECT EXECUTION: VERIFIED",
  );

  const deferred = result.includes(
    "AUTO PROJECT EXECUTION: DEFERRED",
  );

  const failed =
    !verified &&
    !deferred &&
    result.includes("AUTO PROJECT EXECUTION:");

  const entrypointMatch = result.match(
    /^Entrypoint:\s*(.*)$/m,
  );

  const exitCodeMatch = result.match(
    /^Exit code:\s*(-?\d+)$/m,
  );

  const stdoutMatch = result.match(
    /^Stdout:\s*(.*)$/m,
  );

  const reasonMatch = result.match(
    /^Reason:\s*(.*)$/m,
  );

  const repairMatch = result.match(
    /AUTO REPAIR:\s*SUCCESS\s*\n([^\n]*)/,
  );

  const repairConfidence = parseJsonEvidenceBlock(
    result,
    "AUTO REPAIR CONFIDENCE:\n",
    [
      "\n\nVERIFIED AUTO EXECUTION EVIDENCE:",
      "\nVERIFIED AUTO EXECUTION EVIDENCE:",
    ],
  );

  return {
    verified,
    deferred,
    failed,
    repaired: Boolean(repairMatch),
    repairSummary: repairMatch
      ? repairMatch[1].trim()
      : "",
    repairConfidence,
    entrypoint: entrypointMatch
      ? entrypointMatch[1].trim()
      : "",
    exitCode: exitCodeMatch
      ? Number(exitCodeMatch[1])
      : null,
    stdout: stdoutMatch
      ? stdoutMatch[1].trim()
      : "",
    reason: reasonMatch
      ? reasonMatch[1].trim()
      : "",
    raw: result,
  };
}


function BuilderAutoExecutionEvidence({ result }) {
  const execution = parseBuilderAutoExecutionResult(
    result,
  );

  if (!execution) {
    return null;
  }

  const statusColor = execution.verified
    ? "#4de3a5"
    : execution.deferred
      ? "#ffd166"
      : "#ff7b7b";

  const background = execution.verified
    ? "rgba(77, 227, 165, 0.08)"
    : execution.deferred
      ? "rgba(255, 209, 102, 0.08)"
      : "rgba(255, 123, 123, 0.08)";

  const border = execution.verified
    ? "1px solid rgba(77, 227, 165, 0.30)"
    : execution.deferred
      ? "1px solid rgba(255, 209, 102, 0.30)"
      : "1px solid rgba(255, 123, 123, 0.30)";

  const statusLabel = execution.verified
    ? "AUTO VERIFIED ✓"
    : execution.deferred
      ? "AUTO RUN DEFERRED"
      : "AUTO RUN FAILED";

  return (
    <div
      style={{
        marginTop: "10px",
        padding: "12px",
        borderRadius: "6px",
        background,
        border,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "8px",
          flexWrap: "wrap",
          marginBottom: "9px",
        }}
      >
        <strong>Builder Agent</strong>

        <span
          style={{
            padding: "2px 7px",
            borderRadius: "999px",
            fontSize: "11px",
            fontWeight: 700,
            color: statusColor,
            background: `${statusColor}18`,
          }}
        >
          {statusLabel}
        </span>

        {execution.repaired && (
          <span
            style={{
              padding: "2px 7px",
              borderRadius: "999px",
              fontSize: "11px",
              fontWeight: 700,
              color: "#55a7ff",
              background:
                "rgba(85, 167, 255, 0.12)",
            }}
          >
            AUTO REPAIR ✓
          </span>
        )}

        <RepairConfidenceBadge
          confidence={
            execution.repairConfidence
          }
        />
      </div>

      {execution.entrypoint && (
        <div style={{ marginBottom: "5px" }}>
          <strong>Entrypoint:</strong>{" "}
          <code>{execution.entrypoint}</code>
        </div>
      )}

      {execution.exitCode !== null && (
        <div style={{ marginBottom: "5px" }}>
          <strong>Exit code:</strong>{" "}
          {execution.exitCode}
        </div>
      )}

      {execution.stdout && (
        <div style={{ marginBottom: "5px" }}>
          <strong>Output:</strong>{" "}
          <code>{execution.stdout}</code>
        </div>
      )}

      {execution.repaired && (
        <div
          style={{
            marginTop: "8px",
            padding: "8px",
            borderRadius: "4px",
            color: "#9cc7ff",
            background:
              "rgba(85, 167, 255, 0.07)",
          }}
        >
          <strong>Builder Repair:</strong>{" "}
          {execution.repairSummary ||
            "Entrypoint repaired and retested successfully."}
        </div>
      )}

      <RepairConfidenceDetails
        confidence={
          execution.repairConfidence
        }
      />

      {execution.reason && (
        <div
          style={{
            marginTop: "8px",
            padding: "8px",
            borderRadius: "4px",
            color: "#ffd166",
            background:
              "rgba(255, 209, 102, 0.07)",
          }}
        >
          <strong>Deferred:</strong>{" "}
          {execution.reason}
        </div>
      )}

      <details style={{ marginTop: "10px" }}>
        <summary
          style={{
            cursor: "pointer",
            opacity: 0.8,
          }}
        >
          Builder Auto-Run Evidence
        </summary>

        <pre
          style={{
            marginTop: "8px",
            padding: "10px",
            overflowX: "auto",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            fontSize: "11px",
            lineHeight: 1.45,
            background: "rgba(0, 0, 0, 0.20)",
            borderRadius: "4px",
          }}
        >
          {execution.raw}
        </pre>
      </details>
    </div>
  );
}


function WorkspaceExecutionEvidence({ result }) {
  const execution = parseWorkspaceExecutionResult(result);

  if (!execution) {
    return null;
  }

  return (
    <div
      style={{
        marginTop: "10px",
        padding: "12px",
        borderRadius: "6px",
        background: execution.verified
          ? "rgba(77, 227, 165, 0.08)"
          : "rgba(255, 123, 123, 0.08)",
        border: execution.verified
          ? "1px solid rgba(77, 227, 165, 0.30)"
          : "1px solid rgba(255, 123, 123, 0.30)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "8px",
          flexWrap: "wrap",
          marginBottom: "9px",
        }}
      >
        <strong>Workspace Executor</strong>

        <span
          style={{
            padding: "2px 7px",
            borderRadius: "999px",
            fontSize: "11px",
            fontWeight: 700,
            color: execution.verified
              ? "#4de3a5"
              : "#ff7b7b",
            background: execution.verified
              ? "rgba(77, 227, 165, 0.12)"
              : "rgba(255, 123, 123, 0.12)",
          }}
        >
          {execution.verified
            ? "VERIFIED ✓"
            : "FAILED"}
        </span>

        {execution.repaired && (
          <span
            style={{
              padding: "2px 7px",
              borderRadius: "999px",
              fontSize: "11px",
              fontWeight: 700,
              color: "#55a7ff",
              background:
                "rgba(85, 167, 255, 0.12)",
            }}
          >
            AUTO REPAIR ✓
          </span>
        )}


        <RepairConfidenceBadge
          confidence={
            execution.repairConfidence
          }
        />
      </div>

      {execution.artifact && (
        <div style={{ marginBottom: "5px" }}>
          <strong>Artifact:</strong>{" "}
          <code>{execution.artifact}</code>
        </div>
      )}

      {execution.exitCode !== null && (
        <div style={{ marginBottom: "5px" }}>
          <strong>Exit code:</strong>{" "}
          {execution.exitCode}
        </div>
      )}

      {execution.stdout && (
        <div style={{ marginBottom: "5px" }}>
          <strong>Output:</strong>{" "}
          <code>{execution.stdout}</code>
        </div>
      )}

      {execution.repaired && (
        <div
          style={{
            marginTop: "8px",
            padding: "8px",
            borderRadius: "4px",
            background:
              "rgba(85, 167, 255, 0.07)",
          }}
        >
          <strong>Builder Repair:</strong>{" "}
          {execution.repairSummary ||
            "Artifact repaired and retested successfully."}
        </div>
      )}

      <RepairConfidenceDetails
        confidence={
          execution.repairConfidence
        }
      />

      <details style={{ marginTop: "10px" }}>
        <summary
          style={{
            cursor: "pointer",
            opacity: 0.8,
          }}
        >
          Execution Evidence
        </summary>

        <pre
          style={{
            marginTop: "8px",
            padding: "10px",
            overflowX: "auto",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            fontSize: "11px",
            lineHeight: 1.45,
            background: "rgba(0, 0, 0, 0.20)",
            borderRadius: "4px",
          }}
        >
          {execution.raw}
        </pre>
      </details>
    </div>
  );
}


export default function MissionQueue() {
  const [missions, setMissions] = useState([]);
  const [missionSearch, setMissionSearch] = useState("");
  const [runningMissionId, setRunningMissionId] = useState(null);
  const [executingMissionId, setExecutingMissionId] =
    useState(null);
  const [workerActionMissionId, setWorkerActionMissionId] =
    useState(null);
  const [reporterActionMissionId, setReporterActionMissionId] =
    useState(null);

  const [openDetailsId, setOpenDetailsId] = useState(null);
  const [openPlanId, setOpenPlanId] = useState(null);
  const [openResearchId, setOpenResearchId] = useState(null);
  const [openTasksId, setOpenTasksId] = useState(null);
  const [openDeliverableId, setOpenDeliverableId] = useState(null);
  const [openWorkspaceId, setOpenWorkspaceId] = useState(null);

  const [missionDetails, setMissionDetails] = useState({});
  const [plans, setPlans] = useState({});
  const [researchByMission, setResearchByMission] = useState({});
  const [tasksByMission, setTasksByMission] = useState({});
  const [deliverablesByMission, setDeliverablesByMission] =
    useState({});
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


  const loadMissionDetails = useCallback(async (missionId) => {
    const response = await fetch(
      `${API_BASE}/missions/${missionId}`,
    );

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(
        data.detail || "Failed to load mission details.",
      );
    }

    setMissionDetails((current) => ({
      ...current,
      [missionId]: data,
    }));

    return data;
  }, []);


  const loadDeliverable = useCallback(async (missionId) => {
    const response = await fetch(
      `${API_BASE}/missions/${missionId}/deliverable`,
    );

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(
        data.detail || "No final deliverable was found.",
      );
    }

    setDeliverablesByMission((current) => ({
      ...current,
      [missionId]: data,
    }));

    return data;
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


  async function toggleDetails(missionId) {
    if (openDetailsId === missionId) {
      setOpenDetailsId(null);
      return;
    }

    setErrors((current) => ({
      ...current,
      [missionId]: "",
    }));

    setOpenPlanId(null);
    setOpenResearchId(null);
    setOpenTasksId(null);
    setOpenDeliverableId(null);
    setOpenWorkspaceId(null);

    try {
      await Promise.all([
        loadMissionDetails(missionId),
        loadTasks(missionId),
      ]);

      setOpenDetailsId(missionId);
    } catch (error) {
      setErrors((current) => ({
        ...current,
        [missionId]: error.message,
      }));
    }
  }


  async function retryReporter(missionId) {
    setReporterActionMissionId(missionId);

    setErrors((current) => ({
      ...current,
      [missionId]: "",
    }));

    try {
      const response = await fetch(
        `${API_BASE}/missions/${missionId}/deliverable`,
        {
          method: "POST",
        },
      );

      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          data.detail || "Reporter failed to create the deliverable.",
        );
      }

      const deliverable = data.deliverable;

      if (!deliverable) {
        throw new Error(
          "Reporter completed without returning a deliverable.",
        );
      }

      setDeliverablesByMission((current) => ({
        ...current,
        [missionId]: deliverable,
      }));

      setOpenDetailsId(null);
      setOpenPlanId(null);
      setOpenResearchId(null);
      setOpenTasksId(null);
      setOpenDeliverableId(missionId);

      await loadMissions();
    } catch (error) {
      setErrors((current) => ({
        ...current,
        [missionId]: error.message,
      }));

      await loadMissions();
    } finally {
      setReporterActionMissionId(null);
    }
  }


  async function toggleDeliverable(missionId) {
    if (openDeliverableId === missionId) {
      setOpenDeliverableId(null);
      setOpenWorkspaceId(null);
    setOpenWorkspaceId(null);
      return;
    }

    setErrors((current) => ({
      ...current,
      [missionId]: "",
    }));

    setOpenDetailsId(null);
    setOpenPlanId(null);
    setOpenResearchId(null);
    setOpenTasksId(null);

    if (deliverablesByMission[missionId]) {
      setOpenDeliverableId(missionId);
      return;
    }

    try {
      await loadDeliverable(missionId);
      setOpenDeliverableId(missionId);
    } catch (error) {
      setErrors((current) => ({
        ...current,
        [missionId]: error.message,
      }));
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

    setOpenDetailsId(null);
    setOpenResearchId(null);
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


  async function toggleResearch(missionId) {
    if (openResearchId === missionId) {
      setOpenResearchId(null);
      return;
    }

    setErrors((current) => ({
      ...current,
      [missionId]: "",
    }));

    setOpenDetailsId(null);
    setOpenPlanId(null);
    setOpenTasksId(null);

    if (researchByMission[missionId]) {
      setOpenResearchId(missionId);
      return;
    }

    try {
      const response = await fetch(
        `${API_BASE}/missions/${missionId}/research`,
      );

      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          data.detail || "No saved research was found.",
        );
      }

      setResearchByMission((current) => ({
        ...current,
        [missionId]: data,
      }));

      setOpenResearchId(missionId);
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

    setOpenDetailsId(null);
    setOpenPlanId(null);
    setOpenResearchId(null);

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


  async function retryBlockedTask(missionId) {
    setWorkerActionMissionId(missionId);

    setErrors((current) => ({
      ...current,
      [missionId]: "",
    }));

    try {
      const resetResponse = await fetch(
        `${API_BASE}/missions/${missionId}/tasks/reset-blocked`,
        {
          method: "POST",
        },
      );

      const resetData = await resetResponse
        .json()
        .catch(() => ({}));

      if (!resetResponse.ok) {
        throw new Error(
          resetData.detail || "Blocked task reset failed.",
        );
      }

      const workerResponse = await fetch(
        `${API_BASE}/missions/${missionId}/worker/start?delay_seconds=2`,
        {
          method: "POST",
        },
      );

      const workerData = await workerResponse
        .json()
        .catch(() => ({}));

      if (!workerResponse.ok) {
        throw new Error(
          workerData.detail ||
            "Task was reset, but the worker failed to resume.",
        );
      }

      setWorker(workerData.worker || EMPTY_WORKER);
      setTasksByMission((current) => ({
        ...current,
        [missionId]: resetData.tasks || [],
      }));

      setOpenDetailsId(null);
      setOpenPlanId(null);
      setOpenResearchId(null);
      setOpenDeliverableId(null);
      setOpenWorkspaceId(null);
    setOpenWorkspaceId(null);
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

      await loadTasks(missionId).catch(() => {});
      await loadMissions();
    } finally {
      setWorkerActionMissionId(null);
    }
  }


  function toggleWorkspace(missionId) {
    if (openWorkspaceId === missionId) {
      setOpenWorkspaceId(null);
      return;
    }

    setOpenDetailsId(null);
    setOpenPlanId(null);
    setOpenResearchId(null);
    setOpenTasksId(null);
    setOpenDeliverableId(null);
    setOpenWorkspaceId(missionId);
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


  const normalizedMissionSearch =
    missionSearch.trim().toLowerCase();

  const filteredMissions = normalizedMissionSearch
    ? missions.filter((mission) => {
        const searchable = [
          mission.id,
          mission.name,
          mission.status,
          mission.agent,
          mission.priority,
        ]
          .filter((value) => value !== null && value !== undefined)
          .join(" ")
          .toLowerCase();

        return searchable.includes(
          normalizedMissionSearch,
        );
      })
    : missions;


  return (
    <div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "10px",
          marginBottom: "14px",
          padding: "10px 12px",
          background: "rgba(15, 27, 44, 0.88)",
          border: "1px solid rgba(85, 167, 255, 0.22)",
          borderRadius: "7px",
        }}
      >
        <div
          style={{
            minWidth: "120px",
          }}
        >
          <div
            style={{
              color: "#8fa2b7",
              fontSize: "10px",
              letterSpacing: "0.8px",
              textTransform: "uppercase",
            }}
          >
            Mission Filter
          </div>

          <strong
            style={{
              display: "block",
              marginTop: "2px",
              color: "#e8f1fb",
              fontSize: "12px",
            }}
          >
            {filteredMissions.length} of {missions.length}
          </strong>
        </div>

        <input
          type="search"
          value={missionSearch}
          onChange={(event) =>
            setMissionSearch(event.target.value)
          }
          placeholder="Search ID, title, status, agent..."
          aria-label="Search missions"
          style={{
            flex: "1 1 260px",
            minWidth: "180px",
            padding: "9px 11px",
            color: "#eaf4ff",
            background: "#0a1524",
            border: "1px solid #38516f",
            borderRadius: "5px",
            outline: "none",
          }}
        />

        {missionSearch && (
          <button
            type="button"
            onClick={() => setMissionSearch("")}
            style={{
              padding: "8px 12px",
              color: "#dce8f4",
              background: "#253246",
              border: "1px solid #435773",
              borderRadius: "5px",
              cursor: "pointer",
            }}
          >
            Clear
          </button>
        )}
      </div>

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

      {missions.length > 0 &&
        filteredMissions.length === 0 && (
          <div
            style={{
              padding: "16px",
              marginBottom: "12px",
              color: "#aab8c8",
              background: "rgba(20, 31, 48, 0.72)",
              border:
                "1px solid rgba(170, 184, 200, 0.20)",
              borderRadius: "6px",
            }}
          >
            No missions match "{missionSearch}".
          </div>
        )}

      {filteredMissions.map((mission) => {
        const policy = missionPolicy(mission.status);
        const missionTone = missionToneStyle(policy.tone);

        const isPlanning = runningMissionId === mission.id;
        const isExecuting = executingMissionId === mission.id;
        const isWorkerAction =
          workerActionMissionId === mission.id;
        const isReporterAction =
          reporterActionMissionId === mission.id;

        const details = missionDetails[mission.id];
        const plan = plans[mission.id];
        const research = researchByMission[mission.id];
        const researchReport = research?.report || {};
        const researchTechnologies = Array.isArray(
          researchReport.technologies,
        )
          ? researchReport.technologies
          : [];
        const researchSteps = Array.isArray(
          researchReport.steps,
        )
          ? researchReport.steps
          : [];
        const researchRisks = Array.isArray(
          researchReport.risks,
        )
          ? researchReport.risks
          : [];
        const tasks = tasksByMission[mission.id] || [];
        const deliverable = deliverablesByMission[mission.id];

        const areDetailsOpen = openDetailsId === mission.id;
        const isPlanOpen = openPlanId === mission.id;
        const isResearchOpen =
          openResearchId === mission.id;
        const areTasksOpen = openTasksId === mission.id;
        const isDeliverableOpen =
          openDeliverableId === mission.id;
        const isWorkspaceOpen =
          openWorkspaceId === mission.id;

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

            <div
              className="mission-status"
              style={{
                display: "inline-flex",
                alignItems: "center",
                marginTop: "6px",
                padding: "4px 9px",
                borderRadius: "999px",
                color: missionTone.color,
                background: missionTone.background,
                border: missionTone.border,
                fontSize: "11px",
                fontWeight: "700",
                letterSpacing: "0.4px",
              }}
            >
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
                onClick={() => toggleDetails(mission.id)}
                style={{
                  padding: "7px 13px",
                  background: areDetailsOpen
                    ? "#355f9b"
                    : "#253246",
                  color: "white",
                  border: "1px solid #476f9f",
                  borderRadius: "4px",
                  cursor: "pointer",
                }}
              >
                {areDetailsOpen
                  ? "Hide Details"
                  : "Mission Details"}
              </button>

              <button
                type="button"
                disabled={
                  !policy.canRun ||
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
                  cursor:
                    isPlanning
                      ? "wait"
                      : policy.canRun
                        ? "pointer"
                        : "not-allowed",
                  opacity:
                    !policy.canRun ||
                    isPlanning ||
                    worker.thread_alive
                      ? 0.65
                      : 1,
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
                onClick={() => toggleResearch(mission.id)}
                style={{
                  padding: "7px 13px",
                  background: isResearchOpen
                    ? "#0f9f8f"
                    : "#253246",
                  color: "white",
                  border: "1px solid #287f77",
                  borderRadius: "4px",
                  cursor: "pointer",
                }}
              >
                {isResearchOpen
                  ? "Hide Research"
                  : "View Research"}
              </button>

              <button
                type="button"
                onClick={() =>
                  toggleWorkspace(mission.id)
                }
                style={{
                  padding: "7px 13px",
                  background: isWorkspaceOpen
                    ? "#1b6f5b"
                    : "#253246",
                  color: "white",
                  border: "1px solid #3c506b",
                  borderRadius: "4px",
                  cursor: "pointer",
                }}
              >
                {isWorkspaceOpen
                  ? "Hide Workspace"
                  : "View Workspace"}
              </button>

              {policy.canViewReport && (
                <button
                  type="button"
                  onClick={() => toggleDeliverable(mission.id)}
                  style={{
                    padding: "7px 13px",
                    background: isDeliverableOpen
                      ? "#c58b24"
                      : "#253246",
                    color: "white",
                    border: "1px solid #9b762f",
                    borderRadius: "4px",
                    cursor: "pointer",
                  }}
                >
                  {isDeliverableOpen
                    ? "Hide Report"
                    : "View Report"}
                </button>
              )}

              {mission.status === "Blocked" && (
                <button
                  type="button"
                  disabled={
                    isWorkerAction ||
                    worker.thread_alive
                  }
                  onClick={() =>
                    retryBlockedTask(mission.id)
                  }
                  style={{
                    padding: "7px 13px",
                    background: isWorkerAction
                      ? "#6b5326"
                      : "#d39220",
                    color: "white",
                    border: "1px solid #e3ad43",
                    borderRadius: "4px",
                    cursor: isWorkerAction
                      ? "wait"
                      : "pointer",
                    opacity:
                      isWorkerAction ||
                      worker.thread_alive
                        ? 0.6
                        : 1,
                  }}
                >
                  {isWorkerAction
                    ? "Retrying task..."
                    : "↻ Retry Blocked Task"}
                </button>
              )}

              {policy.canRetryReport && (
                <button
                  type="button"
                  disabled={
                    isReporterAction ||
                    worker.thread_alive
                  }
                  onClick={() => retryReporter(mission.id)}
                  style={{
                    padding: "7px 13px",
                    background: isReporterAction
                      ? "#6b5326"
                      : "#c58b24",
                    color: "white",
                    border: "1px solid #d9a441",
                    borderRadius: "4px",
                    cursor: isReporterAction
                      ? "wait"
                      : "pointer",
                    opacity:
                      isReporterAction ||
                      worker.thread_alive
                        ? 0.6
                        : 1,
                  }}
                >
                  {isReporterAction
                    ? "Reporter working..."
                    : "↻ Retry Report"}
                </button>
              )}

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

            {areDetailsOpen && details && (
              <div
                style={{
                  marginTop: "14px",
                  padding: "15px",
                  background:
                    "linear-gradient(180deg, rgba(11, 25, 45, 0.97), rgba(7, 16, 30, 0.97))",
                  border: "1px solid #476f9f",
                  borderRadius: "8px",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "flex-start",
                    flexWrap: "wrap",
                    gap: "12px",
                  }}
                >
                  <div>
                    <div
                      style={{
                        color: "#7eb6ff",
                        fontSize: "11px",
                        letterSpacing: "1.2px",
                        textTransform: "uppercase",
                      }}
                    >
                      Mission #{details.id}
                    </div>

                    <strong
                      style={{
                        display: "block",
                        marginTop: "4px",
                        color: "#eef6ff",
                        fontSize: "16px",
                      }}
                    >
                      {details.title}
                    </strong>
                  </div>

                  <span
                    style={{
                      padding: "5px 9px",
                      borderRadius: "999px",
                      background:
                        details.status === "Completed"
                          ? "rgba(77, 227, 165, 0.12)"
                          : details.status === "Error" ||
                          details.status === "Report Error"
                            ? "rgba(255, 123, 123, 0.12)"
                            : "rgba(85, 167, 255, 0.12)",
                      color:
                        details.status === "Completed"
                          ? "#4de3a5"
                          : details.status === "Error" ||
                          details.status === "Report Error"
                            ? "#ff7b7b"
                            : "#55a7ff",
                      fontSize: "12px",
                      fontWeight: "700",
                    }}
                  >
                    {details.status}
                  </span>
                </div>

                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns:
                      "repeat(auto-fit, minmax(110px, 1fr))",
                    gap: "8px",
                    marginTop: "14px",
                  }}
                >
                  {[
                    ["Progress", `${details.progress ?? 0}%`],
                    ["Agent", details.agent || "Unassigned"],
                    ["Priority", details.priority || "Normal"],
                    ["Tasks", tasks.length],
                    ["Completed", completedTasks],
                  ].map(([label, value]) => (
                    <div
                      key={label}
                      style={{
                        padding: "9px",
                        background: "rgba(18, 37, 61, 0.8)",
                        border: "1px solid rgba(104, 145, 190, 0.22)",
                        borderRadius: "6px",
                      }}
                    >
                      <div
                        style={{
                          color: "#8399b0",
                          fontSize: "10px",
                          textTransform: "uppercase",
                          letterSpacing: "0.7px",
                        }}
                      >
                        {label}
                      </div>

                      <div
                        style={{
                          marginTop: "4px",
                          color: "#e6f0fa",
                          fontSize: "13px",
                          fontWeight: "700",
                        }}
                      >
                        {value}
                      </div>
                    </div>
                  ))}
                </div>

                <div
                  style={{
                    marginTop: "15px",
                    paddingTop: "13px",
                    borderTop:
                      "1px solid rgba(104, 145, 190, 0.22)",
                  }}
                >
                  <strong
                    style={{
                      color: "#9dc7ff",
                      fontSize: "12px",
                      textTransform: "uppercase",
                      letterSpacing: "0.9px",
                    }}
                  >
                    Task Execution
                  </strong>

                  {tasks.length === 0 ? (
                    <div
                      style={{
                        marginTop: "10px",
                        color: "#8fa2b7",
                        fontSize: "12px",
                      }}
                    >
                      No task chain has been created for this mission.
                    </div>
                  ) : (
                    <div style={{ marginTop: "9px" }}>
                      {tasks.map((task) => (
                        <div
                          key={`summary-${task.id}`}
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: "9px",
                            padding: "7px 0",
                            borderBottom:
                              "1px solid rgba(104, 145, 190, 0.12)",
                          }}
                        >
                          <span
                            style={{
                              width: "19px",
                              color: taskStatusColor(task.status),
                              fontWeight: "700",
                              textAlign: "center",
                            }}
                          >
                            {task.status === "Completed"
                              ? "✓"
                              : task.status === "Running"
                                ? "▶"
                                : task.status === "Error"
                                  ? "!"
                                  : "○"}
                          </span>

                          <span
                            style={{
                              minWidth: "18px",
                              color: "#778da5",
                              fontSize: "11px",
                            }}
                          >
                            {task.position}
                          </span>

                          <span
                            style={{
                              flex: 1,
                              color: "#d8e5f2",
                              fontSize: "12px",
                            }}
                          >
                            {task.title}
                          </span>

                          <span
                            style={{
                              color: taskStatusColor(task.status),
                              fontSize: "10px",
                            }}
                          >
                            {task.status}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}

            {isWorkspaceOpen && (
              <BuilderWorkspace
                missionId={mission.id}
              />
            )}

            {isDeliverableOpen && deliverable && (
              <div
                style={{
                  marginTop: "14px",
                  padding: "16px",
                  background:
                    "linear-gradient(180deg, rgba(39, 30, 11, 0.96), rgba(22, 17, 8, 0.96))",
                  border: "1px solid #c58b24",
                  borderRadius: "8px",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    flexWrap: "wrap",
                    gap: "10px",
                    marginBottom: "13px",
                  }}
                >
                  <strong
                    style={{
                      color: "#ffd477",
                      fontSize: "14px",
                    }}
                  >
                    Reporter Agent · Final Deliverable
                  </strong>

                  <span
                    style={{
                      color: "#c7b486",
                      fontSize: "12px",
                    }}
                  >
                    {deliverable.model} · {deliverable.status}
                  </span>
                </div>

                <div
                  style={{
                    color: "#f1ead9",
                    whiteSpace: "pre-wrap",
                    lineHeight: 1.65,
                    fontSize: "13px",
                  }}
                >
                  {deliverable.content}
                </div>

                {deliverable.updated_at && (
                  <div
                    style={{
                      marginTop: "14px",
                      paddingTop: "10px",
                      borderTop:
                        "1px solid rgba(197, 139, 36, 0.25)",
                      color: "#9f9275",
                      fontSize: "10px",
                    }}
                  >
                    Updated {deliverable.updated_at}
                  </div>
                )}
              </div>
            )}

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

            {isResearchOpen && research && (
              <div
                style={{
                  marginTop: "14px",
                  padding: "14px",
                  background: "rgba(5, 28, 31, 0.94)",
                  border: "1px solid #0f9f8f",
                  borderRadius: "7px",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    flexWrap: "wrap",
                    gap: "10px",
                    marginBottom: "12px",
                  }}
                >
                  <strong style={{ color: "#53e0cf" }}>
                    Researcher Agent v1
                  </strong>

                  <span
                    style={{
                      color: "#9fb4c9",
                      fontSize: "12px",
                    }}
                  >
                    {research.model}
                  </span>
                </div>

                <div
                  style={{
                    color: "#dce8f4",
                    whiteSpace: "pre-wrap",
                    lineHeight: 1.55,
                    marginBottom: "14px",
                  }}
                >
                  {researchReport.summary ||
                    "No research summary was returned."}
                </div>

                {[
                  ["Technologies", researchTechnologies],
                  ["Recommended Steps", researchSteps],
                  ["Risks", researchRisks],
                ].map(([label, items]) => (
                  items.length > 0 && (
                    <div
                      key={label}
                      style={{ marginTop: "12px" }}
                    >
                      <strong style={{ color: "#53e0cf" }}>
                        {label}
                      </strong>

                      <ol
                        style={{
                          marginTop: "7px",
                          paddingLeft: "22px",
                          color: "#cfe0e8",
                        }}
                      >
                        {items.map((item, index) => (
                          <li
                            key={`${label}-${index}`}
                            style={{ marginBottom: "5px" }}
                          >
                            {String(item)}
                          </li>
                        ))}
                      </ol>
                    </div>
                  )
                ))}
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
                      <>
                        {parseBuilderAutoExecutionResult(
                          task.result,
                        ) ? (
                          <BuilderAutoExecutionEvidence
                            result={task.result}
                          />
                        ) : parseWorkspaceExecutionResult(
                            task.result,
                          ) ? (
                          <WorkspaceExecutionEvidence
                            result={task.result}
                          />
                        ) : (
                          <div
                            style={{
                              marginTop: "9px",
                              padding: "9px",
                              color: "#dce8f4",
                              background:
                                "rgba(4, 10, 20, 0.7)",
                              borderRadius: "4px",
                              whiteSpace: "pre-wrap",
                              lineHeight: 1.5,
                              fontSize: "12px",
                            }}
                          >
                            {task.result}
                          </div>
                        )}
                      </>
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
