'use strict'
/**
 * 预加载脚本（主窗口与启动画面共用）。
 *
 * 只暴露只读信息与几个"宿主能力"，不给页面任何 Node 能力：
 * contextIsolation + sandbox 都开着，页面拿到的就是下面这几个函数。
 */
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('agentHost', {
  isElectron: true,
  platform: process.platform,

  /** 订阅后端启动/初始化进度；返回取消订阅函数。 */
  onBackendStatus: (cb) => {
    const handler = (_event, payload) => {
      try { cb(payload) } catch { /* 回调自身出错不该影响宿主 */ }
    }
    ipcRenderer.on('backend-status', handler)
    return () => ipcRenderer.removeListener('backend-status', handler)
  },

  info: () => ipcRenderer.invoke('app:info'),
  openDataDir: () => ipcRenderer.invoke('app:open-data-dir'),
  openLogs: () => ipcRenderer.invoke('app:open-logs')
})
