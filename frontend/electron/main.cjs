const { app, BrowserWindow, dialog } = require("electron");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

let backendProcess;
let frontendServer;
let backendLog;

function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = http.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close((error) => (error ? reject(error) : resolve(port)));
    });
  });
}

function pythonCommand(projectRoot) {
  if (process.env.WORKBENCH_PYTHON) {
    return { command: process.env.WORKBENCH_PYTHON, args: [] };
  }
  const candidates =
    process.platform === "win32"
      ? [path.join(projectRoot, ".venv", "Scripts", "python.exe")]
      : [path.join(projectRoot, ".venv", "bin", "python")];
  const executable = candidates.find((candidate) => fs.existsSync(candidate));
  return { command: executable || "python", args: [] };
}

function startBackend(port) {
  const dataDirectory = path.join(app.getPath("userData"), "data");
  fs.mkdirSync(dataDirectory, { recursive: true });
  backendLog = fs.createWriteStream(path.join(app.getPath("userData"), "backend.log"), {
    flags: "a",
  });

  let command;
  let args;
  let workingDirectory;
  if (app.isPackaged) {
    const executable = process.platform === "win32" ? "local-agent-backend.exe" : "local-agent-backend";
    command = path.join(process.resourcesPath, "backend", executable);
    args = [];
    workingDirectory = path.dirname(command);
  } else {
    const projectRoot = path.resolve(__dirname, "..", "..");
    const python = pythonCommand(projectRoot);
    command = python.command;
    args = [...python.args, path.join(projectRoot, "backend", "desktop_entry.py")];
    workingDirectory = projectRoot;
  }

  backendProcess = spawn(command, args, {
    cwd: workingDirectory,
    windowsHide: true,
    detached: process.platform !== "win32",
    env: {
      ...process.env,
      WORKBENCH_HOST: "127.0.0.1",
      WORKBENCH_PORT: String(port),
      WORKBENCH_DATA_DIR: dataDirectory,
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  backendProcess.stdout.pipe(backendLog);
  backendProcess.stderr.pipe(backendLog);
  backendProcess.once("error", (error) => backendLog.write(`\nDesktop launch error: ${error.stack || error}\n`));
}

async function waitForBackend(port) {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    if (backendProcess && backendProcess.exitCode !== null) {
      throw new Error(`本地后端提前退出，退出码 ${backendProcess.exitCode}`);
    }
    try {
      const healthy = await new Promise((resolve) => {
        const request = http.get(
          { hostname: "127.0.0.1", port, path: "/api/health", timeout: 1_000 },
          (response) => {
            response.resume();
            resolve(response.statusCode === 200);
          },
        );
        request.once("timeout", () => request.destroy());
        request.once("error", () => resolve(false));
      });
      if (healthy) return;
    } catch {
      // The service is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("本地后端在 30 秒内没有启动成功");
}

function proxyApi(request, response, backendPort) {
  const headers = { ...request.headers, host: `127.0.0.1:${backendPort}` };
  const upstream = http.request(
    {
      hostname: "127.0.0.1",
      port: backendPort,
      path: request.url,
      method: request.method,
      headers,
    },
    (upstreamResponse) => {
      response.writeHead(upstreamResponse.statusCode || 502, upstreamResponse.headers);
      upstreamResponse.pipe(response);
    },
  );
  upstream.once("error", (error) => {
    if (!response.headersSent) {
      response.writeHead(502, { "Content-Type": "application/json; charset=utf-8" });
    }
    response.end(JSON.stringify({ detail: `本地后端连接失败：${error.message}` }));
  });
  request.pipe(upstream);
}

const contentTypes = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".ico": "image/x-icon",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
};

function startFrontendServer(backendPort) {
  const distDirectory = app.isPackaged
    ? path.join(app.getAppPath(), "dist")
    : path.resolve(__dirname, "..", "dist");
  frontendServer = http.createServer((request, response) => {
    if (request.url === "/api" || request.url.startsWith("/api/")) {
      proxyApi(request, response, backendPort);
      return;
    }

    let pathname;
    try {
      pathname = decodeURIComponent(new URL(request.url, "http://127.0.0.1").pathname);
    } catch {
      response.writeHead(400);
      response.end("Bad request");
      return;
    }
    const requested = pathname === "/" ? "index.html" : pathname.replace(/^\/+/, "");
    let filePath = path.resolve(distDirectory, requested);
    if (!filePath.startsWith(`${path.resolve(distDirectory)}${path.sep}`) || !fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
      filePath = path.join(distDirectory, "index.html");
    }
    fs.readFile(filePath, (error, data) => {
      if (error) {
        response.writeHead(500, { "Content-Type": "text/plain; charset=utf-8" });
        response.end("无法读取桌面界面文件");
        return;
      }
      response.writeHead(200, {
        "Content-Type": contentTypes[path.extname(filePath).toLowerCase()] || "application/octet-stream",
        "Cache-Control": "no-cache",
      });
      response.end(data);
    });
  });
  return new Promise((resolve, reject) => {
    frontendServer.once("error", reject);
    frontendServer.listen(0, "127.0.0.1", () => resolve(frontendServer.address().port));
  });
}

async function createWindow() {
  const smokeTest = process.env.WORKBENCH_SMOKE_TEST === "1";
  const backendPort = await getFreePort();
  startBackend(backendPort);
  await waitForBackend(backendPort);
  const frontendPort = await startFrontendServer(backendPort);

  const window = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1024,
    minHeight: 700,
    show: false,
    backgroundColor: "#f7f8f7",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  window.removeMenu();
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  if (!smokeTest) {
    window.once("ready-to-show", () => window.show());
  }
  await window.loadURL(`http://127.0.0.1:${frontendPort}`);
  if (smokeTest) {
    const result = await window.webContents.executeJavaScript(
      "fetch('/api/health').then((response) => response.json())",
    );
    console.log(`Desktop smoke test passed: ${JSON.stringify(result)}`);
    app.quit();
  }
}

function stopServices() {
  if (frontendServer) {
    frontendServer.close();
    frontendServer = undefined;
  }
  if (backendProcess && backendProcess.exitCode === null) {
    if (process.platform === "win32") {
      spawnSync("taskkill", ["/pid", String(backendProcess.pid), "/t", "/f"], {
        windowsHide: true,
        stdio: "ignore",
      });
    } else {
      try {
        process.kill(-backendProcess.pid, "SIGTERM");
      } catch {
        backendProcess.kill();
      }
    }
  }
  backendProcess = undefined;
  if (backendLog) {
    backendLog.end();
    backendLog = undefined;
  }
}

app.whenReady().then(async () => {
  try {
    await createWindow();
  } catch (error) {
    const logPath = path.join(app.getPath("userData"), "backend.log");
    dialog.showErrorBox("Local Agent Workbench 启动失败", `${error.message}\n\n日志：${logPath}`);
    app.quit();
  }
});

app.on("window-all-closed", () => app.quit());
app.on("before-quit", stopServices);
