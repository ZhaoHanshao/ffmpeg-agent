"""生成 Electron 应用图标（icon.png + icon.ico）。

图标与 frontend 的品牌色、启动画面的 logo 是同一套视觉：
圆角方形底 + 胶片框 + 播放三角。

用法（需要 Pillow，仓库 .venv 里没有，用任意带 Pillow 的解释器均可）：
    python electron/build/make_icon.py
"""
import os

from PIL import Image, ImageDraw

SIZE = 512
SS = 4                      # 超采样倍数：先画 4 倍大再缩回来，边缘才不会有锯齿
BRAND_TOP = (91, 124, 250)  # --dsh-brand 提亮一档
BRAND_BOTTOM = (53, 84, 216)  # --dsh-brand-strong 压暗一档
GLYPH = (255, 255, 255)
STROKE = 20                 # 512 画布上的线宽

OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def rounded_gradient(size: int, radius: int) -> Image.Image:
    """左上→右下的品牌色渐变，裁成圆角方形。"""
    grad = Image.new('RGB', (size, size))
    px = grad.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            px[x, y] = tuple(
                round(BRAND_TOP[i] + (BRAND_BOTTOM[i] - BRAND_TOP[i]) * t) for i in range(3)
            )
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    out = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    return out


def draw_glyph(img: Image.Image, scale: int) -> None:
    """按启动画面那份 SVG 的坐标（24×24 视口）等比放大绘制。"""
    d = ImageDraw.Draw(img)
    # 24 单位视口 → 画布宽度的 60%，居中
    s = scale * 0.60 / 24
    off = (scale - 24 * s) / 2

    def X(u):  # noqa: N802 - 借 SVG 的坐标记号，读起来更直观
        return off + u * s

    w = max(1, round(STROKE * scale / SIZE))

    # 胶片框
    d.rounded_rectangle(
        [X(2.5), X(4.5), X(21.5), X(19.5)], radius=X(2.5) - X(0), outline=GLYPH, width=w
    )
    # 两条横线 + 两条竖线（齿孔分隔）
    for y in (9.2, 14.8):
        d.line([X(2.5), X(y), X(21.5), X(y)], fill=GLYPH, width=w)
    for x in (7, 17):
        d.line([X(x), X(4.5), X(x), X(19.5)], fill=GLYPH, width=w)

    # 播放三角（实心）
    d.polygon([(X(10.6), X(11.2)), (X(14.2), X(13.3)), (X(10.6), X(15.4))], fill=GLYPH)


def build() -> Image.Image:
    big = rounded_gradient(SIZE * SS, radius=112 * SS)
    draw_glyph(big, SIZE * SS)
    return big.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> None:
    icon = build()
    png = os.path.join(OUT_DIR, 'icon.png')
    ico = os.path.join(OUT_DIR, 'icon.ico')
    icon.save(png)
    # Windows 会按显示位置挑尺寸，所以把常用几档都塞进 ico
    icon.save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f'已生成 {png}')
    print(f'已生成 {ico}')


if __name__ == '__main__':
    main()
