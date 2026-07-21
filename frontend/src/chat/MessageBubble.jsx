export default function MessageBubble({ message }) {
  const isUser = message.role === "user";

  return (
    <div
      className={
        isUser
          ? "message-row message-row-user"
          : "message-row message-row-assistant"
      }
    >
      <div
        className={
          isUser
            ? "message-bubble message-bubble-user"
            : "message-bubble message-bubble-assistant"
        }
      >
        <div className="message-role">
          {isUser ? "You" : "NUTTZ AI"}
        </div>

        <div className="message-content">
          {message.content}
        </div>
      </div>
    </div>
  );
}