export function isImage(name) {
  return /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(name)
}

export function isVideo(name) {
  return /\.(mp4|webm|avi|mov|mkv)$/i.test(name)
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
    const btn = doc.createElement('button')
    btn.className = 'code-copy'
    btn.type = 'button'
    btn.textContent = '⧉ 复制'
    pre.parentNode.insertBefore(wrap, pre)
    wrap.appendChild(btn)
    wrap.appendChild(pre)
  }
  return doc.body.innerHTML
}
