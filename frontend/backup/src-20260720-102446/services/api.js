const API = "http://127.0.0.1:8000";

export async function sendChat(messages, model = "qwen3:8b") {
  const response = await fetch(`${API}/api/ollama/chat`, {
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

  if (!response.ok) {
    throw new Error(`Server returned ${response.status}`);
  }

  return response.json();
}

export async function getModels() {
  const response = await fetch(`${API}/api/ollama/models`);
  return response.json();
}

export async function getStatus() {
  const response = await fetch(`${API}/api/ollama/status`);
  return response.json();
}
