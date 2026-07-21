# Local Agent Workbench

面向单用户的本地多智能体开发工作台。它运行在 `127.0.0.1`，使用 SQLite 保存任务数据，并可将本地 Git 仓库绑定为任务工作区，或把 GitHub 仓库克隆到指定的本地目录。

## 功能

- 创建并持续跟进开发任务对话。
- 管理本地仓库或 GitHub 仓库的任务工作区。
- 支持需求分析、方案设计、实现、审查和测试规划等协作阶段。
- 提供模型服务配置入口；vLLM 需先部署远程服务后才能使用。

## 技术栈

- 后端：FastAPI、SQLAlchemy、SQLite、HTTPX。
- 前端：React、TypeScript、Vite。
- 测试：pytest。

## 环境要求

- Python 3.10 或更高版本。
- Node.js 18 或更高版本，含 npm。
- Git（创建或克隆任务工作区时需要）。

## 本地启动

在项目根目录执行：

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
npm --prefix frontend install
npm --prefix frontend run build
python -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8787
```

打开 [http://127.0.0.1:8787](http://127.0.0.1:8787)，选择“Create task”，再选择本地 Git 目录，或填写 GitHub 地址及本地克隆目标目录。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests -q
npm --prefix frontend run build
```

## 提交范围

仓库只提交可复现项目所需的源码、测试、依赖清单与锁文件、配置、README 及变更记录。`.gitignore` 会排除本地运行数据和密钥（`data/`、`.env*`）、Python 虚拟环境与缓存、前端依赖和构建产物，以及编辑器和系统生成文件。

## 文件修改与 MCP

当前版本通过 OpenAI 兼容接口的受控本地函数工具访问工作区：始终可用文件列表和文件读取；只有任务处于“编码实现”或“修复”阶段，且任务权限设置为“工作区读写”或“完全访问”时，才向模型提供 `write_file`。因此，在需求分析、设计、待编码确认、验收或只读权限下，模型不能修改文档或代码。

当前版本还支持作为 MCP 客户端连接本机 `stdio` MCP Server。在右上角“MCP Server”中登记启动命令和 JSON 参数数组后，应用会启动子进程并发现其工具。已启用的 MCP Server 为全局配置，所有任务复用其工具，模型会在需要时自动调用，无需逐任务勾选。未知第三方工具默认按只读处理；MCP 工具仍受本应用的阶段和任务权限控制。

MCP Server 的密钥应由启动本应用的本机环境变量提供，页面不会存储密钥。第一版仅支持本地 `stdio`，不连接远程 HTTP MCP Server。

### 预制编码 MCP Server

在 `MCP Server` 窗口点击“添加预制编码 MCP Server”，应用会自动登记一个随应用提供的本地 Server。它使用当前任务的工作区，提供文件列表、文件读取和文件写入工具，并可被所有任务自动使用。

写入工具仍不会绕过任务安全限制：仅当任务处于“编码实现”或“修复”阶段，且 Agent 权限为“工作区读写”或“完全访问”时可用。预制 Server 只接受相对于当前任务工作区的路径，并拒绝路径穿越、绝对路径和 `.git`、`node_modules`、`.venv` 等目录。

不要提交 `data/model-secrets.json`、SQLite 数据库、任务工作区或任何真实凭据。
