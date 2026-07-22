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

当前版本通过 OpenAI 兼容接口的受控本地函数工具访问工作区：始终可用文件列表和文件读取；只有任务处于“编码实现”或“修复”阶段，且任务权限设置为“工作区读写”或“完全访问”时，才向模型提供 `apply_patch`。补丁以文件内容哈希为基线，自动编码模式会直接原子应用，手动确认编码模式会先展示 diff 并等待确认；后续人工修改会使补丁或撤销进入冲突状态，而不会被覆盖。

命令执行仅在“完全访问”权限下可用，输出会作为任务操作记录持续显示，可取消并保留退出码。工作区外的目录必须通过任务授权接口显式授予访问权限。执行记录还提供任务 Git 状态、diff 以及通过工作区测试命令后的确认提交；提交只暂存该任务已完成补丁涉及的路径。

当前版本还支持作为 MCP 客户端连接本机 `stdio` MCP Server。在右上角“MCP Server”中登记启动命令和 JSON 参数数组后，应用会启动子进程并发现其工具。已启用的 MCP Server 为全局配置，所有任务复用其工具，模型会在需要时自动调用，无需逐任务勾选。未知第三方工具默认按只读处理；MCP 工具仍受本应用的阶段和任务权限控制。

MCP Server 的密钥应由启动本应用的本机环境变量提供，页面不会存储密钥。第一版仅支持本地 `stdio`，不连接远程 HTTP MCP Server。

不要提交 `data/model-secrets.json`、SQLite 数据库、任务工作区或任何真实凭据。
