import { contextBridge, ipcRenderer } from 'electron';
import type { ElectronAPI } from '../src/types/notch';

const electronAPI: ElectronAPI = {
  resizeWindow: (width: number, height: number, animated = true) => {
    return ipcRenderer.invoke('notch:resize', { width, height, animated });
  },
  setIgnoreMouseEvents: (ignore: boolean, forward = false) => {
    return ipcRenderer.invoke('notch:set-ignore-mouse-events', { ignore, forward });
  },
  enterContextSelectionMode: () => {
    return ipcRenderer.invoke('notch:enter-context-selection');
  },
  exitContextSelectionMode: () => {
    return ipcRenderer.invoke('notch:exit-context-selection');
  },
  enterWakeOverlay: () => {
    return ipcRenderer.invoke('notch:enter-wake-overlay');
  },
  exitWakeOverlay: () => {
    return ipcRenderer.invoke('notch:exit-wake-overlay');
  },
  closeApp: () => {
    return ipcRenderer.invoke('notch:close');
  },
  onStateTrigger: (callback: (action: string) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, action: string) => {
      callback(action);
    };
    ipcRenderer.on('notch:trigger-action', handler);
    return () => {
      ipcRenderer.removeListener('notch:trigger-action', handler);
    };
  },
  getDisplayBounds: () => {
    return ipcRenderer.invoke('notch:get-display-bounds');
  },
  openUrl: (url: string) => {
    return ipcRenderer.invoke('notch:open-url', url);
  },
};

contextBridge.exposeInMainWorld('electronAPI', electronAPI);
