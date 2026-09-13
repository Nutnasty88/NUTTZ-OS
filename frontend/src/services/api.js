const API = "http://127.0.0.1:8000";

async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, options);

  let data;

  try {
    data = await response.json();
  } catch {
    data = {
      detail: `HTTP ${response.status}`,
    };
  }

  if (!response.ok) {
    const message =
      data?.detail ||
      data?.message ||
      `Request failed with HTTP ${response.status}`;

    throw new Error(
      typeof message === "string"
        ? message
        : JSON.stringify(message),
    );
  }

  return data;
}

export async function getSystem() {
  return request("/api/system");
}

export async function getDocker() {
  return request("/api/docker");
}

export async function getOllamaStatus() {
  return request("/api/ollama/status");
}

export async function getModels() {
  return request("/api/ollama/models");
}

export async function sendChat(messages, model = "qwen3:8b") {
  return request("/api/ollama/chat", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model,
      messages,
      stream: false,
    }),
  });
}

export async function createMission(
  title,
  assignedAgent = "Planner",
  priority = "Normal",
) {
  return request("/api/missions/", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      title,
      assigned_agent: assignedAgent,
      priority,
    }),
  });
}

export async function getMission(missionId) {
  return request(`/api/missions/${missionId}`);
}

export async function runMission(missionId) {
  return request(`/api/missions/${missionId}/run`, {
    method: "POST",
  });
}

export async function getMissionApprovalStatus(missionId) {
  return request(
    `/api/missions/${missionId}/approval-status`,
  );
}

export async function approveMissionPlan(missionId) {
  return request(
    `/api/missions/${missionId}/approve`,
    {
      method: "POST",
    },
  );
}

export async function getMissionTasks(missionId) {
  return request(`/api/missions/${missionId}/tasks`);
}

export async function getMissionDeliverable(missionId) {
  return request(`/api/missions/${missionId}/deliverable`);
}

export async function getMissionWorkerStatus(missionId) {
  return request(
    `/api/missions/${missionId}/worker/status`,
  );
}

export async function startMissionWorker(missionId) {
  return request(
    `/api/missions/${missionId}/worker/start`,
    {
      method: "POST",
    },
  );
}

export async function pauseMissionWorker(missionId) {
  return request(
    `/api/missions/${missionId}/worker/pause`,
    {
      method: "POST",
    },
  );
}
