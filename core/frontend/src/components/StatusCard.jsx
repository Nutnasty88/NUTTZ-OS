function StatusCard({ label, value, detail, percent }) {
  const safePercent =
    typeof percent === "number"
      ? Math.max(0, Math.min(100, percent))
      : null;

  return (
    <article className="status-card">
      <div className="status-card-heading">
        <span>{label}</span>
        <strong>{value}</strong>
      </div>

      {safePercent !== null && (
        <div className="meter">
          <div
            className="meter-fill"
            style={{ width: `${safePercent}%` }}
          />
        </div>
      )}

      <p>{detail}</p>
    </article>
  );
}

export default StatusCard;
