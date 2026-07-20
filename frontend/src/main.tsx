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
  Send,
  Settings2,
  Shield,
  TerminalSquare,
  X,
} from "lucide-react";
import { createRoot } from "react-dom/client";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "./styles.css";

type PermissionMode = "read-only" | "workspace-write" | "full-access";
type TaskMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
};
type Task = {
  id: string;
  title: string;
  permission_mode: PermissionMode;
  status: string;
  current_stage: string;
  workflow_type: string;
  assigned_agent: string;
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
};
type StageRun = {
  id: string;
  stage: string;
  agent: string;
  status: string;
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

function App() {
  const [tasks, setTasks] = useState<Task[]>([]),
    [selected, setSelected] = useState<Task | null>(null),
    [providers, setProviders] = useState<Provider[]>([]),
    [messages, setMessages] = useState<TaskMessage[]>([]),
    [stageRuns, setStageRuns] = useState<StageRun[]>([]),
    [contextUsage, setContextUsage] = useState<ContextUsage | null>(null),
    [runs, setRuns] = useState<Run[]>([]),
    [draft, setDraft] = useState(""),
    [sending, setSending] = useState(false),
    [compressing, setCompressing] = useState(false),
    [notice, setNotice] = useState(""),
    [showTask, setShowTask] = useState(false),
    [showProviders, setShowProviders] = useState(false),
    [showModels, setShowModels] = useState(false),
    [showPermissions, setShowPermissions] = useState(false);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const activeProvider =
    providers.find((provider) => provider.is_active) ?? null;
  const load = async () => {
    try {
      const [taskList, providerList] = await Promise.all([
        api<Task[]>("/api/tasks"),
        api<Provider[]>("/api/model-providers"),
      ]);
      setTasks(taskList);
      setProviders(providerList);
    } catch (error) {
      setNotice(String(error).replace(/^Error: /, ""));
    }
  };
  useEffect(() => {
    void load();
  }, []);
  const refreshTaskWorkflow = async (taskId: string) => {
    const [task, stages, context] = await Promise.all([
      api<Task>(`/api/tasks/${taskId}`),
      api<StageRun[]>(`/api/tasks/${taskId}/stages`),
      api<ContextUsage>(`/api/tasks/${taskId}/context`),
    ]);
    setSelected(task);
    setTasks((items) =>
      items.map((item) => (item.id === task.id ? task : item)),
    );
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
  const sendMessage = async (event?: FormEvent) => {
    event?.preventDefault();
    const content = draft.trim();
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
      { id: runId, content: "", activities: [], files: [], complete: false },
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
              complete: true,
              error: payload.message,
            }));
        }
      }
      await refreshTaskWorkflow(taskId);
    } catch (error) {
      updateRun(runId, (run) => ({
        ...run,
        complete: true,
        error: String(error).replace(/^Error: /, ""),
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
            <button
              className={`task ${selected?.id === task.id ? "active" : ""}`}
              key={task.id}
              onClick={() => setSelected(task)}
            >
              <span>{task.title}</span>
            </button>
          ))}
        </aside>
        <section className="workbench">
          {selected ? (
            <div className="chat-shell">
              <div className="chat-title">
                <h1>{selected.title}</h1>
              </div>
              <div className="message-list">
                {messages.length === 0 && (
                  <div className="empty-chat">
                    <Activity size={34} />
                    <h2>开始这个任务</h2>
                    <p>描述你想完成的工作，Agent 会保留对话上下文。</p>
                  </div>
                )}
                {messages.map((message) => (
                  <article
                    className={`message ${message.role}`}
                    key={message.id}
                  >
                    {message.role === "assistant" && (
                      <div className="message-role">Agent</div>
                    )}
                    {message.role === "assistant" ? (
                      <MarkdownContent content={message.content} taskId={selected.id} />
                    ) : (
                      <p>{message.content}</p>
                    )}
                  </article>
                ))}
                {runs.map((run) => (
                  <RunOutput key={run.id} run={run} taskId={selected.id} />
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
      {showProviders && (
        <ProviderModal
          providers={providers}
          onClose={() => setShowProviders(false)}
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

function RunOutput({ run, taskId }: { run: Run; taskId: string }) {
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
        <p className="stream-error">{run.error}</p>
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
createRoot(document.getElementById("root")!).render(<App />);
