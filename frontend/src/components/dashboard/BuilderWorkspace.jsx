import { useCallback, useEffect, useState } from "react";


const API_BASE = "http://127.0.0.1:8000/api";
const PROJECT_MANIFEST_PATH = "nuttz-project.json";


function formatBytes(value) {
  const bytes = Number(value || 0);

  if (bytes < 1024) {
    return `${bytes} B`;
  }

  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }

  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}


function shortHash(value) {
  if (!value) {
    return "";
  }

  if (value.length <= 20) {
    return value;
  }

  return `${value.slice(0, 12)}…${value.slice(-8)}`;
}


export default function BuilderWorkspace({
  missionId,
}) {
  const [workspace, setWorkspace] = useState(null);
  const [files, setFiles] = useState([]);
  const [selectedPath, setSelectedPath] = useState("");
  const [selectedFile, setSelectedFile] = useState(null);
  const [projectManifest, setProjectManifest] = useState(null);
  const [launchResult, setLaunchResult] = useState(null);

  const [loading, setLoading] = useState(true);
  const [launching, setLaunching] = useState(false);
  const [fileLoading, setFileLoading] = useState(false);

  const [error, setError] = useState("");
  const [fileError, setFileError] = useState("");
  const [launchError, setLaunchError] = useState("");


  const loadWorkspace = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const [workspaceResponse, filesResponse] =
        await Promise.all([
          fetch(
            `${API_BASE}/missions/${missionId}/workspace`,
          ),
          fetch(
            `${API_BASE}/missions/${missionId}/workspace/files`,
          ),
        ]);

      if (!workspaceResponse.ok) {
        const data = await workspaceResponse
          .json()
          .catch(() => ({}));

        throw new Error(
          data.detail ||
            `Workspace request failed with HTTP ${workspaceResponse.status}.`,
        );
      }

      if (!filesResponse.ok) {
        const data = await filesResponse
          .json()
          .catch(() => ({}));

        throw new Error(
          data.detail ||
            `Workspace file request failed with HTTP ${filesResponse.status}.`,
        );
      }

      const workspaceData =
        await workspaceResponse.json();

      const filesData =
        await filesResponse.json();

      setWorkspace(workspaceData.workspace || null);

      const nextFiles = Array.isArray(filesData.files)
        ? filesData.files
        : [];

      setFiles(nextFiles);

      const manifestExists = nextFiles.some(
        (file) =>
          file.path === PROJECT_MANIFEST_PATH,
      );

      if (manifestExists) {
        try {
          const query = new URLSearchParams({
            path: PROJECT_MANIFEST_PATH,
          });

          const manifestResponse = await fetch(
            `${API_BASE}/missions/${missionId}/workspace/file?${query.toString()}`,
          );

          const manifestData = await manifestResponse
            .json()
            .catch(() => ({}));

          if (manifestResponse.ok) {
            const content =
              manifestData.file?.content || "";

            const parsed = JSON.parse(content);

            setProjectManifest(parsed);
          } else {
            setProjectManifest(null);
          }
        } catch {
          setProjectManifest(null);
        }
      } else {
        setProjectManifest(null);
      }

      setSelectedPath((current) => {
        if (
          current &&
          nextFiles.some(
            (file) => file.path === current,
          )
        ) {
          return current;
        }

        return nextFiles[0]?.path || "";
      });
    } catch (requestError) {
      setWorkspace(null);
      setFiles([]);
      setSelectedPath("");
      setSelectedFile(null);
      setProjectManifest(null);
      setLaunchResult(null);
      setLaunchError("");

      setError(
        requestError.message ||
          "Builder workspace could not be loaded.",
      );
    } finally {
      setLoading(false);
    }
  }, [missionId]);


  const loadFile = useCallback(
    async (path) => {
      if (!path) {
        setSelectedFile(null);
        return;
      }

      setFileLoading(true);
      setFileError("");

      try {
        const query = new URLSearchParams({
          path,
        });

        const response = await fetch(
          `${API_BASE}/missions/${missionId}/workspace/file?${query.toString()}`,
        );

        const data = await response
          .json()
          .catch(() => ({}));

        if (!response.ok) {
          throw new Error(
            data.detail ||
              `Artifact request failed with HTTP ${response.status}.`,
          );
        }

        setSelectedFile(data.file || null);
      } catch (requestError) {
        setSelectedFile(null);

        setFileError(
          requestError.message ||
            "Artifact could not be loaded.",
        );
      } finally {
        setFileLoading(false);
      }
    },
    [missionId],
  );


  async function launchProject() {
    setLaunching(true);
    setLaunchError("");
    setLaunchResult(null);

    try {
      const response = await fetch(
        `${API_BASE}/missions/${missionId}/workspace/launch`,
        {
          method: "POST",
        },
      );

      const data = await response
        .json()
        .catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          data.detail ||
            `Project launch failed with HTTP ${response.status}.`,
        );
      }

      setLaunchResult(data);
    } catch (requestError) {
      setLaunchError(
        requestError.message ||
          "Project launch failed.",
      );
    } finally {
      setLaunching(false);
    }
  }


  useEffect(() => {
    loadWorkspace();
  }, [loadWorkspace]);


  useEffect(() => {
    if (selectedPath) {
      loadFile(selectedPath);
    } else {
      setSelectedFile(null);
    }
  }, [selectedPath, loadFile]);


  if (loading) {
    return (
      <div
        style={{
          marginTop: "12px",
          padding: "14px",
          color: "#aab8c8",
          background: "rgba(10, 21, 36, 0.80)",
          border: "1px solid #314863",
          borderRadius: "7px",
        }}
      >
        Loading Builder workspace...
      </div>
    );
  }


  if (error) {
    return (
      <div
        style={{
          marginTop: "12px",
          padding: "14px",
          color: "#ffd166",
          background: "rgba(255, 209, 102, 0.06)",
          border:
            "1px solid rgba(255, 209, 102, 0.30)",
          borderRadius: "7px",
        }}
      >
        <strong>Builder Workspace</strong>

        <div
          style={{
            marginTop: "7px",
            lineHeight: 1.5,
            fontSize: "12px",
          }}
        >
          {error}
        </div>

        <button
          type="button"
          onClick={loadWorkspace}
          style={{
            marginTop: "10px",
            padding: "6px 10px",
            color: "#dce8f4",
            background: "#253246",
            border: "1px solid #435773",
            borderRadius: "5px",
            cursor: "pointer",
          }}
        >
          Retry
        </button>
      </div>
    );
  }


  return (
    <div
      style={{
        marginTop: "12px",
        padding: "14px",
        background: "rgba(7, 16, 29, 0.92)",
        border:
          "1px solid rgba(85, 167, 255, 0.30)",
        borderRadius: "7px",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: "10px",
          flexWrap: "wrap",
          marginBottom: "12px",
        }}
      >
        <div>
          <div
            style={{
              color: "#8fa2b7",
              fontSize: "10px",
              letterSpacing: "0.8px",
              textTransform: "uppercase",
            }}
          >
            Builder Workspace
          </div>

          <strong
            style={{
              display: "block",
              marginTop: "3px",
              color: "#e8f1fb",
            }}
          >
            {workspace?.name || `mission-${missionId}`}
          </strong>
        </div>

        <button
          type="button"
          onClick={loadWorkspace}
          style={{
            padding: "6px 10px",
            color: "#dce8f4",
            background: "#253246",
            border: "1px solid #435773",
            borderRadius: "5px",
            cursor: "pointer",
          }}
        >
          Refresh
        </button>
      </div>


      <div
        style={{
          display: "flex",
          gap: "8px",
          flexWrap: "wrap",
          marginBottom: "14px",
        }}
      >
        <div
          style={{
            padding: "7px 9px",
            background: "#101d2e",
            border: "1px solid #2e425b",
            borderRadius: "5px",
            fontSize: "11px",
          }}
        >
          <span style={{ color: "#8fa2b7" }}>
            Files:
          </span>{" "}
          <strong>
            {workspace?.file_count ?? files.length}
          </strong>
        </div>

        <div
          style={{
            padding: "7px 9px",
            background: "#101d2e",
            border: "1px solid #2e425b",
            borderRadius: "5px",
            fontSize: "11px",
          }}
        >
          <span style={{ color: "#8fa2b7" }}>
            Size:
          </span>{" "}
          <strong>
            {formatBytes(workspace?.total_bytes)}
          </strong>
        </div>

        <div
          style={{
            padding: "7px 9px",
            background: "#101d2e",
            border: "1px solid #2e425b",
            borderRadius: "5px",
            fontSize: "11px",
          }}
        >
          <span style={{ color: "#8fa2b7" }}>
            Mode:
          </span>{" "}
          <strong style={{ color: "#4de3a5" }}>
            Read Only
          </strong>
        </div>
      </div>


      {projectManifest && (
        <div
          style={{
            marginBottom: "14px",
            padding: "12px",
            background:
              "rgba(77, 227, 165, 0.06)",
            border:
              "1px solid rgba(77, 227, 165, 0.28)",
            borderRadius: "6px",
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              gap: "10px",
              flexWrap: "wrap",
              marginBottom: "11px",
            }}
          >
            <div>
              <div
                style={{
                  color: "#8fa2b7",
                  fontSize: "10px",
                  letterSpacing: "0.7px",
                  textTransform: "uppercase",
                }}
              >
                Project Summary
              </div>

              <strong
                style={{
                  display: "block",
                  marginTop: "3px",
                  color: "#e8f1fb",
                }}
              >
                {projectManifest.name ||
                  `mission-${missionId}`}
              </strong>
            </div>

            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "8px",
                flexWrap: "wrap",
              }}
            >
              {projectManifest.verification
                ?.verified && (
                <button
                  type="button"
                  onClick={launchProject}
                  disabled={launching}
                  style={{
                    padding: "6px 10px",
                    color: "#08150f",
                    background: launching
                      ? "#7aa897"
                      : "#4de3a5",
                    border:
                      "1px solid rgba(77, 227, 165, 0.55)",
                    borderRadius: "5px",
                    cursor: launching
                      ? "wait"
                      : "pointer",
                    fontSize: "11px",
                    fontWeight: 800,
                  }}
                >
                  {launching
                    ? "Launching..."
                    : "Launch Project"}
                </button>
              )}

              <span
              style={{
                padding: "4px 8px",
                color:
                  projectManifest.verification
                    ?.verified
                    ? "#4de3a5"
                    : "#ffd166",
                background:
                  projectManifest.verification
                    ?.verified
                    ? "rgba(77, 227, 165, 0.10)"
                    : "rgba(255, 209, 102, 0.10)",
                border:
                  projectManifest.verification
                    ?.verified
                    ? "1px solid rgba(77, 227, 165, 0.30)"
                    : "1px solid rgba(255, 209, 102, 0.30)",
                borderRadius: "999px",
                fontSize: "10px",
                fontWeight: 700,
                textTransform: "uppercase",
              }}
            >
              {projectManifest.verification
                ?.verified
                ? "Verified Project"
                : "Unverified Project"}
              </span>
            </div>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns:
                "repeat(auto-fit, minmax(150px, 1fr))",
              gap: "8px",
            }}
          >
            <div
              style={{
                padding: "8px 9px",
                background: "#0a1524",
                border: "1px solid #263b54",
                borderRadius: "5px",
              }}
            >
              <div
                style={{
                  color: "#71869e",
                  fontSize: "9px",
                  textTransform: "uppercase",
                  letterSpacing: "0.6px",
                }}
              >
                Runtime
              </div>

              <strong
                style={{
                  display: "block",
                  marginTop: "4px",
                  color: "#dce8f4",
                  fontSize: "12px",
                }}
              >
                {projectManifest.runtime || "Unknown"}
              </strong>
            </div>

            <div
              style={{
                padding: "8px 9px",
                background: "#0a1524",
                border: "1px solid #263b54",
                borderRadius: "5px",
              }}
            >
              <div
                style={{
                  color: "#71869e",
                  fontSize: "9px",
                  textTransform: "uppercase",
                  letterSpacing: "0.6px",
                }}
              >
                Entrypoint
              </div>

              <strong
                style={{
                  display: "block",
                  marginTop: "4px",
                  color: "#dce8f4",
                  fontSize: "12px",
                  fontFamily: "monospace",
                  overflowWrap: "anywhere",
                }}
              >
                {projectManifest.entrypoint ||
                  "Unknown"}
              </strong>
            </div>

            <div
              style={{
                padding: "8px 9px",
                background: "#0a1524",
                border: "1px solid #263b54",
                borderRadius: "5px",
              }}
            >
              <div
                style={{
                  color: "#71869e",
                  fontSize: "9px",
                  textTransform: "uppercase",
                  letterSpacing: "0.6px",
                }}
              >
                Schema
              </div>

              <strong
                style={{
                  display: "block",
                  marginTop: "4px",
                  color: "#dce8f4",
                  fontSize: "12px",
                }}
              >
                v{projectManifest.schema_version ?? "?"}
              </strong>
            </div>

            <div
              style={{
                padding: "8px 9px",
                background: "#0a1524",
                border: "1px solid #263b54",
                borderRadius: "5px",
              }}
            >
              <div
                style={{
                  color: "#71869e",
                  fontSize: "9px",
                  textTransform: "uppercase",
                  letterSpacing: "0.6px",
                }}
              >
                Verified By
              </div>

              <strong
                style={{
                  display: "block",
                  marginTop: "4px",
                  color: "#4de3a5",
                  fontSize: "12px",
                }}
              >
                {projectManifest.verification
                  ?.source || "Unknown"}
              </strong>
            </div>
          </div>

          <div
            style={{
              marginTop: "9px",
              padding: "9px",
              background: "#050c16",
              border: "1px solid #263b54",
              borderRadius: "5px",
            }}
          >
            <div
              style={{
                color: "#71869e",
                fontSize: "9px",
                textTransform: "uppercase",
                letterSpacing: "0.6px",
              }}
            >
              Run Command
            </div>

            <code
              style={{
                display: "block",
                marginTop: "5px",
                color: "#dce8f4",
                fontSize: "11px",
                whiteSpace: "pre-wrap",
                overflowWrap: "anywhere",
              }}
            >
              {Array.isArray(
                projectManifest.run_command,
              )
                ? projectManifest.run_command.join(
                    " ",
                  )
                : "Unknown"}
            </code>
          </div>

          {Array.isArray(projectManifest.files) && (
            <div
              style={{
                marginTop: "9px",
                padding: "9px",
                background: "#050c16",
                border: "1px solid #263b54",
                borderRadius: "5px",
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: "8px",
                  flexWrap: "wrap",
                  marginBottom: "7px",
                }}
              >
                <div
                  style={{
                    color: "#71869e",
                    fontSize: "9px",
                    textTransform: "uppercase",
                    letterSpacing: "0.6px",
                  }}
                >
                  Verified Files
                </div>

                <span
                  style={{
                    padding: "3px 7px",
                    color: "#4de3a5",
                    background:
                      "rgba(77, 227, 165, 0.10)",
                    border:
                      "1px solid rgba(77, 227, 165, 0.28)",
                    borderRadius: "999px",
                    fontSize: "9px",
                    fontWeight: 700,
                  }}
                >
                  {projectManifest.files.length} verified
                </span>
              </div>

              {projectManifest.files.length === 0 ? (
                <div
                  style={{
                    color: "#8fa2b7",
                    fontSize: "10px",
                  }}
                >
                  No verified project files.
                </div>
              ) : (
                <div
                  style={{
                    display: "grid",
                    gap: "5px",
                  }}
                >
                  {projectManifest.files.map(
                    (verifiedFile) => (
                      <div
                        key={verifiedFile.path}
                        style={{
                          display: "grid",
                          gridTemplateColumns:
                            "minmax(120px, 1fr) auto minmax(150px, auto)",
                          alignItems: "center",
                          gap: "10px",
                          padding: "6px 7px",
                          background: "#0a1524",
                          border:
                            "1px solid #1e3148",
                          borderRadius: "4px",
                          minWidth: 0,
                        }}
                      >
                        <code
                          title={verifiedFile.path}
                          style={{
                            color: "#dce8f4",
                            fontSize: "10px",
                            overflow: "hidden",
                            textOverflow:
                              "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                        >
                          {verifiedFile.path}
                        </code>

                        <span
                          style={{
                            color: "#8fa2b7",
                            fontSize: "9px",
                            whiteSpace: "nowrap",
                          }}
                        >
                          {formatBytes(
                            verifiedFile.size_bytes,
                          )}
                        </span>

                        <code
                          title={verifiedFile.sha256}
                          style={{
                            color: "#71869e",
                            fontSize: "9px",
                            textAlign: "right",
                            whiteSpace: "nowrap",
                          }}
                        >
                          {shortHash(
                            verifiedFile.sha256,
                          )}
                        </code>
                      </div>
                    ),
                  )}
                </div>
              )}
            </div>
          )}

          {projectManifest.artifact?.sha256 && (
            <div
              title={
                projectManifest.artifact.sha256
              }
              style={{
                marginTop: "8px",
                color: "#71869e",
                fontSize: "10px",
                fontFamily: "monospace",
                overflowWrap: "anywhere",
              }}
            >
              Artifact SHA256:{" "}
              {shortHash(
                projectManifest.artifact.sha256,
              )}
            </div>
          )}
          {(launchResult || launchError) && (
            <div
              style={{
                marginTop: "10px",
                padding: "10px",
                background: "#050c16",
                border: launchResult?.success
                  ? "1px solid rgba(77, 227, 165, 0.30)"
                  : "1px solid rgba(255, 123, 123, 0.30)",
                borderRadius: "5px",
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  gap: "8px",
                  flexWrap: "wrap",
                  marginBottom: "8px",
                }}
              >
                <strong
                  style={{
                    color: launchResult?.success
                      ? "#4de3a5"
                      : "#ff7b7b",
                    fontSize: "11px",
                  }}
                >
                  {launchResult?.success
                    ? "Launch Verified"
                    : "Launch Failed"}
                </strong>

                {launchResult?.verified_manifest && (
                  <span
                    style={{
                      color: "#8fa2b7",
                      fontSize: "10px",
                    }}
                  >
                    Manifest verified
                  </span>
                )}
              </div>

              {launchError && (
                <div
                  style={{
                    color: "#ff7b7b",
                    fontSize: "11px",
                    lineHeight: 1.5,
                  }}
                >
                  {launchError}
                </div>
              )}

              {launchResult?.execution && (
                <>
                  <div
                    style={{
                      display: "flex",
                      gap: "8px",
                      flexWrap: "wrap",
                      marginBottom: "8px",
                      color: "#aab8c8",
                      fontSize: "10px",
                    }}
                  >
                    <span>
                      Exit:{" "}
                      <strong
                        style={{
                          color:
                            launchResult.execution
                              .exit_code === 0
                              ? "#4de3a5"
                              : "#ff7b7b",
                        }}
                      >
                        {launchResult.execution
                          .exit_code ?? "n/a"}
                      </strong>
                    </span>

                    <span>
                      Duration:{" "}
                      <strong>
                        {launchResult.execution
                          .duration_ms ?? 0}
                        ms
                      </strong>
                    </span>

                    <span>
                      Status:{" "}
                      <strong>
                        {launchResult.execution
                          .status || "unknown"}
                      </strong>
                    </span>
                  </div>

                  {launchResult.execution.stdout && (
                    <div
                      style={{
                        marginTop: "6px",
                      }}
                    >
                      <div
                        style={{
                          color: "#71869e",
                          fontSize: "9px",
                          textTransform:
                            "uppercase",
                          letterSpacing: "0.6px",
                        }}
                      >
                        Stdout
                      </div>

                      <pre
                        style={{
                          margin: "5px 0 0",
                          padding: "8px",
                          color: "#dce8f4",
                          background: "#02070d",
                          border:
                            "1px solid #263b54",
                          borderRadius: "4px",
                          whiteSpace: "pre-wrap",
                          overflowWrap: "anywhere",
                          fontSize: "10px",
                          lineHeight: 1.45,
                        }}
                      >
                        {launchResult.execution.stdout}
                      </pre>
                    </div>
                  )}

                  {launchResult.execution.stderr && (
                    <div
                      style={{
                        marginTop: "6px",
                      }}
                    >
                      <div
                        style={{
                          color: "#ff9f6e",
                          fontSize: "9px",
                          textTransform:
                            "uppercase",
                          letterSpacing: "0.6px",
                        }}
                      >
                        Stderr
                      </div>

                      <pre
                        style={{
                          margin: "5px 0 0",
                          padding: "8px",
                          color: "#ffb49b",
                          background: "#120907",
                          border:
                            "1px solid rgba(255, 123, 123, 0.25)",
                          borderRadius: "4px",
                          whiteSpace: "pre-wrap",
                          overflowWrap: "anywhere",
                          fontSize: "10px",
                          lineHeight: 1.45,
                        }}
                      >
                        {launchResult.execution.stderr}
                      </pre>
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </div>
      )}


      {files.length === 0 ? (
        <div
          style={{
            padding: "12px",
            color: "#8fa2b7",
            background: "#0a1524",
            border: "1px solid #263b54",
            borderRadius: "5px",
          }}
        >
          This workspace contains no artifacts.
        </div>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns:
              "minmax(170px, 0.32fr) minmax(0, 1fr)",
            gap: "12px",
          }}
        >
          <div
            style={{
              minWidth: 0,
              background: "#0a1524",
              border: "1px solid #263b54",
              borderRadius: "6px",
              overflow: "hidden",
            }}
          >
            <div
              style={{
                padding: "8px 10px",
                color: "#8fa2b7",
                fontSize: "10px",
                letterSpacing: "0.7px",
                textTransform: "uppercase",
                borderBottom:
                  "1px solid #263b54",
              }}
            >
              Artifacts
            </div>

            {files.map((file) => {
              const selected =
                selectedPath === file.path;

              return (
                <button
                  key={file.path}
                  type="button"
                  onClick={() =>
                    setSelectedPath(file.path)
                  }
                  style={{
                    display: "block",
                    width: "100%",
                    padding: "9px 10px",
                    color: selected
                      ? "#eaf4ff"
                      : "#b9c3d0",
                    textAlign: "left",
                    background: selected
                      ? "rgba(85, 167, 255, 0.15)"
                      : "transparent",
                    border: "none",
                    borderBottom:
                      "1px solid rgba(49, 72, 99, 0.45)",
                    cursor: "pointer",
                  }}
                >
                  <div
                    style={{
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      fontSize: "12px",
                      fontWeight: 700,
                    }}
                  >
                    {file.path}
                  </div>

                  <div
                    style={{
                      marginTop: "3px",
                      color: "#71869e",
                      fontSize: "10px",
                    }}
                  >
                    {formatBytes(file.size_bytes)}
                  </div>
                </button>
              );
            })}
          </div>


          <div
            style={{
              minWidth: 0,
              background: "#07101d",
              border: "1px solid #263b54",
              borderRadius: "6px",
              overflow: "hidden",
            }}
          >
            <div
              style={{
                padding: "9px 11px",
                borderBottom:
                  "1px solid #263b54",
              }}
            >
              <strong
                style={{
                  color: "#e8f1fb",
                  fontSize: "12px",
                }}
              >
                {selectedPath || "Artifact Preview"}
              </strong>

              {selectedFile?.sha256 && (
                <div
                  title={selectedFile.sha256}
                  style={{
                    marginTop: "3px",
                    color: "#71869e",
                    fontSize: "10px",
                    fontFamily: "monospace",
                  }}
                >
                  SHA256:{" "}
                  {shortHash(selectedFile.sha256)}
                </div>
              )}
            </div>

            {fileLoading ? (
              <div
                style={{
                  padding: "14px",
                  color: "#8fa2b7",
                }}
              >
                Loading artifact...
              </div>
            ) : fileError ? (
              <div
                style={{
                  padding: "14px",
                  color: "#ff7b7b",
                }}
              >
                {fileError}
              </div>
            ) : (
              <pre
                style={{
                  margin: 0,
                  padding: "12px",
                  maxHeight: "420px",
                  overflow: "auto",
                  color: "#dce8f4",
                  background: "#050c16",
                  fontSize: "11px",
                  lineHeight: 1.55,
                  whiteSpace: "pre-wrap",
                  overflowWrap: "anywhere",
                  fontFamily:
                    "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
                }}
              >
                {selectedFile?.content || ""}
              </pre>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
