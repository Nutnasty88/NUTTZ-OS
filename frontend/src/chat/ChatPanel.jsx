import { useState } from "react";
import {
  approveMissionPlan,
  createMission,
  getMission,
  getMissionApprovalStatus,
  getMissionDeliverable,
  getMissionTasks,
  getMissionWorkerStatus,
  pauseMissionWorker,
  runMission,
  sendChat,
  startMissionWorker,
} from "../services/api";

const MODEL = "qwen3:8b";

function extractAssistantContent(reply) {
  if (typeof reply?.message?.content === "string") {
    return reply.message.content;
  }

  if (typeof reply?.response === "string") {
    return reply.response;
  }

  if (typeof reply?.message === "string") {
    return reply.message;
  }

  return JSON.stringify(reply, null, 2);
}

function missionIdFrom(match) {
  const value = Number(match?.[1]);

  if (!Number.isInteger(value) || value < 1) {
    throw new Error("Mission ID must be a positive integer.");
  }

  return value;
}

function formatTasks(data) {
  const tasks = Array.isArray(data)
    ? data
    : data?.tasks;

  if (!Array.isArray(tasks) || tasks.length === 0) {
    return "No tasks were returned for this mission.";
  }

  return tasks
    .map((task, index) => {
      const position =
        task.position ??
        task.task_position ??
        index + 1;

      const title =
        task.title ??
        task.name ??
        `Task ${position}`;

      const status =
        task.status ??
        "Unknown";

      return `${position}. [${status}] ${title}`;
    })
    .join("\n");
}

function formatMissionStatus(mission, workerData) {
  const worker = workerData?.worker ?? {};
  const lease = workerData?.lease ?? null;

  const parts = [
    `Mission ${mission.id}`,
    `Title: ${mission.title}`,
    `Status: ${mission.status}`,
    `Progress: ${mission.progress ?? 0}%`,
    `Agent: ${mission.agent ?? "Unknown"}`,
    `Priority: ${mission.priority ?? "Unknown"}`,
    `Worker: ${worker.status ?? "Unknown"}`,
  ];

  if (worker.current_task_id) {
    parts.push(
      `Current task: ${worker.current_task_id}`,
    );
  }

  if (worker.last_message) {
    parts.push(
      `Worker message: ${worker.last_message}`,
    );
  }

  if (worker.last_error) {
    parts.push(
      `Worker error: ${worker.last_error}`,
    );
  }

  if (lease) {
    parts.push("Worker lease: active");
  }

  return parts.join("\n");
}

function formatApprovalStatus(data) {
  const approval = data?.approval ?? data ?? {};
  const approved = approval.approved === true;

  const parts = [
    `Mission ${approval.mission_id} plan approval`,
    `Status: ${
      approved
        ? "Approved"
        : "Approval required"
    }`,
    `Tasks covered: ${approval.task_count ?? 0}`,
  ];

  if (approval.approved_at) {
    parts.push(
      `Approved at: ${approval.approved_at}`,
    );
  }

  if (
    approval.approved_plan_fingerprint &&
    approval.current_plan_fingerprint
  ) {
    parts.push(
      approval.approved_plan_fingerprint ===
        approval.current_plan_fingerprint
        ? "Plan fingerprint: current"
        : "Plan fingerprint: changed since approval",
    );
  }

  return parts.join("\n");
}

function helpText() {
  return [
    "NUTTZ control commands:",
    "",
    "create mission <description>",
    "run mission <id>",
    "approval status <id>",
    "approve plan <id>",
    "start worker <id>",
    "status mission <id>",
    "pause mission <id>",
    "show plan <id>",
    "show research <id>",
    "show tasks <id>",
    "show deliverable <id>",
    "help",
    "",
    "Anything else is sent to the AI model.",
  ].join("\n");
}

