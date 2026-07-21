export default function ChatHeader() {
  return (
    <div className="chat-header">
      <div>
        <h2>NUTTZ AI</h2>
        <p>Connected to your local Ollama backend</p>
      </div>

      <div className="chat-status">
        <span className="status-dot"></span>
        Online
      </div>
    </div>
  );
}