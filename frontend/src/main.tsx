import { FormEvent, KeyboardEvent, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  Activity,
  Bot,
  Check,
  ChevronDown,
  CircleDot,
  FileCode2,
  FolderGit2,
  GitBranch,
  Globe2,
  Plus,
  MoreHorizontal,
  Pencil,
  Send,
  Settings2,
  Shield,
  Workflow,
  TerminalSquare,
  Trash2,
  RotateCcw,
  X,
} from "lucide-react";
import { createRoot } from "react-dom/client";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "./styles.css";

type PermissionMode = "read-only" | "workspace-write" | "full-access";
type ExecutionMode = "confirm_before_coding" | "automatic" | "manual_confirmation";
type McpAccessMode = "read-only" | "workspace-write" | "full-access";
type McpTool = { name: string; description: string; input_schema: Record<string, unknown>; access_mode?: McpAccessMode };
type McpServer = { id: string; name: string; command: string; arguments: string[]; enabled: boolean; tools: McpTool[] };
type TaskMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
};
type Task = {
  id: string;
  title: string;
  requirement: string;
  permission_mode: PermissionMode;
  execution_mode: ExecutionMode;
  execution_mode_locked: boolean;
  status: string;
  current_stage: string;
  workflow_type: string;
  assigned_agent: string;
  routing_decision: RoutingDecision | null;
};
type RoutingDecision = {
  task_type: "read_only_analysis" | "development";
  complexity_reason: string;
  workflow: "read_only" | "simple" | "full";
  required_stages: string[];
};
type Provider = {
  id: string;
  name: string;
  kind: "vllm" | "external";
  base_url: string;
  model_name: string;
  is_active: boolean;
};
type SourceMode = "local" | "github";
type ActivityItem = { kind: string; title: string; detail: string };
type ChangedFile = { path: string; action: string };
type RunStage = { stage: string; agent: string; status: string; output?: string };
type RunTiming = {
  started_at: string;
  total_ms: number;
  agents: Record<string, Record<string, number>>;
};
type Run = {
  id: string;
  created_at: string;
  messageId?: string;
  title?: string;
  content: string;
  activities: ActivityItem[];
  files: ChangedFile[];
  complete: boolean;
  workflow?: RoutingDecision;
  activeAgent?: string;
  stages: RunStage[];
  error?: string;
  retryContent?: string;
  timing?: RunTiming;
};
type AgentRun = {
  id: string;
  status: string;
  created_at: string;
  result: {
    content?: string;
    activities?: ActivityItem[];
    files?: ChangedFile[];
    stages?: RunStage[];
    error?: string;
    message_id?: string;
    workflow?: RoutingDecision;
    timing?: RunTiming;
  };
};
type ConversationItem =
  | { kind: "message"; created_at: string; message: TaskMessage; trace?: Run }
  | { kind: "run"; created_at: string; run: Run };
type StageRun = {
  id: string;
  stage: string;
  agent: string;
  status: string;
  input_summary: string;
  output: string | null;
};
type ContextUsage = {
  used_tokens: number;
  total_tokens: number;
  compacted_messages: number;
  compressible_messages: number;
};
type ExecutionOperation = {
  id: string;
  kind: "patch" | "command";
  status: string;
  request: Record<string, unknown>;
  result: Record<string, unknown> | null;
  created_at: string;
};
const INITIAL_RENDERED_MESSAGES = 20;

function dedupeChangedFiles(files: ChangedFile[]): ChangedFile[] {
  return files.filter((file, index, items) =>
    items.findIndex((candidate) => candidate.path === file.path && candidate.action === file.action) === index
  );
}

function hasVisibleRunResult(run: AgentRun): boolean {
  const result = run.result;
  return Boolean(
    result.content ||
    result.error ||
    result.workflow ||
    result.timing ||
    (result.activities?.length ?? 0) > 0 ||
    (result.files?.length ?? 0) > 0 ||
    (result.stages?.length ?? 0) > 0
  );
}

function restoreAgentRuns(agentRuns: AgentRun[]): Run[] {
  return agentRuns
    .filter((run) => run.status !== "completed" || hasVisibleRunResult(run))
    .reverse()
    .map((run) => ({
      id: run.id,
      created_at: run.created_at,
      messageId: run.result.message_id,
      title: run.status === "failed" ? "Agent 运行失败" : "Agent 正在工作",
      content: run.result.content ?? "",
      activities: run.result.activities ?? [],
      files: dedupeChangedFiles(run.result.files ?? []),
      complete: run.status !== "running",
      workflow: run.result.workflow,
      activeAgent: [...(run.result.activities ?? [])].reverse().find((activity) => activity.kind === "agent")?.title,
      stages: run.result.stages ?? [],
      error: run.result.error,
      timing: run.result.timing,
    }));
}

function runMatchesMessage(run: Run, message: TaskMessage): boolean {
  return run.complete && !run.error && Boolean(run.content) && (
    run.messageId === message.id || (!run.messageId && run.content === message.content)
  );
}

const api = async <T,>(url: string, init?: RequestInit): Promise<T> => {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok)
    throw new Error((await response.text()) || response.statusText);
  if (response.status === 204) return undefined as T;
  return response.json();
};
const permissionLabels: Record<PermissionMode, string> = {
  "read-only": "只读",
  "workspace-write": "工作区读写",
  "full-access": "完全访问",
};
const executionModeLabels: Record<ExecutionMode, string> = {
  confirm_before_coding: "计划模式",
  automatic: "自动编码",
  manual_confirmation: "手动确认编码",
};
const mcpAccessLabels: Record<McpAccessMode, string> = {
  "read-only": "只读",
  "workspace-write": "工作区写入",
  "full-access": "完全访问",
};

