export function isImage(name) {
  return /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(name)
}

export function isVideo(name) {
  return /\.(mp4|webm|avi|mov|mkv)$/i.test(name)
}

export function isAudio(name) {
  return /\.(mp3|wav|flac|aac|m4a|ogg|opus|wma)$/i.test(name)
}

/** 能按纯文本读出来展示的类型（字幕/日志/配置等）。 */
export function isText(name) {
  return /\.(srt|ass|ssa|vtt|sub|json|txt|log|xml|csv|md|ya?ml|ini|conf)$/i.test(name)
}

/**
 * 预览方式：image / video / audio / text / none。
 * 集中在这里，避免文件面板和消息区各写一套扩展名判断后逐渐跑偏。
 */
export function previewKind(name) {
  if (isImage(name)) return 'image'
  if (isVideo(name)) return 'video'
  if (isAudio(name)) return 'audio'
  if (isText(name)) return 'text'
  return 'none'
}

/** 按扩展名给出文件类型图标与中文类型名，用于文件列表与消息里的文件标签。 */
export function fileKind(name) {
  const n = String(name || '').toLowerCase()
  if (isVideo(n)) return { icon: '🎬', label: '视频' }
  if (isAudio(n)) return { icon: '🎵', label: '音频' }
  if (isImage(n)) return { icon: '🖼️', label: '图片' }
  if (/\.(srt|ass|ssa|vtt|sub)$/.test(n)) return { icon: '💬', label: '字幕' }
  if (/\.(json|txt|log|xml|csv)$/.test(n)) return { icon: '📝', label: '文本' }
  return { icon: '📄', label: '文件' }
}

/** 人类可读的体积；未知（0/非数字）返回空串，便于模板里 v-if 判断。 */
export function formatSize(bytes) {
  const n = Number(bytes)
  if (!Number.isFinite(n) || n <= 0) return ''
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i += 1
  }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`
}

export function sanitizeHtml(html) {
  const doc = new DOMParser().parseFromString(html, 'text/html')
  doc.querySelectorAll('script, iframe, object, embed, link, meta, style').forEach((el) => el.remove())
  for (const el of doc.body.querySelectorAll('*')) {
    for (const attr of [...el.attributes]) {
      const name = attr.name.toLowerCase()
      const val = attr.value.trim().toLowerCase()
      if (
        name.startsWith('on') ||
        ((name === 'href' || name === 'src') && val.startsWith('javascript:'))
      ) {
        el.removeAttribute(attr.name)
      }
    }
  }
  for (const pre of doc.querySelectorAll('pre')) {
    const wrap = doc.createElement('div')
    wrap.className = 'code-block'
    // 顶部工具条：左边语言、右边复制。常驻显示，不再依赖悬停
    // （旧实现把按钮绝对定位在右上角、悬停才显形，触屏设备永远看不到，
    //  还得给 pre 预留 32px 空白带，标题区看着像被挖了个洞）。
    const bar = doc.createElement('div')
    bar.className = 'code-bar'
    const codeEl = pre.querySelector('code')
    const matched = /(?:language|lang)-([\w+#.-]+)/.exec((codeEl && codeEl.className) || '')
    if (matched) {
      const lang = doc.createElement('span')
      lang.className = 'code-lang'
      lang.textContent = matched[1]
      bar.appendChild(lang)
    }
    const btn = doc.createElement('button')
    btn.className = 'code-copy'
    btn.type = 'button'
    btn.textContent = '⧉ 复制'
    bar.appendChild(btn)
    pre.parentNode.insertBefore(wrap, pre)
    wrap.appendChild(bar)
    wrap.appendChild(pre)
  }
  return doc.body.innerHTML
}
