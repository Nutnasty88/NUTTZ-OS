import { useCallback, useEffect, useState } from "react";


const API_BASE = "http://127.0.0.1:8000/api";


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

  const [loading, setLoading] = useState(true);
  const [fileLoading, setFileLoading] = useState(false);

  const [error, setError] = useState("");
  const [fileError, setFileError] = useState("");


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
