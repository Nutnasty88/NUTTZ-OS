function ContainerList({ docker }) {
  const containers = docker?.containers || [];

  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">DOCKER ENGINE</p>
          <h2>Containers</h2>
        </div>

        <span className="count-badge">
          {docker ? `${docker.running}/${docker.total} running` : "Loading"}
        </span>
      </div>

      <div className="container-list">
        {!docker && <p className="muted">Reading Docker state...</p>}

        {docker && containers.length === 0 && (
          <p className="muted">No containers found.</p>
        )}

        {containers.map((container) => (
          <article className="container-row" key={container.id}>
            <div className={`container-icon ${container.status}`}>
              {container.status === "running" ? "●" : "○"}
            </div>

            <div className="container-info">
              <strong>{container.name}</strong>
              <span>{container.image}</span>
            </div>

            <span className={`container-status ${container.status}`}>
              {container.status}
            </span>
          </article>
        ))}
      </div>
    </section>
  );
}

export default ContainerList;
