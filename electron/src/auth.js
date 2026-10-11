'use strict'
/**
 * 访问令牌的注入。
 *
 * 为什么由主进程来加、而不是把令牌交给页面：
 *   Electron 里发往 http://127.0.0.1:<port> 的所有请求都会经过 defaultSession，
 *   在这里补一个请求头，页面自己完全不需要知道令牌存在——
 *   前端在 localStorage 里没有令牌时会走「直链」(<img src> / <video src>)，
 *   而直链同样过 session，于是预览、播放、下载全都照常工作。
 *
 * 顺带的好处：桌面版监听回环端口，本机其它进程或浏览器里发起的跨站请求
 * 即使猜到端口也调不动 /api/*（拿不到这个头）。
 */
const { session } = require('electron')

/**
 * 给发往后端的请求统一补上 X-Auth-Token。
 * @param {number} port  后端监听端口
 * @param {string} token 后端启动时拿到的 AUTH_TOKEN
 */
function installAuthHeader (port, token) {
  if (!port || !token) return
  const filter = {
    urls: [`http://127.0.0.1:${port}/*`, `http://localhost:${port}/*`]
  }
  session.defaultSession.webRequest.onBeforeSendHeaders(filter, (details, callback) => {
    const headers = { ...details.requestHeaders }
    // 前端若自己带了一个（用户在设置弹窗里填过），以主进程的为准：
    // 删掉同名的任意大小写变体，避免出现两个 X-Auth-Token 让后端取到错的那个
    for (const key of Object.keys(headers)) {
      if (key.toLowerCase() === 'x-auth-token') delete headers[key]
    }
    headers['X-Auth-Token'] = token
    callback({ requestHeaders: headers })
  })
}

module.exports = { installAuthHeader }
