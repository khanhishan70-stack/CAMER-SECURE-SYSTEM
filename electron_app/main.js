const { app, BrowserWindow, dialog } = require("electron");
const path = require("path");
const { spawn, execSync } = require("child_process");
const http = require("http");
const fs = require("fs");

const PORT = 8844;
const HOST = "127.0.0.1";
const URL = `http://${HOST}:${PORT}`;

let mainWindow = null;
let pythonProcess = null;

const homeDir = require("os").homedir();
const logFile = path.join(homeDir, "securecam-debug.log");

function log(msg) {
  try { fs.appendFileSync(logFile, `${new Date().toISOString()} ${msg}\n`); } catch (_) {}
}

function getBackendDir() {
  if (app.isPackaged) {
    return process.resourcesPath;
  }
  return path.resolve(__dirname, "..");
}

function findPython() {
  const candidates = ["python", "python3", "py", "python.exe"];
  // Known-good interpreter paths are probed first - the machine may also have
  // a fresh Python (e.g. 3.14) that looks fine but lacks our libraries.
  const knownGood = [
    "C:\\Program Files\\Python312\\python.exe",
    "C:\\Python312\\python.exe",
    "C:\\Program Files\\Python311\\python.exe",
    "C:\\Program Files\\Python310\\python.exe",
  ];
  const badPaths = ["WindowsApps", "Microsoft\\WindowsApps"];
  const pyInits = [];
  for (const p of knownGood) {
    try {
      if (fs.existsSync(p)) pyInits.push(p);
    } catch (_) {}
  }
  for (const cmd of candidates) {
    try {
      const out = execSync(`where.exe ${cmd} 2>nul`, {
        timeout: 5000,
        shell: "cmd.exe",
    windowsHide: false,
      });
      const paths = out.toString().trim().split("\r\n");
      for (const p of paths) {
        if (p && !badPaths.some(b => p.includes(b))) {
          if (!pyInits.includes(p)) {
            pyInits.push(p);
            log(`Python candidate: ${p}`);
          }
          break;
        }
      }
    } catch (_) {}
  }
  // Try each found Python and keep the first one that actually has the
  // libraries SecureCam needs. Importing these can be slow (30s+) on a cold
  // or busy machine, so the probe gets a generous timeout - and a timeout is
  // NOT treated as "missing" (only a real nonzero exit code is).
  for (const p of pyInits) {
    try {
      execSync(
        `"${p}" -c "import fastapi,uvicorn,cv2,vosk,sounddevice,numpy,onnxruntime,insightface"`,
        { timeout: 120000, shell: "cmd.exe", windowsHide: false, stdio: ["ignore", "pipe", "pipe"] }
      );
      log(`Python OK with deps: ${p}`);
      return p;
    } catch (e) {
      // Exit code 1 = the interpreter genuinely lacks a module. Any other
      // failure (timeout/killed) means we couldn't verify - assume it is fine.
      if (e && e.status === 1) {
        log(`Python missing deps: ${p}`);
      } else {
        log(`Python probe inconclusive (${e && e.status}), assuming OK: ${p}`);
        return p;
      }
    }
  }
  log("No Python with deps found - falling back to first candidate");
  return pyInits[0] || "python.exe";
}

function startPythonServer(backendDir, pythonPath) {
  const runScript = path.join(backendDir, "run.py");
  log(`Spawning: ${pythonPath} ${runScript} in ${backendDir}`);

  pythonProcess = spawn(pythonPath, [runScript], {
    cwd: backendDir,
    stdio: ["ignore", "pipe", "pipe"],
    shell: false,
    windowsHide: false,
    env: {
      ...process.env,
      PYTHONUNBUFFERED: "1",
      PYTHONDONTWRITEBYTECODE: "1",
    },
  });

  pythonProcess.stdout.on("data", (d) => log(`[py] ${d.toString().trim()}`));
  pythonProcess.stderr.on("data", (d) => log(`[py] ${d.toString().trim()}`));
  pythonProcess.on("error", (err) => log(`spawn error: ${err.code} ${err.message}`));
  pythonProcess.on("exit", (code, sig) => {
    log(`python exit code=${code} signal=${sig}`);
    pythonProcess = null;
  });
}

function waitForServer(retries = 50, delay = 1500) {
  return new Promise((resolve, reject) => {
    const check = (n) => {
      if (n >= retries) return reject(new Error("Server timeout"));
      const req = http.get(`${URL}/api/status`, (res) => {
        if (res.statusCode === 200) return resolve();
        setTimeout(() => check(n + 1), delay);
      });
      req.on("error", () => setTimeout(() => check(n + 1), delay));
      req.setTimeout(3000, () => { req.destroy(); setTimeout(() => check(n + 1), delay); });
    };
    check(0);
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400, height: 900,
    minWidth: 1100, minHeight: 700,
    title: "SecureCam",
    webPreferences: { nodeIntegration: false, contextIsolation: true },
    show: false,
  });
  mainWindow.loadURL(URL);
  mainWindow.once("ready-to-show", () => mainWindow.show());
  mainWindow.on("closed", () => { mainWindow = null; });
}

app.whenReady().then(async () => {
  log("=== SecureCam start ===");
  const backendDir = getBackendDir();
  log(`Packaged: ${app.isPackaged}, backend=${backendDir}`);
  
  if (app.isPackaged) {
    log(`Resources exists: ${fs.existsSync(path.join(backendDir, "run.py"))}`);
    log(`App exists: ${fs.existsSync(path.join(backendDir, "app"))}`);
    log(`Dir listing: ${fs.readdirSync(backendDir).join(", ")}`);
  }

  const pythonPath = findPython();
  startPythonServer(backendDir, pythonPath);

  try {
    await waitForServer();
    log("Server ready!");
    createWindow();
  } catch (err) {
    log(`FAILED: ${err.message}`);
    dialog.showErrorBox("SecureCam Error",
      `Failed to start Python server.\n\nCheck: ${logFile}`);
    app.quit();
  }
});

function shutdownPython() {
  if (!pythonProcess) return;
  try {
    const req = http.request(`${URL}/api/shutdown`, { method: "POST", timeout: 3000 });
    req.on("error", () => {});
    req.end();
  } catch (_) {}
  setTimeout(() => {
    if (pythonProcess) { pythonProcess.kill(); pythonProcess = null; }
  }, 4000);
}

app.on("window-all-closed", () => {
  shutdownPython();
});
app.on("before-quit", () => {
  shutdownPython();
});
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) { mainWindow.show(); mainWindow.focus(); }
  });
}
