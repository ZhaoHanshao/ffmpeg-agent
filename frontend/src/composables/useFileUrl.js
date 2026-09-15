import { ref, watch, onUnmounted } from 'vue'
import { API_BASE, authHeaders, getToken } from '../api'

/** 文件在服务端的直链。src: 'upload' | 'output'。 */
export function fileUrl(name, src = 'upload') {
  const base = src === 'output' ? 'output' : 'upload'
  return `${API_BASE}/${base}/${encodeURIComponent(name || '')}`
}

/**
 * 开启鉴权时能预取的上限。
 *
 * 没有它的话，点一个 1GB 的上传视频会先把它**整个读进内存**（Blob 无法流式喂给
 * <video>），页面直接卡住且没有任何提示。超过上限就退化成"只提供下载"。
 */
export const MAX_PREFETCH_BYTES = 256 * 1024 * 1024

/**
 * 取文件体积（字节）；拿不到返回 0，由调用方按"未知"处理。
 *
 * **不能用 HEAD**：当前 FastAPI 0.138 + Starlette 1.3 不会给 `@app.get` 自动加
 * HEAD，任何 HEAD 请求都会落到末尾的 StaticFiles 挂载上返回 404
 * （`/api/health`、`/api/output` 一并实测如此）。旧实现用 HEAD 取体积，
 * 结果列表里的文件大小永远是空的、超大文件保护也永远不触发。
 * 改用 Range 取首字节：206 响应的 `Content-Range: bytes 0-0/<总长>` 就是答案。
 */
export async function fetchFileSize(name, src = 'upload') {
  try {
    const res = await fetch(fileUrl(name, src), {
      headers: { ...authHeaders(), Range: 'bytes=0-0' },
    })
    const range = res.headers.get('Content-Range') || ''
    const total = parseInt(range.split('/')[1] || '0', 10)
    if (total > 0) return total
    if (!res.ok) return 0
    return parseInt(res.headers.get('Content-Length') || '0', 10) || 0
  } catch {
    return 0
  }
}

/**
 * 取到能直接用于 <img>/<video>/<a download> 的 URL。
 *
 * 启用 AUTH_TOKEN 时必须走 fetch：这些标签**无法附带自定义请求头**，
 * 直链一律 401（已实测）。未启用鉴权时返回直链——这样视频/音频还能走
 * range 流式播放，不必先整包下载到内存。
 *
 * 返回值里的 revoke 在需要认证时才非空，用完必须调用，否则 blob 常驻内存。
 * oversized 非 0 表示文件超过预取上限，调用方应改为提示下载。
 */
export async function resolveFileUrl(name, src = 'upload') {
  if (!name) return { url: '', revoke: null }
  if (!getToken()) return { url: fileUrl(name, src), revoke: null }
  const size = await fetchFileSize(name, src)
  if (size > MAX_PREFETCH_BYTES) return { url: '', revoke: null, oversized: size }
  const res = await fetch(fileUrl(name, src), { headers: authHeaders() })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  return { url, revoke: () => URL.revokeObjectURL(url) }
}

/** 直接取二进制内容（文本预览、体积统计等用）。 */
export async function fetchFileBlob(name, src = 'upload') {
  const res = await fetch(fileUrl(name, src), { headers: authHeaders() })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.blob()
}

/**
 * 触发浏览器下载。
 *
 * knownUrl 传已经拿到的 blob URL 时直接复用；传空（无法预览、没有预取的类型）
 * 就按需取一次——**开启 AUTH_TOKEN 时 <a href=直链> 带不了鉴权头会 401**，
 * 所以不能简单地只放一个直链。
 */
export async function downloadFile(name, src = 'upload', knownUrl = '') {
  if (!name) return
  let href = knownUrl
  let temp = ''
  if (!href) {
    const blob = await fetchFileBlob(name, src)
    href = temp = URL.createObjectURL(blob)
  }
  const a = document.createElement('a')
  a.href = href
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  if (temp) setTimeout(() => URL.revokeObjectURL(temp), 10000)
}

/**
 * 按内容猜编码解码文本。
 *
 * 中文字幕/日志常见 GB18030（UTF-8 解码会满屏 U+FFFD），所以先按 UTF-8 解，
 * 替换字符占比偏高时再试 GB18030。判断阈值保守一点，避免把正常文本误判成乱码。
 */
export function decodeText(buffer) {
  const bytes = new Uint8Array(buffer)
  const utf8 = new TextDecoder('utf-8').decode(bytes)
  const bad = (utf8.match(/\uFFFD/g) || []).length
  if (bad > Math.max(2, utf8.length * 0.005)) {
    try {
      return new TextDecoder('gb18030').decode(bytes)
    } catch {
      // 浏览器不支持该编码：退回 UTF-8 的结果
    }
  }
  return utf8
}

/**
 * 跟着 (name, src) 变化的可预览 URL。组件卸载时自动回收 blob。
 *
 * 请求是异步的，期间用户可能已经切到别的文件，所以回来后要再核对一次目标，
 * 否则会把上一个文件的内容显示在当前文件名下。
 */
export function useFileUrl(source) {
  const url = ref('')
  const loading = ref(false)
  const error = ref('')
  // 超过预取上限时的体积（>0 表示"太大了，别预取"）
  const oversized = ref(0)
  let revoke = null
  let seq = 0

  function cleanup() {
    if (revoke) {
      revoke()
      revoke = null
    }
  }

  async function load() {
    const { name, src } = typeof source === 'function' ? source() : source.value
    const token = ++seq
    cleanup()
    error.value = ''
    oversized.value = 0
    url.value = ''
    if (!name) {
      loading.value = false
      return
    }
    loading.value = true
    try {
      const res = await resolveFileUrl(name, src)
      if (token !== seq) {
        // 期间已经切走了：把这次拿到的 blob 直接丢掉
        res.revoke?.()
        return
      }
      if (res.oversized) {
        oversized.value = res.oversized
        return
      }
      revoke = res.revoke
      url.value = res.url
    } catch (e) {
      if (token === seq) error.value = e?.message || '加载失败'
    } finally {
      if (token === seq) loading.value = false
    }
  }

  watch(source, load, { immediate: true })
  onUnmounted(() => {
    seq += 1
    cleanup()
  })

  return { url, loading, error, oversized, reload: load }
}
