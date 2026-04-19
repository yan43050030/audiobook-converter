"""生成应用图标：一个人在读书"""

from PIL import Image, ImageDraw


def create_icon():
    size = 512
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 背景圆
    margin = 20
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=(45, 85, 165),  # 深蓝
    )

    cx, cy = size // 2, size // 2 + 30

    # 头部
    head_r = 55
    draw.ellipse(
        [cx - head_r, cy - 170 - head_r * 2, cx + head_r, cy - 170],
        fill=(255, 215, 175),  # 肤色
    )

    # 身体（半圆披风）
    draw.pieslice(
        [cx - 100, cy - 120, cx + 100, cy + 80],
        start=0, end=180,
        fill=(220, 180, 120),  # 衣服颜色
    )

    # 书本（打开的书）
    book_y = cy - 60
    # 左页
    draw.polygon(
        [(cx - 5, book_y), (cx - 95, book_y - 15), (cx - 95, book_y + 70), (cx - 5, book_y + 85)],
        fill=(255, 250, 240),
        outline=(180, 160, 130),
    )
    # 右页
    draw.polygon(
        [(cx + 5, book_y), (cx + 95, book_y - 15), (cx + 95, book_y + 70), (cx + 5, book_y + 85)],
        fill=(255, 250, 240),
        outline=(180, 160, 130),
    )
    # 书脊线
    draw.line([(cx, book_y - 5), (cx, book_y + 88)], fill=(150, 120, 80), width=2)

    # 左页文字线条
    for i in range(5):
        y = book_y + 10 + i * 13
        draw.line([(cx - 80, y), (cx - 20, y)], fill=(180, 180, 180), width=2)

    # 右页文字线条
    for i in range(5):
        y = book_y + 10 + i * 13
        draw.line([(cx + 20, y), (cx + 80, y)], fill=(180, 180, 180), width=2)

    # 耳机（表示听书）
    ear_y = cy - 230
    draw.arc(
        [cx - 50, ear_y - 10, cx + 50, ear_y + 50],
        start=180, end=0,
        fill=(60, 60, 60), width=6,
    )
    # 左耳塞
    draw.ellipse([cx - 55, ear_y + 15, cx - 38, ear_y + 40], fill=(60, 60, 60))
    # 右耳塞
    draw.ellipse([cx + 38, ear_y + 15, cx + 55, ear_y + 40], fill=(60, 60, 60))

    # 保存多种尺寸
    img.save("icon.png")

    # macOS icns
    sizes = [16, 32, 64, 128, 256, 512]
    imgs = [img.resize((s, s), Image.LANCZOS) for s in sizes]
    imgs[0].save("icon.ico", format="ICO", sizes=[(s, s) for s in sizes])

    print("图标生成完成: icon.png, icon.ico")


if __name__ == "__main__":
    create_icon()
