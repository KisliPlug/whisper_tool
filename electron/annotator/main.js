import { app, BrowserWindow, clipboard, ipcMain, Menu, nativeImage } from "electron";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const sessionDir = process.argv[2];
if (!sessionDir) {
  console.error("Missing annotator session directory.");
  app.exit(2);
}

app.setName("Whisper Screenshot Annotator");
Menu.setApplicationMenu(null);

const requestPath = path.join(sessionDir, "request.json");
const resultPath = path.join(sessionDir, "result.json");
const WINDOW_TITLE = "Whisper Screenshot Annotator";

async function createWindow() {
  const request = JSON.parse(await fs.readFile(requestPath, "utf8"));
  const win = new BrowserWindow({
    width: Math.min(1800, Math.max(1100, request.size.width + 260)),
    height: Math.min(1100, Math.max(760, request.size.height + 170)),
    minWidth: 980,
    minHeight: 680,
    title: WINDOW_TITLE,
    backgroundColor: "#101114",
    autoHideMenuBar: true,
    alwaysOnTop: true,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  win.setAlwaysOnTop(true);
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  win.on("page-title-updated", (event) => {
    event.preventDefault();
    win.setTitle(WINDOW_TITLE);
  });
  win.once("ready-to-show", () => {
    win.show();
    win.focus();
    win.setAlwaysOnTop(true);
  });

  if (process.env.ANNOTATOR_DEV_URL) {
    await win.loadURL(process.env.ANNOTATOR_DEV_URL);
  } else {
    await win.loadFile(path.join(__dirname, "../../dist/index.html"));
  }
}

ipcMain.handle("annotator:load", async () => {
  const request = JSON.parse(await fs.readFile(requestPath, "utf8"));
  return {
    ...request,
    sessionDir,
    imageUrl: pathToFileURL(request.imagePath).href,
    commitPath: path.join(sessionDir, "commit"),
    cancelPath: path.join(sessionDir, "cancel")
  };
});

ipcMain.handle("annotator:checkSignal", async () => {
  try {
    await fs.access(path.join(sessionDir, "commit"));
    return "commit";
  } catch {}
  try {
    await fs.access(path.join(sessionDir, "cancel"));
    return "cancel";
  } catch {}
  return null;
});

ipcMain.handle("annotator:save", async (_event, payload) => {
  const imageBuffer = Buffer.from(payload.imageBase64, "base64");
  let clipboardImage = false;
  if (payload.copyImageToClipboard) {
    clipboard.writeImage(nativeImage.createFromBuffer(imageBuffer));
    clipboardImage = true;
  }
  await fs.writeFile(path.join(sessionDir, "output.png"), imageBuffer);
  await fs.writeFile(
    path.join(sessionDir, "annotations.json"),
    JSON.stringify(payload.metadata, null, 2),
    "utf8"
  );
  await fs.writeFile(
    resultPath,
    JSON.stringify({ ok: true, clipboardImage }, null, 2),
    "utf8"
  );
  app.exit(0);
});

ipcMain.handle("annotator:cancel", async () => {
  await fs.writeFile(resultPath, JSON.stringify({ ok: false }, null, 2), "utf8");
  app.exit(0);
});

app.whenReady().then(createWindow);
app.on("window-all-closed", async () => {
  try {
    await fs.writeFile(resultPath, JSON.stringify({ ok: false }, null, 2), "utf8");
  } catch {}
  app.quit();
});
