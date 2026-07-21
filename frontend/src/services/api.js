const API = "http://127.0.0.1:8000";

export async function getSystem() {
  const response = await fetch(`${API}/api/system`);
  return response.json();
}

export async function getDocker() {
  const response = await fetch(`${API}/api/docker`);
  return response.json();
}

export async function getOllamaStatus() {
  const response = await fetch(`${API}/api/ollama/status`);
  return response.json();
}

export async function getModels() {
  const response = await fetch(`${API}/api/ollama/models`);
  return response.json();
}

export async function sendChat(message, model) {
  const response = await fetch(`${API}/api/ollama/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      message,
      model,
    }),
  });

  return response.json();
}