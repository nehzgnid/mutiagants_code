import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendDirectory = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const projectRoot = path.resolve(frontendDirectory, "..");
const backendDirectory = path.join(projectRoot, "backend");
const outputDirectory = path.join(projectRoot, "desktop-backend-dist");
const workDirectory = path.join(projectRoot, ".desktop-build", "pyinstaller");
const specDirectory = path.join(projectRoot, ".desktop-build");

fs.mkdirSync(outputDirectory, { recursive: true });
fs.mkdirSync(workDirectory, { recursive: true });
fs.mkdirSync(specDirectory, { recursive: true });

const virtualEnvironmentPython =
  process.platform === "win32"
    ? path.join(projectRoot, ".venv", "Scripts", "python.exe")
    : path.join(projectRoot, ".venv", "bin", "python");
const python = process.env.WORKBENCH_PYTHON ||
  (fs.existsSync(virtualEnvironmentPython) ? virtualEnvironmentPython : "python");

const args = [
  "-m",
  "PyInstaller",
  "--noconfirm",
  "--clean",
  "--onefile",
  "--name",
  "local-agent-backend",
  "--distpath",
  outputDirectory,
  "--workpath",
  workDirectory,
  "--specpath",
  specDirectory,
  "--paths",
  backendDirectory,
  path.join(backendDirectory, "desktop_entry.py"),
];
const result = spawnSync(python, args, { cwd: projectRoot, stdio: "inherit" });
if (result.error) {
  throw result.error;
}
if (result.status !== 0) {
  process.exit(result.status ?? 1);
}
