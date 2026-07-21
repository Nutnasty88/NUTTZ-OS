function QuickActions({ onRefresh }) {
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">CONTROL SURFACE</p>
          <h2>Quick Actions</h2>
        </div>
      </div>

      <div className="action-grid">
        <button type="button" onClick={onRefresh}>
          <span>↻</span>
          Refresh telemetry
        </button>

        <a href="https://localhost:9443" target="_blank" rel="noreferrer">
          <span>🐳</span>
          Open Portainer
        </a>

        <button type="button" disabled>
          <span>＋</span>
          Install module
          <small>Coming soon</small>
        </button>

        <button type="button" disabled>
          <span>💾</span>
          Run backup
          <small>Coming soon</small>
        </button>
      </div>
    </section>
  );
}

export default QuickActions;
