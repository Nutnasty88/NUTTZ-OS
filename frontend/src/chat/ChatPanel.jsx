import { useState } from "react";
import { sendChat } from "../services/api";

export default function ChatPanel() {
  const [messages, setMessages] = useState([
    {
      role: "assistant",
      content: "Welcome to NUTTZ OS. How can I help you today?"
    }
  ]);

  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSend() {
    if (!input.trim() || loading) return;

    const userMessage = {
      role: "user",
      content: input
    };

    setMessages(prev => [...prev, userMessage]);
    setLoading(true);

    try {
      const reply = await sendChat(input, "qwen3:8b");

      setMessages(prev => [
        ...prev,
        {
          role: "assistant",
          content: reply.response || reply.message || JSON.stringify(reply)
        }
      ]);
    } catch (err) {
      setMessages(prev => [
        ...prev,
        {
          role: "assistant",
          content: "Error contacting the AI backend."
        }
      ]);
    }

    setInput("");
    setLoading(false);
  }

  return (
    <div className="card">
      <h2>💬 AI Chat</h2>

      <div
        style={{
          height: 350,
          overflowY: "auto",
          marginTop: 15,
          marginBottom: 15,
          padding: 10,
          background: "#0f172a",
          borderRadius: 8
        }}
      >
        {messages.map((msg, index) => (
          <div
            key={index}
            style={{
              marginBottom: 12,
              textAlign: msg.role === "user" ? "right" : "left"
            }}
          >
            <strong>
              {msg.role === "user" ? "You" : "AI"}
            </strong>

            <div>{msg.content}</div>
          </div>
        ))}
      </div>

      <input
        style={{
          width: "100%",
          padding: 10,
          marginBottom: 10
        }}
        placeholder="Ask your AI..."
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            handleSend();
          }
        }}
      />

      <button
        style={{
          width: "100%",
          padding: 10
        }}
        onClick={handleSend}
        disabled={loading}
      >
        {loading ? "Thinking..." : "Send"}
      </button>
    </div>
  );
}