function canonicalViewCommand(label, missionId) {
  const normalizedLabel = label.toLowerCase();

  if (
    normalizedLabel === "details" ||
    normalizedLabel === "detail" ||
    normalizedLabel === "status"
  ) {
    return `status mission ${missionId}`;
  }

  if (
    normalizedLabel === "task" ||
    normalizedLabel === "tasks"
  ) {
    return `show tasks ${missionId}`;
  }

  if (normalizedLabel === "report") {
    return `show deliverable ${missionId}`;
  }

  return `show ${normalizedLabel} ${missionId}`;
}

function normalizeNuttzCommand(rawInput) {
  const input = rawInput
    .trim()
    .replace(/\s+/g, " ");

  let match = input.match(
    /^(?:show|open|view)(?:\s+me)?(?:\s+the)?\s+(plan|research|tasks?|deliverable|report|details?|status)(?:\s+for)?(?:\s+mission)?\s*#?\s*(\d+)$/i,
  );

  if (match) {
    return canonicalViewCommand(
      match[1],
      match[2],
    );
  }

  match = input.match(
    /^mission\s*#?\s*(\d+)\s+(plan|research|tasks?|deliverable|report|details?|status)$/i,
  );

  if (match) {
    return canonicalViewCommand(
      match[2],
      match[1],
    );
  }

  match = input.match(
    /^(?:approval\s+status|plan\s+approval\s+status)(?:\s+for)?(?:\s+mission)?\s*#?\s*(\d+)$/i,
  );

  if (match) {
    return `approval status ${match[1]}`;
  }

  match = input.match(
    /^is\s+(?:the\s+)?plan\s+approved(?:\s+for)?(?:\s+mission)?\s*#?\s*(\d+)$/i,
  );

  if (match) {
    return `approval status ${match[1]}`;
  }

  match = input.match(
    /^approve(?:\s+the)?\s+(?:plan(?:\s+for)?|mission)\s*#?\s*(\d+)$/i,
  );

  if (match) {
    return `approve plan ${match[1]}`;
  }

  match = input.match(
    /^run\s+(?:mission\s*)?#?\s*(\d+)$/i,
  );

  if (match) {
    return `run mission ${match[1]}`;
  }

  match = input.match(
    /^start\s+(?:mission\s+)?worker\s*#?\s*(\d+)$/i,
  );

  if (match) {
    return `start worker ${match[1]}`;
  }

  match = input.match(
    /^pause\s+(?:mission\s+)?(?:worker\s*)?#?\s*(\d+)$/i,
  );

  if (match) {
    return `pause mission ${match[1]}`;
  }

  return input;
}

function panelRequestFromInput(rawInput) {
  const input = normalizeNuttzCommand(rawInput);

  const patterns = [
    [
      /^approval\s+status\s+(\d+)$/i,
      "plan",
    ],
    [
      /^approve\s+plan\s+(\d+)$/i,
      "plan",
    ],
    [
      /^run\s+mission\s+(\d+)$/i,
      "plan",
    ],
    [
      /^start\s+(?:mission\s+)?worker\s+(\d+)$/i,
      "tasks",
    ],
    [
      /^(?:status\s+mission|mission\s+status)\s+(\d+)$/i,
      "details",
    ],
    [
      /^pause\s+(?:mission\s+)?(?:worker\s+)?(\d+)$/i,
      "tasks",
    ],
    [
      /^(?:show\s+)?plan\s+(?:mission\s+)?(\d+)$/i,
      "plan",
    ],
    [
      /^(?:show\s+)?research\s+(?:mission\s+)?(\d+)$/i,
      "research",
    ],
    [
      /^(?:show\s+)?tasks\s+(?:mission\s+)?(\d+)$/i,
      "tasks",
    ],
    [
      /^(?:show\s+)?deliverable\s+(?:mission\s+)?(\d+)$/i,
      "deliverable",
    ],
  ];

  for (const [pattern, panel] of patterns) {
    const match = input.match(pattern);

    if (match) {
      return {
        missionId: missionIdFrom(match),
        panel,
      };
    }
  }

  return null;
}