function App() {
  const [tasks, setTasks] = useState<Task[]>([]),
    [selected, setSelected] = useState<Task | null>(null),
    [providers, setProviders] = useState<Provider[]>([]),
    [mcpServers, setMcpServers] = useState<McpServer[]>([]),
    [messages, setMessages] = useState<TaskMessage[]>([]),
    [stageRuns, setStageRuns] = useState<StageRun[]>([]),
    [contextUsage, setContextUsage] = useState<ContextUsage | null>(null),
    [runs, setRuns] = useState<Run[]>([]),
    [operations, setOperations] = useState<ExecutionOperation[]>([]),
    [draft, setDraft] = useState(""),
    [sending, setSending] = useState(false),
    [compressing, setCompressing] = useState(false),
    [notice, setNotice] = useState(""),
    [showTask, setShowTask] = useState(false),
    [taskConfig, setTaskConfig] = useState<Task | null>(null),
    [taskMenu, setTaskMenu] = useState<string | null>(null),
    [showProviders, setShowProviders] = useState(false),
    [showMcpServers, setShowMcpServers] = useState(false),
    [showModels, setShowModels] = useState(false),
    [showPermissions, setShowPermissions] = useState(false),
    [showExecutionModes, setShowExecutionModes] = useState(false),
    [showWorkPanel, setShowWorkPanel] = useState(true),
    [renderedMessageCount, setRenderedMessageCount] = useState(INITIAL_RENDERED_MESSAGES),
    [scrollToLatestRequest, setScrollToLatestRequest] = useState(0);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const messageListRef = useRef<HTMLDivElement>(null);
  const activeProvider =
    providers.find((provider) => provider.is_active) ?? null;
  const load = async () => {
    try {
      const [taskList, providerList, serverList] = await Promise.all([
        api<Task[]>("/api/tasks"),
        api<Provider[]>("/api/model-providers"),
        api<McpServer[]>("/api/mcp-servers"),
      ]);
      setTasks(taskList);
      setProviders(providerList);
      setMcpServers(serverList);
    } catch (error) {
      setNotice(String(error).replace(/^Error: /, ""));
    }
  };
  useEffect(() => {
    void load();
  }, []);
  const refreshTaskWorkflow = async (taskId: string) => {
    const [task, taskMessages, stages, context, taskOperations, agentRuns] = await Promise.all([
      api<Task>(`/api/tasks/${taskId}`),
      api<TaskMessage[]>(`/api/tasks/${taskId}/messages`),
      api<StageRun[]>(`/api/tasks/${taskId}/stages`),
      api<ContextUsage>(`/api/tasks/${taskId}/context`),
      api<ExecutionOperation[]>(`/api/tasks/${taskId}/operations`),
      api<AgentRun[]>(`/api/tasks/${taskId}/agent-runs`),
    ]);
    setSelected(task);
    setTasks((items) =>
      items.map((item) => (item.id === task.id ? task : item)),
    );
    setMessages(taskMessages);
    setStageRuns(stages);
    setContextUsage(context);
    setOperations(taskOperations);
    setRuns(restoreAgentRuns(agentRuns));
    return task;
  };
  useEffect(() => {
    if (!selected) {
      setMessages([]);
      setStageRuns([]);
      setRuns([]);
      setContextUsage(null);
      setOperations([]);
      return;
    }
    setRuns([]);
    Promise.all([
      api<TaskMessage[]>(`/api/tasks/${selected.id}/messages`),
      api<StageRun[]>(`/api/tasks/${selected.id}/stages`),
      api<ContextUsage>(`/api/tasks/${selected.id}/context`), api<ExecutionOperation[]>(`/api/tasks/${selected.id}/operations`),
      api<AgentRun[]>(`/api/tasks/${selected.id}/agent-runs`),
    ])
      .then(([items, stages, context, taskOperations, agentRuns]) => {
        setMessages(items);
        setStageRuns(stages);
        setContextUsage(context);
        setOperations(taskOperations);
        setRuns(restoreAgentRuns(agentRuns));
      })
      .catch((error) => setNotice(String(error).replace(/^Error: /, "")));
  }, [selected?.id]);
  useEffect(() => {
    if (!selected) return;
    const timer = window.setInterval(() => {
      api<ExecutionOperation[]>(`/api/tasks/${selected.id}/operations`).then(setOperations).catch(() => undefined);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [selected?.id]);
  useLayoutEffect(() => {
    if (scrollToLatestRequest === 0) return;
    const messageList = messageListRef.current;
    if (messageList)
      messageList.scrollTop = messageList.scrollHeight;
  }, [messages.length, runs.length, scrollToLatestRequest]);
  const selectTask = (task: Task) => {
    setRenderedMessageCount(INITIAL_RENDERED_MESSAGES);
    setScrollToLatestRequest((request) => request + 1);
    setSelected(task);
  };
  const attachedRunIds = new Set<string>();
  const messageTimelineItems: ConversationItem[] = messages.map((message) => {
    const trace = message.role === "assistant"
      ? runs.find((run) => !attachedRunIds.has(run.id) && runMatchesMessage(run, message))
      : undefined;
    if (trace) attachedRunIds.add(trace.id);
    return { kind: "message", created_at: message.created_at, message, trace };
  });
  const timelineItems: ConversationItem[] = [
    ...messageTimelineItems,
    ...runs.filter((run) => !attachedRunIds.has(run.id)).map((run) => ({ kind: "run" as const, created_at: run.created_at, run })),
  ].sort((left, right) => {
    const timeDifference = Date.parse(left.created_at) - Date.parse(right.created_at);
    return timeDifference || (left.kind === "message" ? -1 : 1);
  });
  const renderedTimelineItems = timelineItems.slice(-renderedMessageCount);
  const hiddenMessageCount = timelineItems.length - renderedTimelineItems.length;
  const selectProvider = async (provider: Provider) => {
    try {
      await api<Provider>(`/api/model-providers/${provider.id}/activate`, {
        method: "POST",
      });
      setProviders((items) =>
        items.map((item) => ({ ...item, is_active: item.id === provider.id })),
      );
      setShowModels(false);
      composerRef.current?.focus();
    } catch (error) {
      setNotice(String(error).replace(/^Error: /, ""));
    }
  };
  const selectPermission = async (permission_mode: PermissionMode) => {
    if (!selected) return;
    try {
      const task = await api<Task>(`/api/tasks/${selected.id}/permission`, {
        method: "PATCH",
        body: JSON.stringify({ permission_mode }),
      });
      setSelected(task);
      setTasks((items) =>
        items.map((item) => (item.id === task.id ? task : item)),
      );
      setShowPermissions(false);
      composerRef.current?.focus();
    } catch (error) {
      setNotice(String(error).replace(/^Error: /, ""));
    }
  };
  const selectExecutionMode = async (execution_mode: ExecutionMode) => {
    if (!selected || selected.execution_mode_locked) return;
    try {
      const task = await api<Task>(`/api/tasks/${selected.id}/execution-mode`, {
        method: "PATCH",
        body: JSON.stringify({ execution_mode }),
      });
      setSelected(task);
      setTasks((items) => items.map((item) => (item.id === task.id ? task : item)));
      setShowExecutionModes(false);
      composerRef.current?.focus();
    } catch (error) {
      setNotice(String(error).replace(/^Error: /, ""));
    }
  };
  const deleteTask = async (task: Task) => {
    if (!window.confirm(`删除“${task.title}”及其所有对话记录？此操作无法撤销。`)) {
      return;
    }
    try {
      await api<void>(`/api/tasks/${task.id}`, { method: "DELETE" });
      setTaskMenu(null);
      setTasks((items) => items.filter((item) => item.id !== task.id));
      if (selected?.id === task.id) setSelected(null);
    } catch (error) {
      setNotice(String(error).replace(/^Error: /, ""));
    }
  };
  const updateRun = (id: string, update: (run: Run) => Run) =>
    setRuns((items) =>
      items.map((item) => (item.id === id ? update(item) : item)),
    );
  const updateOperation = async (operation: ExecutionOperation, action: "approval" | "cancel" | "undo", approve?: boolean) => {
    if (!selected) return;
    try {
      await api<ExecutionOperation>(`/api/tasks/${selected.id}/operations/${operation.id}/${action}`, {
        method: "POST", body: action === "approval" ? JSON.stringify({ approve }) : undefined,
      });
      await refreshTaskWorkflow(selected.id);
    } catch (error) { setNotice(String(error).replace(/^Error: /, "")); }
  };
  const commitAgentChanges = async () => {
    if (!selected) return;
    const message = window.prompt("提交说明");
    if (!message?.trim()) return;
    try {
      await api(`/api/tasks/${selected.id}/git/commit`, { method: "POST", body: JSON.stringify({ message }) });
      await refreshTaskWorkflow(selected.id);
    } catch (error) { setNotice(String(error).replace(/^Error: /, "")); }
  };
  const compressContext = async () => {
    if (!selected || sending || compressing) return;
    const taskId = selected.id;
    const runId = `context-${Date.now()}`;
    setRuns((items) => [
      ...items,
      {
        id: runId,
        created_at: new Date().toISOString(),
        title: "正在压缩上下文",
        content: "",
        activities: [
          { kind: "agent", title: "上下文压缩", detail: "正在准备压缩" },
        ],
        stages: [],
        files: [],
        complete: false,
      },
    ]);
    setCompressing(true);
    try {
      const response = await fetch(`/api/tasks/${taskId}/context/compress/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      if (!response.ok || !response.body)
        throw new Error((await response.text()) || response.statusText);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const next = await reader.read();
        if (next.done) break;
        buffer += decoder.decode(next.value, { stream: true });
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop() ?? "";
        for (const chunk of chunks) {
          const eventName = chunk.match(/^event: (.+)$/m)?.[1];
          const data = chunk.match(/^data: (.+)$/m)?.[1];
          if (!eventName || !data) continue;
          const payload = JSON.parse(data);
          if (eventName === "activity")
            updateRun(runId, (run) => ({
              ...run,
              activities: [...run.activities, payload],
            }));
          if (eventName === "workflow")
            updateRun(runId, (run) => ({ ...run, workflow: payload }));
          if (eventName === "done") {
            setContextUsage(payload.context);
            updateRun(runId, (run) => ({
              ...run,
              title: "上下文压缩完成",
              complete: true,
              activities: [
                ...run.activities,
                {
                  kind: "agent",
                  title: "上下文压缩完成",
                  detail: `已压缩至 ${payload.context.used_tokens.toLocaleString()} / ${payload.context.total_tokens.toLocaleString()} tokens`,
                },
              ],
            }));
          }
          if (eventName === "error")
            updateRun(runId, (run) => ({
              ...run,
              title: "上下文压缩失败",
              complete: true,
              error: payload.message,
            }));
        }
      }
      composerRef.current?.focus();
    } catch (error) {
      updateRun(runId, (run) => ({
        ...run,
        title: "上下文压缩失败",
        complete: true,
        error: String(error).replace(/^Error: /, ""),
      }));
    } finally {
      setCompressing(false);
    }
  };
  const sendMessage = async (event?: FormEvent, retryContent?: string) => {
    event?.preventDefault();
    const content = retryContent ?? draft.trim();
    if (!selected || !content || sending || compressing) return;
    if (!activeProvider) {
      setNotice("请先选择模型档案。");
      setShowModels(true);
      return;
    }
    const taskId = selected.id,
      runId = `run-${Date.now()}`;
    setMessages((items) => [
      ...items,
      { id: `pending-${Date.now()}`, role: "user", content, created_at: new Date().toISOString() },
    ]);
    setRuns((items) => [
      ...items,
      {
        id: runId,
        created_at: new Date().toISOString(),
        content: "",
        activities: [{ kind: "agent", title: "Main Agent", detail: "正在启动连续执行任务" }],
        activeAgent: "Main Agent",
        stages: [],
        files: [],
        complete: false,
        timing: { started_at: new Date().toISOString(), total_ms: 0, agents: {} },
      },
    ]);
    setDraft("");
    setSending(true);
    try {
      const response = await fetch(`/api/tasks/${taskId}/messages/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, continuation: false }),
      });
      if (!response.ok || !response.body)
        throw new Error((await response.text()) || response.statusText);
      const reader = response.body.getReader(),
        decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const next = await reader.read();
        if (next.done) break;
        buffer += decoder.decode(next.value, { stream: true });
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop() ?? "";
        for (const chunk of chunks) {
          const eventName = chunk.match(/^event: (.+)$/m)?.[1];
          const data = chunk.match(/^data: (.+)$/m)?.[1];
          if (!eventName || !data) continue;
          const payload = JSON.parse(data);
          if (eventName === "activity")
            updateRun(runId, (run) => ({
              ...run,
              activities: [...run.activities, payload],
              activeAgent: payload.kind === "agent" ? payload.title : run.activeAgent,
            }));
          if (eventName === "timing")
            updateRun(runId, (run) => ({ ...run, timing: payload }));
          if (eventName === "token")
            updateRun(runId, (run) => ({
              ...run,
              content: run.content + payload.content,
            }));
          if (eventName === "file")
            updateRun(runId, (run) => ({
              ...run,
              files: dedupeChangedFiles([...run.files, payload]),
            }));
          if (eventName === "done") {
            updateRun(runId, (run) => ({
              ...run,
              content: payload.message.content,
              complete: true,
            }));
          }
          if (eventName === "error")
            updateRun(runId, (run) => ({
              ...run,
              title: payload.retryable ? "主 Agent 路由失败" : run.title,
              complete: true,
              error: payload.message,
              retryContent: payload.retryable ? content : undefined,
            }));
        }
      }
      await refreshTaskWorkflow(taskId);
    } catch (error) {
      updateRun(runId, (run) => ({
        ...run,
        complete: true,
        error: String(error).replace(/^Error: /, ""),
        retryContent: content,
      }));
    } finally {
      setSending(false);
    }
  };
  const onComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault();
      void sendMessage();
    }
  };
  return (
    <main>
      <header>
        <div className="brand">
          <Bot size={20} />
          <span>Agent Workbench</span>
        </div>
        <div className="header-actions">
          <button
            className="settings-button"
            title="配置模型接口"
            onClick={() => setShowProviders(true)}
          >
            <Settings2 size={16} /> 配置模型接口
          </button>
          <button
            className="settings-button"
            title="配置本地 MCP Server"
            onClick={() => setShowMcpServers(true)}
          >
            <TerminalSquare size={16} /> MCP Server
          </button>
        </div>
      </header>
      {notice && (
        <div className="notice">
          {notice}
          <button title="关闭" onClick={() => setNotice("")}>
            <X size={15} />
          </button>
        </div>
      )}
      <section className={`layout ${showWorkPanel && selected ? "work-panel-open" : "work-panel-hidden"}`}>
        <aside className="sidebar">
          <div className="section-title">
            <span>任务</span>
            <button title="新建任务" onClick={() => setShowTask(true)}>
              <Plus size={16} />
            </button>
          </div>
          {tasks.map((task) => (
            <div
              className={`task-row ${selected?.id === task.id ? "active" : ""}`}
              key={task.id}
            >
              <button className="task" onClick={() => selectTask(task)}>
                <span>{task.title}</span>
              </button>
              <button
                type="button"
                className="task-menu-button"
                title={`配置任务：${task.title}`}
                aria-label={`配置任务：${task.title}`}
                aria-expanded={taskMenu === task.id}
                onClick={() => setTaskMenu((open) => (open === task.id ? null : task.id))}
              >
                <MoreHorizontal size={16} />
              </button>
              {taskMenu === task.id && (
                <div className="task-menu">
                  <button type="button" onClick={() => { setTaskConfig(task); setTaskMenu(null); }}>
                    <Pencil size={15} /> 更改配置
                  </button>
                  <button type="button" className="danger" onClick={() => void deleteTask(task)}>
                    <Trash2 size={15} /> 删除任务
                  </button>
                </div>
              )}
            </div>
          ))}
        </aside>
        <section className="workbench">
          {selected ? (
            <div className="chat-shell">
              <div className="chat-title">
                <h1>{selected.title}</h1>
                <button className="work-panel-toggle" type="button" title={showWorkPanel ? "隐藏工作区" : "显示工作区"} aria-label={showWorkPanel ? "隐藏工作区" : "显示工作区"} onClick={() => setShowWorkPanel((visible) => !visible)}>
                  <Settings2 size={16} />
                </button>
              </div>
              <div className="message-list" ref={messageListRef}>
                {messages.length === 0 && (
                  <div className="empty-chat">
                    <Activity size={34} />
                    <h2>开始这个任务</h2>
                    <p>描述你想完成的工作，Agent 会保留对话上下文。</p>
                  </div>
                )}
                {hiddenMessageCount > 0 && (
                  <button
                    type="button"
                    className="load-earlier-messages"
                    onClick={() => setRenderedMessageCount((count) => count + INITIAL_RENDERED_MESSAGES)}
                  >
                    加载更早消息（{hiddenMessageCount}）
                  </button>
                )}
                {renderedTimelineItems.map((item) => {
                  if (item.kind === "run") {
                    return <RunOutput key={item.run.id} run={item.run} taskId={selected.id}
                      onRetry={(content) => void sendMessage(undefined, content)} />;
                  }
                  const { message } = item;
                  const stageRun = message.role === "assistant"
                    ? stageRuns.find((run) => run.output === message.content)
                    : undefined;
                  return (
                    <article className={`message ${message.role}`} key={message.id}>
                      {message.role === "assistant" && <div className="message-role">Agent</div>}
                      {item.trace && <AgentRunTrace run={item.trace} taskId={selected.id}
                        onRetry={(content) => void sendMessage(undefined, content)} />}
                      {stageRun && <StageRunTrace run={stageRun} task={selected} />}
                      {message.role === "assistant" ? (
                        <MarkdownContent content={message.content} taskId={selected.id} />
                      ) : (
                        <p>{message.content}</p>
                      )}
                    </article>
                  );
                })}
              </div>
              <form className="message-composer" onSubmit={sendMessage}>
                <textarea
                  ref={composerRef}
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={onComposerKeyDown}
                  placeholder="给 Agent 下达任务或继续对话"
                  aria-label="消息内容"
                />
                <div className="composer-footer">
                  <div className="execution-mode-picker">
                    <button
                      type="button"
                      className="execution-mode-button"
                      disabled={selected.execution_mode_locked}
                      onClick={() => setShowExecutionModes((open) => !open)}
                      title={selected.execution_mode_locked ? "正在编码或修复，执行模式暂时锁定" : "选择当前任务的执行模式"}
                    >
                      <Workflow size={15} />
                      <span>{executionModeLabels[selected.execution_mode]}</span>
                      <ChevronDown size={14} />
                    </button>
                    {showExecutionModes && !selected.execution_mode_locked && (
                      <div className="execution-mode-menu">
                        {(Object.keys(executionModeLabels) as ExecutionMode[]).map((mode) => (
                          <button type="button" key={mode} className={selected.execution_mode === mode ? "selected" : ""} onClick={() => void selectExecutionMode(mode)}>
                            <span>
                              <strong>{executionModeLabels[mode]}</strong>
                              <small>{mode === "confirm_before_coding" ? "完成分析与设计后，编码前等待确认" : mode === "manual_confirmation" ? "每组代码补丁显示 diff，确认后应用" : "完成设计后连续进入编码、审核和测试"}</small>
                            </span>
                            {selected.execution_mode === mode && <Check size={15} />}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="permission-picker">
                    <button
                      type="button"
                      className="permission-button"
                      onClick={() => setShowPermissions((open) => !open)}
                      title="选择 Agent 权限"
                    >
                      <Shield size={15} />
                      <span>{permissionLabels[selected.permission_mode]}</span>
                      <ChevronDown size={14} />
                    </button>
                    {showPermissions && (
                      <div className="permission-menu">
                        {(
                          Object.keys(permissionLabels) as PermissionMode[]
                        ).map((mode) => (
                          <button
                            type="button"
                            key={mode}
                            className={
                              selected.permission_mode === mode
                                ? "selected"
                                : ""
                            }
                            onClick={() => void selectPermission(mode)}
                          >
                            <span>
                              <strong>{permissionLabels[mode]}</strong>
                              <small>
                                {mode === "read-only"
                                  ? "仅查看任务代码目录"
                                  : mode === "workspace-write"
                                    ? "可修改任务代码目录"
                                    : "可访问本地路径并执行命令"}
                              </small>
                            </span>
                            {selected.permission_mode === mode && (
                              <Check size={15} />
                            )}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="composer-actions">
                    {contextUsage && (
                      <div className="context-monitor">
                        <button
                          type="button"
                          className="context-ring"
                          style={{
                            background: `conic-gradient(#20262b ${Math.round((contextUsage.used_tokens / contextUsage.total_tokens) * 100)}%, #d9dfe2 0)`,
                          }}
                          aria-label="上下文用量"
                        />
                        <div className="context-popover" role="status">
                          <strong>
                            {contextUsage.used_tokens.toLocaleString()} / {contextUsage.total_tokens.toLocaleString()} tokens
                          </strong>
                          <button
                            type="button"
                            onClick={() => void compressContext()}
                            disabled={
                              sending ||
                              compressing ||
                              contextUsage.compressible_messages === 0
                            }
                          >
                            压缩上下文
                          </button>
                        </div>
                      </div>
                    )}
                    <div className="provider-picker">
                      <button
                        type="button"
                        className="model-button"
                        onClick={() => setShowModels((open) => !open)}
                        title="选择模型档案"
                      >
                        <Bot size={15} />
                        <span>{activeProvider?.name ?? "选择模型档案"}</span>
                        <ChevronDown size={14} />
                      </button>
                      {showModels && (
                        <div className="provider-menu">
                          {providers.length ? (
                            providers.map((provider) => (
                              <button
                                type="button"
                                key={provider.id}
                                className={provider.is_active ? "selected" : ""}
                                onClick={() => void selectProvider(provider)}
                              >
                                <span>
                                  <strong>{provider.name}</strong>
                                  <small>{provider.model_name}</small>
                                </span>
                                {provider.is_active && <Check size={15} />}
                              </button>
                            ))
                          ) : (
                            <div className="no-providers">
                              暂无模型档案
                              <button
                                type="button"
                                onClick={() => {
                                  setShowModels(false);
                                  setShowProviders(true);
                                }}
                              >
                                去添加
                              </button>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                    <button
                      className="send-button"
                      disabled={sending || compressing || !draft.trim()}
                      title="发送"
                    >
                      <Send size={17} />
                    </button>
                  </div>
                </div>
              </form>
            </div>
          ) : (
            <div className="welcome">
              <Bot size={46} />
              <h1>开始一个任务</h1>
              <p>创建任务后，在对话中描述你的需求。</p>
              <button className="primary" onClick={() => setShowTask(true)}>
                <Plus size={16} /> 新建任务
              </button>
            </div>
          )}
        </section>
        {selected && showWorkPanel && (
          <aside className="work-panel" aria-label="工作区">
            <div className="work-panel-title"><span>工作区</span></div>
            <ExecutionPanel operations={operations} onAction={updateOperation} onCommit={() => void commitAgentChanges()} />
            <section className="workspace-section" aria-label="环境信息">
              <div className="workspace-section-title"><span>环境信息</span><Plus size={16} /></div>
              <dl className="environment-facts">
                <div><dt>权限</dt><dd>{permissionLabels[selected.permission_mode]}</dd></div>
                <div><dt>模式</dt><dd>{executionModeLabels[selected.execution_mode]}</dd></div>
                <div><dt>阶段</dt><dd>{selected.current_stage}</dd></div>
              </dl>
            </section>
          </aside>
        )}
      </section>
      {showTask && (
        <TaskModal
          onClose={() => setShowTask(false)}
          onCreated={(task) => {
            setShowTask(false);
            setSelected(task);
            void load();
          }}
        />
      )}
      {taskConfig && (
        <TaskConfigModal
          task={taskConfig}
          onClose={() => setTaskConfig(null)}
          onUpdated={(task) => {
            setTasks((items) => items.map((item) => (item.id === task.id ? task : item)));
            if (selected?.id === task.id) setSelected(task);
            setTaskConfig(null);
          }}
        />
      )}
      {showProviders && (
        <ProviderModal
          providers={providers}
          onClose={() => setShowProviders(false)}
          onChanged={load}
        />
      )}
      {showMcpServers && (
        <McpServerModal
          servers={mcpServers}
          onClose={() => setShowMcpServers(false)}
          onChanged={load}
        />
      )}
    </main>
  );
}

function MarkdownContent({
  content,
  taskId,
}: {
  content: string;
  taskId: string;
}) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => {
            const localPath =
              href && (/^[A-Za-z]:[\\/]/.test(href) || href.startsWith("/"));
            const destination = localPath
              ? `/api/tasks/${taskId}/files?path=${encodeURIComponent(href)}`
              : href;
            return (
              <a href={destination} target="_blank" rel="noreferrer">
                {children}
              </a>
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

const stageAgents: Record<string, string> = {
  "阅读分析": "阅读 Agent",
  "需求分析": "主 Agent",
  "概要设计": "阅读 Agent",
  "详细设计": "阅读 Agent",
  "编码实现": "执行 Agent",
  "代码审核": "审查 Agent",
  "单元测试": "测试 Agent",
};

function AgentFlow({ decision, activeAgent }: { decision: RoutingDecision; activeAgent?: string }) {
  const agentFlow = Array.from(new Set(["主 Agent", ...decision.required_stages.map((stage) => stageAgents[stage] ?? "Agent")]));
  return (
    <div className="workflow-plan">
      <p className="workflow-plan-title">协作流程</p>
      <div className="workflow-agent-flow">
        {agentFlow.map((agent, index) => (
          <span className={`workflow-agent ${agent === activeAgent ? "active" : ""}`} key={agent}>
            {index > 0 && <span className="workflow-arrow">→</span>}
            {agent}
          </span>
        ))}
      </div>
    </div>
  );
}

function ExecutionPanel({ operations, onAction, onCommit }: {
  operations: ExecutionOperation[];
  onAction: (operation: ExecutionOperation, action: "approval" | "cancel" | "undo", approve?: boolean) => Promise<void>;
  onCommit: () => void;
}) {
  const active = operations.filter((operation) => operation.status !== "completed" && operation.status !== "undone");
  const completedPatches = operations.filter((operation) => operation.kind === "patch" && operation.status === "completed");
  if (!operations.length) return null;
  return (
    <section className="execution-panel" aria-label="执行记录">
      <div className="execution-panel-heading"><strong>执行记录</strong>{completedPatches.length > 0 && <button type="button" onClick={onCommit}><GitBranch size={14} /> 提交 Agent 变更</button>}</div>
      {operations.slice(0, 8).map((operation) => {
        const result = operation.result ?? {};
        const files = Array.isArray(result.files) ? result.files as { path: string; diff: string }[] : [];
        return <article className={`execution-operation ${operation.status}`} key={operation.id}>
          <div className="execution-operation-title"><TerminalSquare size={14} /><strong>{operation.kind === "patch" ? "代码补丁" : "命令"}</strong><span>{operation.status}</span></div>
          {files.map((file) => <details key={file.path}><summary>{file.path}</summary><pre>{file.diff}</pre></details>)}
          {typeof result.stdout === "string" && <pre className="terminal-output">{result.stdout}{typeof result.stderr === "string" ? result.stderr : ""}</pre>}
          <div className="execution-operation-actions">
            {operation.status === "pending_approval" && <><button type="button" className="primary" onClick={() => void onAction(operation, "approval", true)}>应用补丁</button><button type="button" onClick={() => void onAction(operation, "approval", false)}>拒绝</button></>}
            {["queued", "running", "pending_approval"].includes(operation.status) && <button type="button" onClick={() => void onAction(operation, "cancel")}>取消</button>}
            {operation.kind === "patch" && operation.status === "completed" && <button type="button" onClick={() => void onAction(operation, "undo")}><RotateCcw size={14} /> 撤销</button>}
          </div>
        </article>;
      })}
      {active.length > 0 && <small className="execution-active">{active.length} 个操作仍在进行或等待处理</small>}
    </section>
  );
}

function StageRunTrace({ run, task }: { run: StageRun; task: Task }) {
  const decision = task.routing_decision;
  return (
    <details className="activity-trace completed-trace">
      <summary>
        <Activity size={15} /> 工作过程
      </summary>
      <div>
        {decision && <AgentFlow decision={decision} />}
        <p className="activity agent">
          <CircleDot size={14} />
          <span>
            <strong>{run.agent} · {run.stage}</strong>
            {run.status === "completed" ? "已完成" : "进行中"}
          </span>
        </p>
        {run.input_summary && <p className="stage-run-input">{run.input_summary}</p>}
      </div>
    </details>
  );
}

function formatDuration(milliseconds: number) {
  return milliseconds < 1000 ? `${milliseconds} ms` : `${(milliseconds / 1000).toFixed(1)} s`;
}

function TimingSummary({ timing }: { timing: RunTiming }) {
  const metricLabels: Record<string, string> = {
    model_ms: "模型响应",
    tool_ms: "工具调用",
    operation_wait_ms: "等待命令或补丁",
    approval_wait_ms: "等待确认",
  };
  const entries = Object.entries(timing.agents).flatMap(([agent, metrics]) =>
    Object.entries(metrics).map(([metric, elapsed]) => ({ agent, metric, elapsed })),
  );
  return (
    <section className="timing-summary" aria-label="本轮 Agent 耗时">
      <p><strong>本轮耗时</strong><span>{formatDuration(timing.total_ms)}</span></p>
      {entries.map(({ agent, metric, elapsed }) => (
        <p key={`${agent}-${metric}`}>
          <span>{agent} / {metricLabels[metric] ?? metric}</span>
          <strong>{formatDuration(elapsed)}</strong>
        </p>
      ))}
    </section>
  );
}

function AgentRunTrace({ run, taskId, onRetry, open = false }: {
  run: Run;
  taskId: string;
  onRetry: (content: string) => void;
  open?: boolean;
}) {
  const icon = (kind: string) =>
    kind === "network" ? (
      <Globe2 size={14} />
    ) : kind === "tool" ? (
      <TerminalSquare size={14} />
    ) : (
      <CircleDot size={14} />
    );
  return (
    <>
      {!run.complete && <span className="agent-working-spinner" aria-label="Agent 正在工作" />}
      <details className="activity-trace" open={open}>
        <summary>
          <Activity size={15} /> {run.complete ? (run.title ?? "工作过程") : (run.title ?? "Agent 正在工作")}
        </summary>
        <div>
          {run.workflow && <AgentFlow decision={run.workflow} activeAgent={run.activeAgent} />}
          {run.timing && <TimingSummary timing={run.timing} />}
          {run.activities.map((item, index) => (
            <p
              className={`activity ${item.kind}`}
              key={`${item.title}-${index}`}
            >
              {icon(item.kind)}
              <span>
                <strong>{item.title}</strong>
                {item.detail}
              </span>
            </p>
          ))}
          {run.stages.map((stage) => (
            <section className="run-stage" key={`${stage.stage}-${stage.agent}`}>
              <p><span className={stage.status === "running" ? "active-stage" : ""}>{stage.agent} · {stage.stage}</span></p>
              {stage.output && <MarkdownContent content={stage.output} taskId={taskId} />}
            </section>
          ))}
        </div>
      </details>
      {run.error ? (
        <>
          <p className="stream-error">{run.error}</p>
          {run.retryContent && (
            <button className="retry-button" type="button" onClick={() => onRetry(run.retryContent!)}>
              <RotateCcw size={15} /> 重试主 Agent 判定
            </button>
          )}
        </>
      ) : null}
      {run.files.length > 0 && (
        <div className="changed-files">
          <span>更改的文件</span>
          {run.files.map((file) => (
            <a
              key={file.path}
              href={`/api/tasks/${taskId}/files?path=${encodeURIComponent(file.path)}`}
              target="_blank"
              rel="noreferrer"
            >
              <FileCode2 size={14} /> {file.path}
            </a>
          ))}
        </div>
      )}
    </>
  );
}

function RunOutput({ run, taskId, onRetry }: { run: Run; taskId: string; onRetry: (content: string) => void }) {
  return (
    <article className={`message assistant streamed ${!run.complete ? "working" : ""}`}>
      <AgentRunTrace run={run} taskId={taskId} onRetry={onRetry} open={!run.complete} />
      {!run.error && run.content && run.stages.length === 0 && <MarkdownContent content={run.content} taskId={taskId} />}
    </article>
  );
}

function ProviderModal({
  providers,
  onClose,
  onChanged,
}: {
  providers: Provider[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const [name, setName] = useState(""),
    [kind, setKind] = useState<"vllm" | "external">("external"),
    [baseUrl, setBaseUrl] = useState("https://api.openai.com/v1"),
    [modelName, setModelName] = useState(""),
    [apiKey, setApiKey] = useState(""),
    [message, setMessage] = useState("");
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      await api<Provider>("/api/model-providers", {
        method: "POST",
        body: JSON.stringify({
          name,
          kind,
          base_url: baseUrl,
          model_name: modelName,
          api_key: apiKey,
        }),
      });
      setMessage("模型档案已保存。");
      setName("");
      setModelName("");
      setApiKey("");
      void onChanged();
    } catch (error) {
      setMessage(String(error).replace(/^Error: /, ""));
    }
  };
  return (
    <div className="modal-back">
      <section className="modal provider-modal">
        <div className="modal-heading">
          <h2>配置模型接口</h2>
          <button title="关闭" onClick={onClose}>
            <X size={17} />
          </button>
        </div>
        <div className="provider-list">
          {providers.map((provider) => (
            <div className="provider-row" key={provider.id}>
              <div>
                <strong>{provider.name}</strong>
                <small>
                  {provider.kind} · {provider.model_name}
                </small>
              </div>
            </div>
          ))}
        </div>
        <form onSubmit={submit} className="provider-form">
          <h3>添加模型档案</h3>
          <label>
            接口类型
            <select
              value={kind}
              onChange={(event) =>
                setKind(event.target.value as "vllm" | "external")
              }
            >
              <option value="external">OpenAI 兼容 API</option>
              <option value="vllm">vLLM</option>
            </select>
          </label>
          <label>
            档案名称
            <input
              required
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label>
            Base URL
            <input
              required
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
            />
          </label>
          <label>
            模型名称
            <input
              required
              value={modelName}
              onChange={(event) => setModelName(event.target.value)}
            />
          </label>
          <label>
            API Key
            <input
              type="password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
            />
          </label>
          {message && <p className="form-message">{message}</p>}
          <div className="modal-actions">
            <button type="button" onClick={onClose}>
              关闭
            </button>
            <button className="primary">保存档案</button>
          </div>
        </form>
      </section>
    </div>
  );
}
function TaskModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (task: Task) => void;
}) {
  const [sourceType, setSourceType] = useState<SourceMode>("local"),
    [localPath, setLocalPath] = useState(""),
    [githubUrl, setGithubUrl] = useState(""),
    [clonePath, setClonePath] = useState(""),
    [title, setTitle] = useState(""),
    [permissionMode, setPermissionMode] = useState<PermissionMode>("full-access"),
    [executionMode, setExecutionMode] = useState<ExecutionMode>("automatic"),
    [error, setError] = useState("");
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      onCreated(
        await api<Task>("/api/tasks", {
          method: "POST",
          body: JSON.stringify({
            source_type: sourceType,
            local_path: sourceType === "local" ? localPath : undefined,
            github_url: sourceType === "github" ? githubUrl : undefined,
            clone_path: sourceType === "github" ? clonePath : undefined,
            title,
            permission_mode: permissionMode,
            execution_mode: executionMode,
            test_command: ["python", "-m", "pytest"],
          }),
        }),
      );
    } catch (reason) {
      setError(String(reason).replace(/^Error: /, ""));
    }
  };
  return (
    <div className="modal-back">
      <form className="modal task-modal" onSubmit={submit}>
        <div className="modal-heading">
          <h2>新建任务</h2>
          <button type="button" title="关闭" onClick={onClose}>
            <X size={17} />
          </button>
        </div>
        <div className="source-switch">
          <button
            type="button"
            className={sourceType === "local" ? "selected" : ""}
            onClick={() => setSourceType("local")}
          >
            <FolderGit2 size={15} /> 本地 Git 文件夹
          </button>
          <button
            type="button"
            className={sourceType === "github" ? "selected" : ""}
            onClick={() => setSourceType("github")}
          >
            <GitBranch size={15} /> Git 仓库
          </button>
        </div>
        {sourceType === "local" ? (
          <label>
            本地 Git 目录
            <input
              required
              value={localPath}
              onChange={(event) => setLocalPath(event.target.value)}
              placeholder="C:\\Projects\\my-app"
            />
          </label>
        ) : (
          <>
            <label>
              GitHub 仓库链接
              <input
                required
                value={githubUrl}
                onChange={(event) => setGithubUrl(event.target.value)}
              />
            </label>
            <label>
              保存到本地目录
              <input
                required
                value={clonePath}
                onChange={(event) => setClonePath(event.target.value)}
              />
            </label>
          </>
        )}
        <label>
          任务标题
          <input
            required
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <label>
          Agent 权限
          <select value={permissionMode} onChange={(event) => setPermissionMode(event.target.value as PermissionMode)}>
            <option value="full-access">完全访问：可修改任务目录并运行本地命令</option>
            <option value="workspace-write">工作区读写：可修改任务目录，不运行命令</option>
            <option value="read-only">只读：只查看文件</option>
          </select>
        </label>
        <label>
          执行模式
          <select value={executionMode} onChange={(event) => setExecutionMode(event.target.value as ExecutionMode)}>
            <option value="automatic">自动执行：连续读取、修改、验证</option>
            <option value="manual_confirmation">手动确认补丁：每组修改先预览</option>
            <option value="confirm_before_coding">旧式编码前确认</option>
          </select>
        </label>
        {error && <p className="error">{error}</p>}
        <div className="modal-actions">
          <button type="button" onClick={onClose}>
            取消
          </button>
          <button className="primary">创建任务</button>
        </div>
      </form>
    </div>
  );
}

function McpServerModal({
  servers,
  onClose,
  onChanged,
}: {
  servers: McpServer[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState<McpServer | null>(null);
  const [showCustomForm, setShowCustomForm] = useState(false);
  const [name, setName] = useState("");
  const [command, setCommand] = useState("");
  const [argumentsText, setArgumentsText] = useState("[]");
  const [enabled, setEnabled] = useState(true);
  const [message, setMessage] = useState("");
  const reset = (clearMessage = true) => {
    setEditing(null); setShowCustomForm(false); setName(""); setCommand(""); setArgumentsText("[]"); setEnabled(true);
    if (clearMessage) setMessage("");
  };
  const edit = (server: McpServer) => {
    setEditing(server); setShowCustomForm(true); setName(server.name); setCommand(server.command);
    setArgumentsText(JSON.stringify(server.arguments, null, 2)); setEnabled(server.enabled); setMessage("");
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      const argumentsList: unknown = JSON.parse(argumentsText);
      if (!Array.isArray(argumentsList) || !argumentsList.every((item) => typeof item === "string"))
        throw new Error("启动参数必须是字符串 JSON 数组。");
      const payload = { name, command, arguments: argumentsList, enabled };
      const server = await api<McpServer>(editing ? `/api/mcp-servers/${editing.id}` : "/api/mcp-servers", {
        method: editing ? "PUT" : "POST", body: JSON.stringify(payload),
      });
      setMessage(`已发现 ${server.tools.length} 个工具。`);
      onChanged();
      reset(false);
    } catch (error) {
      setMessage(String(error).replace(/^Error: /, ""));
    }
  };
  const diagnose = async (server: McpServer) => {
    try {
      const result = await api<{ ok: boolean; message: string; tools: McpTool[] }>(`/api/mcp-servers/${server.id}/diagnose`, { method: "POST" });
      setMessage(result.ok ? `${server.name}：${result.message}，发现 ${result.tools.length} 个工具。` : `${server.name}：${result.message}`);
      onChanged();
    } catch (error) { setMessage(String(error).replace(/^Error: /, "")); }
  };
  const remove = async (server: McpServer) => {
    if (!window.confirm(`删除 MCP Server“${server.name}”？所有任务将不再使用它。`)) return;
    try { await api<void>(`/api/mcp-servers/${server.id}`, { method: "DELETE" }); onChanged(); if (editing?.id === server.id) reset(); }
    catch (error) { setMessage(String(error).replace(/^Error: /, "")); }
  };
  return (
    <div className="modal-back">
      <section className="modal mcp-modal">
        <div className="modal-heading"><h2>本地 MCP Server</h2><button title="关闭" onClick={onClose}><X size={17} /></button></div>
        <p className="modal-intro">已启用 Server 的工具会提供给所有任务；调用仍受每个任务的阶段和 Agent 权限限制。命令和参数不会经由 shell 解析。</p>
        <div className="provider-list">
          <h3 className="mcp-section-heading">已有 MCP 服务器</h3>
          {servers.length ? servers.map((server) => (
            <div className="provider-row mcp-server-row" key={server.id}>
              <div><strong>{server.name}</strong><small>{server.command} · {server.tools.length} 个工具 · {server.enabled ? "已启用" : "已禁用"}</small><small>{server.tools.map((tool) => `${tool.name}（${mcpAccessLabels[tool.access_mode ?? "read-only"]}）`).join("、")}</small></div>
              <div className="mcp-row-actions"><button type="button" onClick={() => void diagnose(server)}>诊断</button><button type="button" onClick={() => edit(server)}>编辑</button><button type="button" className="danger" onClick={() => void remove(server)}>删除</button></div>
            </div>
          )) : <p>尚未配置 MCP Server。</p>}
        </div>
        <section className="mcp-create-section" aria-labelledby="create-mcp-server">
          <h3 id="create-mcp-server" className="mcp-section-heading">创建外部 MCP 服务</h3>
          <div className="mcp-create-actions">
            <button type="button" className="primary" onClick={() => { reset(); setShowCustomForm(true); }}><Plus size={16} /> 配置 MCP 服务</button>
          </div>
        </section>
        {showCustomForm && <form onSubmit={submit} className="provider-form mcp-custom-form">
          <h3>{editing ? `编辑 ${editing.name}` : "自主配置服务器"}</h3>
          <label>名称<input required value={name} onChange={(event) => setName(event.target.value)} /></label>
          <label>启动命令<input required placeholder="npx 或 python" value={command} onChange={(event) => setCommand(event.target.value)} /></label>
          <label>启动参数（JSON 字符串数组）<textarea required value={argumentsText} onChange={(event) => setArgumentsText(event.target.value)} /></label>
          <label className="checkbox-label"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />启用此 Server</label>
          <div className="modal-actions"><button type="button" onClick={() => reset()}>取消</button><button className="primary">保存并发现工具</button></div>
        </form>}
        {message && <p className={message.includes("失败") || message.includes("Error") ? "error" : "form-message"}>{message}</p>}
      </section>
    </div>
  );
}

function TaskConfigModal({
  task,
  onClose,
  onUpdated,
}: {
  task: Task;
  onClose: () => void;
  onUpdated: (task: Task) => void;
}) {
  const [title, setTitle] = useState(task.title);
  const [permissionMode, setPermissionMode] = useState<PermissionMode>(task.permission_mode);
  const [executionMode, setExecutionMode] = useState<ExecutionMode>(task.execution_mode);
  const [error, setError] = useState("");
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      onUpdated(await api<Task>(`/api/tasks/${task.id}`, {
        method: "PATCH",
        body: JSON.stringify({ title, permission_mode: permissionMode, execution_mode: executionMode }),
      }));
    } catch (reason) {
      setError(String(reason).replace(/^Error: /, ""));
    }
  };
  return (
    <div className="modal-back">
      <form className="modal task-config-modal" onSubmit={submit}>
        <div className="modal-heading">
          <h2>任务配置</h2>
          <button type="button" title="关闭" onClick={onClose}><X size={17} /></button>
        </div>
        <label>
          任务标题
          <input required value={title} onChange={(event) => setTitle(event.target.value)} />
        </label>
        <label>
          Agent 权限
          <select value={permissionMode} onChange={(event) => setPermissionMode(event.target.value as PermissionMode)}>
            {(Object.keys(permissionLabels) as PermissionMode[]).map((mode) => (
              <option key={mode} value={mode}>{permissionLabels[mode]}</option>
            ))}
          </select>
        </label>
        <label>
          默认执行模式
          <select disabled={task.execution_mode_locked} value={executionMode} onChange={(event) => setExecutionMode(event.target.value as ExecutionMode)}>
            {(Object.keys(executionModeLabels) as ExecutionMode[]).map((mode) => (
              <option key={mode} value={mode}>{executionModeLabels[mode]}</option>
            ))}
          </select>
        </label>
        {task.execution_mode_locked && <p className="form-message">编码已开始，执行模式已锁定。</p>}
        {error && <p className="error">{error}</p>}
        <div className="modal-actions">
          <button type="button" onClick={onClose}>取消</button>
          <button className="primary">保存配置</button>
        </div>
      </form>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
