function Header({ online, hostname, lastUpdated }) {
  return (
    <header className="topbar">
      <div>
        <p className="eyebrow">SELF-HOSTED CONTROL PLANE</p>
        <h1>NUTTZ OS</h1>
      </div>

      <div className="header-status">
        <div className={`status-pill ${online ? "online" : "offline"}`}>
          <span className="status-dot" />
          {online ? "Core online" : "Core offline"}
        </div>

        <div className="header-meta">
          <span>{hostname || "Detecting host..."}</span>
          <span>
            {lastUpdated
              ? `Updated ${lastUpdated.toLocaleTimeString()}`
              : "Waiting for telemetry..."}
          </span>
        </div>
      </div>
    </header>
  );
}

export default Header;