async function executeNuttzCommand(rawInput) {
  const input = normalizeNuttzCommand(rawInput);

  if (/^(help|commands)$/i.test(input)) {
    return {
      handled: true,
      content: helpText(),
    };
  }

  let match = input.match(
    /^create\s+mission\s+(.+)$/i,
  );

  if (match) {
    const title = match[1].trim();

    if (!title) {
      throw new Error(
        "Please provide a mission description.",
      );
    }

    const result = await createMission(title);

    return {
      handled: true,
      changedMission: true,
      content: [
        `Mission ${result.mission_id} created.`,
        `Status: Pending`,
        "",
        "Use:",
        `run mission ${result.mission_id}`,
        "to create its plan and tasks.",
      ].join("\n"),
    };
  }

  match = input.match(
    /^approval\s+status\s+(\d+)$/i,
  );

  if (match) {
    const missionId = missionIdFrom(match);
    const result =
      await getMissionApprovalStatus(missionId);

    return {
      handled: true,
      content: formatApprovalStatus(result),
    };
  }

  match = input.match(
    /^approve\s+plan\s+(\d+)$/i,
  );

  if (match) {
    const missionId = missionIdFrom(match);
    const result =
      await approveMissionPlan(missionId);

    return {
      handled: true,
      changedMission: true,
      content: [
        result.message ||
          `Mission ${missionId} plan approved.`,
        "",
        formatApprovalStatus(result),
        "",
        `Use "start worker ${missionId}" to begin execution.`,
      ].join("\n"),
    };
  }

  match = input.match(
    /^run\s+mission\s+(\d+)$/i,
  );

  if (match) {
    const missionId = missionIdFrom(match);
    const result = await runMission(missionId);

    return {
      handled: true,
      changedMission: true,
      content:
        result.message ||
        `Mission ${missionId} planning completed.`,
    };
  }

  match = input.match(
    /^start\s+(?:mission\s+)?worker\s+(\d+)$/i,
  );

  if (match) {
    const missionId = missionIdFrom(match);
    const result =
      await startMissionWorker(missionId);

    return {
      handled: true,
      changedMission: true,
      content:
        result.message ||
        `Worker started for mission ${missionId}.`,
    };
  }

  match = input.match(
    /^(?:status\s+mission|mission\s+status)\s+(\d+)$/i,
  );

  if (match) {
    const missionId = missionIdFrom(match);

    const [mission, worker] =
      await Promise.all([
        getMission(missionId),
        getMissionWorkerStatus(missionId),
      ]);

    return {
      handled: true,
      content: formatMissionStatus(
        mission,
        worker,
      ),
    };
  }

  match = input.match(
    /^pause\s+(?:mission\s+)?(?:worker\s+)?(\d+)$/i,
  );

  if (match) {
    const missionId = missionIdFrom(match);

    const result =
      await pauseMissionWorker(missionId);

    return {
      handled: true,
      changedMission: true,
      content:
        result.message ||
        `Pause requested for mission ${missionId}.`,
    };
  }

  match = input.match(
    /^(?:show\s+)?plan\s+(?:mission\s+)?(\d+)$/i,
  );

  if (match) {
    const missionId = missionIdFrom(match);

    return {
      handled: true,
      content:
        `Opening the plan for mission ${missionId}.`,
    };
  }

  match = input.match(
    /^(?:show\s+)?research\s+(?:mission\s+)?(\d+)$/i,
  );

  if (match) {
    const missionId = missionIdFrom(match);

    return {
      handled: true,
      content:
        `Opening the research for mission ${missionId}.`,
    };
  }

  match = input.match(
    /^(?:show\s+)?tasks\s+(?:mission\s+)?(\d+)$/i,
  );

  if (match) {
    const missionId = missionIdFrom(match);
    const result =
      await getMissionTasks(missionId);

    return {
      handled: true,
      content: [
        `Mission ${missionId} tasks:`,
        "",
        formatTasks(result),
      ].join("\n"),
    };
  }

  match = input.match(
    /^(?:show\s+)?deliverable\s+(?:mission\s+)?(\d+)$/i,
  );

  if (match) {
    const missionId = missionIdFrom(match);

    const result =
      await getMissionDeliverable(missionId);

    const content =
      result?.deliverable ??
      result?.content ??
      result?.report ??
      result;

    return {
      handled: true,
      content:
        typeof content === "string"
          ? content
          : JSON.stringify(content, null, 2),
    };
  }

  return {
    handled: false,
  };
}

