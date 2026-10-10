import { app, BrowserWindow, screen, ipcMain, globalShortcut } from 'electron';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

import fs from 'fs';

// Set application identity explicitly
app.name = 'Handsfree Notch Lab';

// Configure a stable, dedicated, writable userData directory under local app data
const localAppData = process.env.LOCALAPPDATA || path.join(process.env.USERPROFILE || '', 'AppData', 'Local');
const stableUserDataPath = path.join(localAppData, 'Handsfree-Notch-Lab');

if (!fs.existsSync(stableUserDataPath)) {
  fs.mkdirSync(stableUserDataPath, { recursive: true });
}
app.setPath('userData', stableUserDataPath);

// Prevent multiple concurrent Electron instances from colliding and locking the disk/GPU caches
const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  app.exit(0);
}

let mainWindow: BrowserWindow | null = null;
let isContextSelecting = false;

// Standard compact pill dimensions in logical pixels
const NOTCH_WIDTH = 224;
const NOTCH_HEIGHT = 50;
const TOP_MARGIN = 14;

function calculateNotchBounds(width: number, height: number) {
  const primaryDisplay = screen.getPrimaryDisplay();
  const { workArea } = primaryDisplay;
  const x = Math.round(workArea.x + (workArea.width - width) / 2);
  const y = Math.round(workArea.y + TOP_MARGIN);
  return { x, y, width, height };
}

function createMainWindow() {
  const initialBounds = calculateNotchBounds(NOTCH_WIDTH, NOTCH_HEIGHT);

  mainWindow = new BrowserWindow({
    x: initialBounds.x,
    y: initialBounds.y,
    width: initialBounds.width,
    height: initialBounds.height,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    hasShadow: false,
    backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  // Keep window always above regular apps and floating docks
  mainWindow.setAlwaysOnTop(true, 'screen-saver');
  mainWindow.setVisibleOnAllWorkspaces?.(true, { visibleOnFullScreen: true });

  const isDev = process.env.NODE_ENV === 'development' || !app.isPackaged;

  if (isDev && process.env.VITE_DEV_SERVER_URL) {
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL);
  } else {
    // Look for built dist/index.html
    const indexPath = path.join(__dirname, '../dist/index.html');
    mainWindow.loadFile(indexPath).catch(() => {
      // Fallback if running directly from project root
      mainWindow?.loadFile(path.join(process.cwd(), 'dist/index.html'));
    });
  }

  // Handle display changes
  screen.on('display-metrics-changed', () => {
    if (mainWindow && !isContextSelecting) {
      const bounds = mainWindow.getBounds();
      const newPos = calculateNotchBounds(bounds.width, bounds.height);
      mainWindow.setBounds(newPos);
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// IPC Handlers
ipcMain.handle('notch:resize', (_event, { width, height, animated = true }) => {
  if (!mainWindow || isContextSelecting) return;

  const targetBounds = calculateNotchBounds(width, height);
  mainWindow.setBounds(targetBounds, animated);
});

ipcMain.handle('notch:set-ignore-mouse-events', (_event, { ignore, forward = false }) => {
  if (!mainWindow) return;
  mainWindow.setIgnoreMouseEvents(ignore, { forward });
});

ipcMain.handle('notch:enter-context-selection', () => {
  if (!mainWindow) return;
  isContextSelecting = true;
  const primaryDisplay = screen.getPrimaryDisplay();
  mainWindow.setBounds(primaryDisplay.bounds, false);
});

ipcMain.handle('notch:exit-context-selection', () => {
  if (!mainWindow) return;
  isContextSelecting = false;
  const normalBounds = calculateNotchBounds(NOTCH_WIDTH, NOTCH_HEIGHT);
  mainWindow.setBounds(normalBounds, true);
});

ipcMain.handle('notch:close', () => {
  app.quit();
});

ipcMain.handle('notch:get-display-bounds', () => {
  const primaryDisplay = screen.getPrimaryDisplay();
  return primaryDisplay.bounds;
});

// App Lifecycle
app.on('second-instance', () => {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  }
});

app.whenReady().then(() => {
  createMainWindow();

  // Global hotkey to wake/toggle notch (Alt+Space or Ctrl+Space)
  try {
    globalShortcut.register('CommandOrControl+Shift+Space', () => {
      if (mainWindow) {
        mainWindow.webContents.send('notch:trigger-action', 'toggle-wake');
      }
    });
  } catch (err) {
    console.warn('Failed to register global shortcut:', err);
  }

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
});
