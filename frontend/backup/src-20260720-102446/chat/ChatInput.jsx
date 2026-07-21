import { useState } from "react";

export default function ChatInput({ onSend, disabled = false }) {
  const [prompt, setPrompt] = useState("");

  function handleSubmit(event) {
    event.preventDefault();

    const cleanPrompt = prompt.trim();

    if (!cleanPrompt || disabled) {
      return;
    }

    onSend(cleanPrompt);
    setPrompt("");
  }

  function handleKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      handleSubmit(event);
    }
  }

  return (
    <form className="chat-input-form" onSubmit={handleSubmit}>
      <textarea
        className="chat-input"
        value={prompt}
        onChange={(event) => setPrompt(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Ask NUTTZ AI anything..."
        rows="1"
        disabled={disabled}
      />

      <button
        className="chat-send-button"
        type="submit"
        disabled={disabled || !prompt.trim()}
      >
        {disabled ? "Thinking..." : "Send"}
      </button>
    </form>
  );
}