export default function ChatPanel() {
  const [messages, setMessages] = useState([
    {
      role: "assistant",
      content:
        "Welcome to NUTTZ OS. Type help to see mission control commands.",
    },
  ]);

  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSend() {
    const cleanInput = input.trim();

    if (!cleanInput || loading) {
      return;
    }

    const userMessage = {
      role: "user",
      content: cleanInput,
    };

    const conversation = [
      ...messages,
      userMessage,
    ];

    setMessages(conversation);
    setInput("");
    setLoading(true);

    try {
      const panelRequest =
        panelRequestFromInput(cleanInput);

      const command =
        await executeNuttzCommand(cleanInput);

      if (command.handled) {
        setMessages((previous) => [
          ...previous,
          {
            role: "assistant",
            content: command.content,
          },
        ]);

        if (
          command.changedMission ||
          panelRequest
        ) {
          window.dispatchEvent(
            new CustomEvent(
              "nuttz:missions-changed",
              {
                detail: panelRequest || {},
              },
            ),
          );
        }

        return;
      }

      const modelMessages = conversation.map(
        ({ role, content }) => ({
          role,
          content,
        }),
      );

      const reply = await sendChat(
        modelMessages,
        MODEL,
      );

      setMessages((previous) => [
        ...previous,
        {
          role: "assistant",
          content:
            extractAssistantContent(reply),
        },
      ]);
    } catch (error) {
      setMessages((previous) => [
        ...previous,
        {
          role: "assistant",
          content: `NUTTZ error: ${
            error?.message ||
            "Unknown error"
          }`,
        },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="card">
      <h2>💬 NUTTZ AI Control</h2>

      <div
        style={{
          height: 420,
          overflowY: "auto",
          marginTop: 15,
          marginBottom: 15,
          padding: 10,
          background: "#0f172a",
          borderRadius: 8,
          whiteSpace: "pre-wrap",
        }}
      >
        {messages.map((msg, index) => (
          <div
            key={index}
            style={{
              marginBottom: 12,
              textAlign:
                msg.role === "user"
                  ? "right"
                  : "left",
            }}
          >
            <strong>
              {msg.role === "user"
                ? "You"
                : "NUTTZ"}
            </strong>

            <div>{msg.content}</div>
          </div>
        ))}
      </div>

      <input
        style={{
          width: "100%",
          padding: 10,
          marginBottom: 10,
        }}
        placeholder="Ask NUTTZ or type help..."
        value={input}
        onChange={(event) =>
          setInput(event.target.value)
        }
        onKeyDown={(event) => {
          if (
            event.key === "Enter" &&
            !event.shiftKey
          ) {
            handleSend();
          }
        }}
        disabled={loading}
      />

      <div
        style={{
          display: "flex",
          gap: 10,
        }}
      >
        <button
          type="button"
          style={{
            flex: 1,
            padding: 10,
          }}
          onClick={() => {
            setMessages((previous) => [
              ...previous,
              {
                role: "assistant",
                content: helpText(),
              },
            ]);
          }}
          disabled={loading}
        >
          Help / Commands
        </button>

        <button
          type="button"
          style={{
            flex: 1,
            padding: 10,
          }}
          onClick={handleSend}
          disabled={loading}
        >
          {loading
            ? "Working..."
            : "Send"}
        </button>
      </div>
    </div>
  );
}
