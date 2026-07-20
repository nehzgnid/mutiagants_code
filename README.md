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

不要提交 `data/model-secrets.json`、SQLite 数据库、任务工作区或任何真实凭据。
