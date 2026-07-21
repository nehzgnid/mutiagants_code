import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
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
type ExecutionMode = "confirm_before_coding" | "automatic";
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
  write_enabled: boolean;
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
type Run = {
  id: string;
  title?: string;
  content: string;
  activities: ActivityItem[];
  files: ChangedFile[];
  complete: boolean;
  error?: string;
  retryContent?: string;
};
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
const api = async <T,>(url: string, init?: RequestInit): Promise<T> => {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok)
    throw new Error((await response.text()) || response.statusText);
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
    [showExecutionModes, setShowExecutionModes] = useState(false);
  const composerRef = useRef<HTMLTextAreaElement>(null);
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
    const [task, taskMessages, stages, context] = await Promise.all([
      api<Task>(`/api/tasks/${taskId}`),
      api<TaskMessage[]>(`/api/tasks/${taskId}/messages`),
      api<StageRun[]>(`/api/tasks/${taskId}/stages`),
      api<ContextUsage>(`/api/tasks/${taskId}/context`),
    ]);
    setSelected(task);
    setTasks((items) =>
      items.map((item) => (item.id === task.id ? task : item)),
    );
    setMessages(taskMessages);
    setStageRuns(stages);
    setContextUsage(context);
  };
  useEffect(() => {
    if (!selected) {
      setMessages([]);
      setStageRuns([]);
      setRuns([]);
      setContextUsage(null);
      return;
    }
    setRuns([]);
    Promise.all([
      api<TaskMessage[]>(`/api/tasks/${selected.id}/messages`),
      api<StageRun[]>(`/api/tasks/${selected.id}/stages`),
      api<ContextUsage>(`/api/tasks/${selected.id}/context`),
    ])
      .then(([items, stages, context]) => {
        setMessages(items);
        setStageRuns(stages);
        setContextUsage(context);
      })
      .catch((error) => setNotice(String(error).replace(/^Error: /, "")));
  }, [selected?.id]);
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
  const compressContext = async () => {
    if (!selected || sending || compressing) return;
    const taskId = selected.id;
    const runId = `context-${Date.now()}`;
    setRuns((items) => [
      ...items,
      {
        id: runId,
        title: "正在压缩上下文",
        content: "",
        activities: [
          { kind: "agent", title: "上下文压缩", detail: "正在准备压缩" },
        ],
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
      {
        id: `pending-${Date.now()}`,
        role: "user",
        content,
        created_at: new Date().toISOString(),
      },
    ]);
    setRuns((items) => [
      ...items,
      {
        id: runId,
        content: "",
        activities: [{ kind: "agent", title: "主 Agent 路由", detail: "正在判定任务复杂度和协作流程" }],
        files: [],
        complete: false,
      },
    ]);
    setDraft("");
    setSending(true);
    try {
      const response = await fetch(`/api/tasks/${taskId}/messages/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
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
            }));
          if (eventName === "token")
            updateRun(runId, (run) => ({
              ...run,
              content: run.content + payload.content,
            }));
          if (eventName === "file")
            updateRun(runId, (run) => ({
              ...run,
              files: [...run.files, payload],
            }));
          if (eventName === "done")
            updateRun(runId, (run) => ({
              ...run,
              content: payload.message.content,
              complete: true,
            }));
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
      // The persisted assistant reply now represents this run in chronological history.
      setRuns((items) => items.filter((run) => run.id !== runId));
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
      </header>
      {notice && (
        <div className="notice">
          {notice}
          <button title="关闭" onClick={() => setNotice("")}>
            <X size={15} />
          </button>
        </div>
      )}
      <section className="layout">
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
              <button className="task" onClick={() => setSelected(task)}>
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
                <span
                  className={`write-status ${selected.write_enabled ? "enabled" : "disabled"}`}
                  title={selected.write_enabled ? "当前阶段允许修改任务工作区文件" : "文件写入会在编码实现或修复阶段启用；请先选择工作区读写权限并完成编码确认"}
                >
                  {selected.write_enabled ? "可修改文件" : "只读（待编码确认）"}
                </span>
              </div>
              <div className="message-list">
                {messages.length === 0 && (
                  <div className="empty-chat">
                    <Activity size={34} />
                    <h2>开始这个任务</h2>
                    <p>描述你想完成的工作，Agent 会保留对话上下文。</p>
                  </div>
                )}
                {messages.map((message) => {
                  const stageRun = message.role === "assistant"
                    ? stageRuns.find((run) => run.output === message.content)
                    : undefined;
                  return (
                    <article
                      className={`message ${message.role}`}
                      key={message.id}
                    >
                      <div className="message-role">
                        {message.role === "user" ? "你" : "Agent"}
                      </div>
                      {stageRun && <StageRunTrace run={stageRun} task={selected} />}
                      {message.role === "assistant" ? (
                        <MarkdownContent content={message.content} taskId={selected.id} />
                      ) : (
                        <p>{message.content}</p>
                      )}
                    </article>
                  );
                })}
                {runs.map((run) => (
                  <RunOutput
                    key={run.id}
                    run={run}
                    taskId={selected.id}
                    onRetry={(content) => void sendMessage(undefined, content)}
                  />
                ))}
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
                  <div className="execution-mode-picker">
                    <button
                      type="button"
                      className="execution-mode-button"
                      disabled={selected.execution_mode_locked}
                      onClick={() => setShowExecutionModes((open) => !open)}
                      title={selected.execution_mode_locked ? "编码已开始，执行模式已锁定" : "选择当前任务的执行模式"}
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
                              <small>{mode === "confirm_before_coding" ? "完成分析与设计后，编码前等待确认" : "完成设计后连续进入编码、审核和测试"}</small>
                            </span>
                            {selected.execution_mode === mode && <Check size={15} />}
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

function workflowStageState(stage: string, task: Task, plannedStages: string[]) {
  const currentIndex = plannedStages.indexOf(task.current_stage);
  const stageIndex = plannedStages.indexOf(stage);
  if (task.current_stage === "已完成") return "已完成";
  if (task.current_stage === "待编码确认" && stage === "编码实现") return "等待编码确认";
  if (currentIndex >= 0 && stageIndex < currentIndex) return "已完成";
  if (currentIndex >= 0 && stageIndex === currentIndex)
    return task.status === "awaiting_input" ? "等待继续" : "进行中";
  return "待执行";
}

function StageRunTrace({ run, task }: { run: StageRun; task: Task }) {
  const decision = task.routing_decision;
  const plannedStages = decision?.required_stages ?? [run.stage];
  return (
    <details className="activity-trace completed-trace">
      <summary>
        <Activity size={15} /> 工作过程
      </summary>
      <div>
        {decision && (
          <div className="workflow-plan">
            <p className="workflow-plan-title">主 Agent 协作计划</p>
            <p className="workflow-plan-reason">{decision.complexity_reason}</p>
            <ol className="workflow-stages">
              <li className="workflow-step completed">
                <CircleDot size={14} />
                <span><strong>主 Agent · 协作规划</strong>已完成</span>
              </li>
              {plannedStages.map((stage) => {
                const state = workflowStageState(stage, task, plannedStages);
                return (
                  <li className={`workflow-step ${state === "已完成" ? "completed" : state === "待执行" ? "pending" : "current"}`} key={stage}>
                    <CircleDot size={14} />
                    <span><strong>{stageAgents[stage] ?? "Agent"} · {stage}</strong>{state}</span>
                  </li>
                );
              })}
            </ol>
          </div>
        )}
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

function RunOutput({ run, taskId, onRetry }: { run: Run; taskId: string; onRetry: (content: string) => void }) {
  const icon = (kind: string) =>
    kind === "network" ? (
      <Globe2 size={14} />
    ) : kind === "tool" ? (
      <TerminalSquare size={14} />
    ) : (
      <CircleDot size={14} />
    );
  return (
    <article className="message assistant streamed">
      <details className="activity-trace" open={!run.complete}>
        <summary>
          <Activity size={15} /> {run.complete ? (run.title ?? "工作过程") : (run.title ?? "Agent 正在工作")}
        </summary>
        <div>
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
      ) : (
        run.content && <MarkdownContent content={run.content} taskId={taskId} />
      )}
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
  const [name, setName] = useState("");
  const [command, setCommand] = useState("");
  const [argumentsText, setArgumentsText] = useState("[]");
  const [enabled, setEnabled] = useState(true);
  const [message, setMessage] = useState("");
  const reset = () => {
    setEditing(null); setName(""); setCommand(""); setArgumentsText("[]"); setEnabled(true); setMessage("");
  };
  const edit = (server: McpServer) => {
    setEditing(server); setName(server.name); setCommand(server.command);
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
      if (!editing) reset();
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
  const addCodingPreset = async () => {
    try {
      const result = await api<{ created: boolean; server: McpServer }>("/api/mcp-servers/presets/coding", { method: "POST" });
      setMessage(result.created ? `已添加 ${result.server.name}，所有任务现在都可自动使用其工具。` : `${result.server.name} 已存在，所有任务均可使用。`);
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
        <div className="modal-actions"><button type="button" className="primary" onClick={() => void addCodingPreset()}>添加预制编码 MCP Server</button></div>
        <div className="provider-list">
          {servers.length ? servers.map((server) => (
            <div className="provider-row mcp-server-row" key={server.id}>
              <div><strong>{server.name}</strong><small>{server.command} · {server.tools.length} 个工具 · {server.enabled ? "已启用" : "已禁用"}</small><small>{server.tools.map((tool) => `${tool.name}（${mcpAccessLabels[tool.access_mode ?? "read-only"]}）`).join("、")}</small></div>
              <div className="mcp-row-actions"><button type="button" onClick={() => void diagnose(server)}>诊断</button><button type="button" onClick={() => edit(server)}>编辑</button><button type="button" className="danger" onClick={() => void remove(server)}>删除</button></div>
            </div>
          )) : <p>尚未配置 MCP Server。</p>}
        </div>
        <form onSubmit={submit} className="provider-form">
          <h3>{editing ? `编辑 ${editing.name}` : "添加本地 stdio Server"}</h3>
          <label>名称<input required value={name} onChange={(event) => setName(event.target.value)} /></label>
          <label>启动命令<input required placeholder="npx 或 python" value={command} onChange={(event) => setCommand(event.target.value)} /></label>
          <label>启动参数（JSON 字符串数组）<textarea required value={argumentsText} onChange={(event) => setArgumentsText(event.target.value)} /></label>
          <label className="checkbox-label"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />启用此 Server</label>
          {message && <p className={message.includes("失败") || message.includes("Error") ? "error" : "form-message"}>{message}</p>}
          <div className="modal-actions">{editing && <button type="button" onClick={reset}>新增 Server</button>}<button className="primary">保存并发现工具</button></div>
        </form>
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
