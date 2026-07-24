import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendDirectory = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const projectRoot = path.resolve(frontendDirectory, "..");
const executable = path.join(
  projectRoot,
  "desktop-backend-dist",
  process.platform === "win32" ? "local-agent-backend.exe" : "local-agent-backend",
);
if (!fs.existsSync(executable)) {
  throw new Error(`Bundled backend does not exist: ${executable}`);
}

const port = 18787;
const dataDirectory = path.join(projectRoot, ".desktop-build", "smoke-data");
fs.mkdirSync(dataDirectory, { recursive: true });
const child = spawn(executable, [], {
  windowsHide: true,
  detached: process.platform !== "win32",
  env: {
    ...process.env,
    WORKBENCH_DATA_DIR: dataDirectory,
    WORKBENCH_PORT: String(port),
  },
  stdio: "ignore",
});

try {
  const deadline = Date.now() + 20_000;
  let result;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`Bundled backend exited with code ${child.exitCode}`);
    }
    try {
      const response = await fetch(`http://127.0.0.1:${port}/api/health`);
      if (response.ok) {
        result = await response.json();
        break;
      }
    } catch {
      // The one-file executable is still unpacking and starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  if (!result) {
    throw new Error("Bundled backend did not become healthy within 20 seconds");
  }
  console.log(JSON.stringify(result));
} finally {
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/pid", String(child.pid), "/t", "/f"], {
      windowsHide: true,
      stdio: "ignore",
    });
  } else {
    try {
      process.kill(-child.pid, "SIGTERM");
    } catch {
      child.kill();
    }
  }
}
