// Electron main process — runs the read-only adapter as a CHILD PROCESS (its own event
// loop, so heavy iCloud/graph reads never freeze the window) and loads the built UI.
// The adapter serves both /api and the static dist/ build, so the window is same-origin.
const { app, BrowserWindow, shell, utilityProcess } = require('electron')
const path = require('node:path')
const http = require('node:http')

const PORT = process.env.PORT || '4317'
const ROOT = path.join(__dirname, '..')
let serverProc = null

function startServer() {
  // utilityProcess = Electron's managed Node child: its own event loop, NOT subject to
  // App Nap throttling (unlike a plain ELECTRON_RUN_AS_NODE spawn), and uses Electron's
  // bundled Node so no separate Node install is required.
  serverProc = utilityProcess.fork(path.join(ROOT, 'server', 'index.mjs'), [], {
    cwd: ROOT,
    env: { ...process.env, PORT: String(PORT) },
    stdio: 'inherit',
  })
  serverProc.on('exit', (code) => console.log('[adapter] exited with', code))
}

function waitForHealth(tries = 80) {
  const url = `http://127.0.0.1:${PORT}/api/health`
  return new Promise((resolve) => {
    const tick = (n) => {
      const req = http.get(url, (res) => {
        if (res.statusCode === 200) { res.resume(); resolve(true) }
        else { res.resume(); n <= 0 ? resolve(false) : setTimeout(() => tick(n - 1), 150) }
      })
      req.on('error', () => (n <= 0 ? resolve(false) : setTimeout(() => tick(n - 1), 150)))
    }
    tick(tries)
  })
}

async function createWindow() {
  const win = new BrowserWindow({
    width: 1460,
    height: 920,
    minWidth: 1080,
    minHeight: 680,
    backgroundColor: '#080b10',
    titleBarStyle: 'hiddenInset',
    trafficLightPosition: { x: 14, y: 16 },
    show: false,
    webPreferences: { contextIsolation: true },
  })
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http')) shell.openExternal(url)
    return { action: 'deny' }
  })
  win.once('ready-to-show', () => win.show())
  await waitForHealth()
  await win.loadURL(`http://127.0.0.1:${PORT}/`)
}

app.whenReady().then(() => {
  startServer()
  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('quit', () => {
  if (serverProc) {
    try { serverProc.kill() } catch { /* ignore */ }
  }
})
