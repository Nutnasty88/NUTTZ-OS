import { useState } from "react";
import "./chat.css";

import ChatHeader from "./ChatHeader";
import ChatInput from "./ChatInput";
import MessageBubble from "./MessageBubble";

import { sendChat } from "../services/api";

export default function ChatPanel() {
  const [messages, setMessages] = useState([
    {
      role: "assistant",
      content:
        "👋 Welcome to NUTTZ AI.\n\nI'm connected to your local Ollama backend.",
    },
  ]);

  const [loading, setLoading] = useState(false);

  async function handleSend(prompt) {
    const updatedMessages = [
      ...messages,
      {
        role: "user",
        content: prompt,
      },
    ];

    setMessages(updatedMessages);
    setLoading(true);

    try {
      const reply = await sendChat(updatedMessages);

      setMessages([
        ...updatedMessages,
        {
          role: "assistant",
          content:
            reply.message?.content ||
            reply.response ||
            JSON.stringify(reply, null, 2),
        },
      ]);
    } catch (err) {
      setMessages([
        ...updatedMessages,
        {
          role: "assistant",
          content: "❌ " + err.message,
        },
      ]);
    }

    setLoading(false);
  }

  return (
    <div className="chat-panel">
      <ChatHeader />

      <div className="chat-history">
        {messages.map((message, index) => (
          <MessageBubble
            key={index}
            message={message}
          />
        ))}

        {loading && (
          <MessageBubble
            message={{
              role: "assistant",
              content: "Thinking...",
            }}
          />
        )}
      </div>

      <ChatInput
        onSend={handleSend}
        disabled={loading}
      />
    </div>
  );
}