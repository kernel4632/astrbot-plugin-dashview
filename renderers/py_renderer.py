from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Optional

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ImportError:
    raise ImportError("Pillow is required for py_renderer. Install with: pip install Pillow")


# 颜色配置（RGB）- 马卡龙玻璃态配色
class Colors:
    PRIMARY_TEXT = (55, 55, 71)
    SECONDARY_TEXT = (100, 100, 120)
    LIGHT_TEXT = (144, 144, 168)
    CARD_BG = (255, 255, 255)

    GREEN = (100, 200, 155)
    ORANGE = (255, 180, 145)
    YELLOW = (255, 210, 140)
    RED = (250, 140, 160)
    CYAN = (120, 195, 225)
    BLUE = (120, 165, 215)
    PURPLE = (185, 160, 210)
    GRAY = (210, 210, 225)

    BG_FALLBACK = (255, 248, 250)
    BG_MASK = (255, 248, 250, 180)

    CARD_BG_OPAQUE = (255, 255, 255, 130)
    CARD_OVERLAY = (255, 255, 255, 160)
    BORDER = (255, 255, 255, 180)
    DIVIDER = (200, 200, 220, 100)


def _get_font(size: int = 12) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font_paths = [
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-SC-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-SC-Regular.otf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto-cjk/NotoSansCJK-Regular.ttc",
        "C:\\Windows\\Fonts\\msyh.ttc",
        "C:\\Windows\\Fonts\\msyhbd.ttc",
        "C:\\Windows\\Fonts\\simhei.ttf",
        "C:\\Windows\\Fonts\\simkai.ttf",
        "C:\\Windows\\Fonts\\simsun.ttc",
        "C:\\Windows\\Fonts\\meiryo.ttc",
        "C:\\Windows\\Fonts\\msgothic.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB W3.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
    ]
    for path in font_paths:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


def _get_color_for_percent(percent: Optional[float]) -> tuple[int, int, int]:
    if percent is None:
        return Colors.GRAY
    if percent < 70:
        return Colors.GREEN
    if percent < 90:
        return Colors.ORANGE
    return Colors.RED


def _format_bytes(value: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    v = float(value)
    while v >= 1024 and idx < len(units) - 1:
        v /= 1024
        idx += 1
    if idx == 0:
        return f"{v:.0f}{units[idx]}"
    return f"{v:.1f}{units[idx]}"


def _draw_glass_card(
    img: Image.Image,
    rect: tuple[int, int, int, int],
    radius: int,
    blur_radius: int,
) -> None:
    x1, y1, x2, y2 = rect
    if x2 <= x1 or y2 <= y1:
        return

    crop = img.crop((x1, y1, x2, y2)).filter(ImageFilter.GaussianBlur(blur_radius * 1.5))
    mask = Image.new("L", (x2 - x1, y2 - y1), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.rounded_rectangle((0, 0, x2 - x1, y2 - y1), radius=radius, fill=255)
    img.paste(crop, (x1, y1), mask)

    overlay = Image.new("RGBA", (x2 - x1, y2 - y1), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.rounded_rectangle((0, 0, x2 - x1, y2 - y1), radius=radius, fill=Colors.CARD_OVERLAY)
    img.alpha_composite(overlay, (x1, y1))

    shadow_offset = 3
    shadow_img = Image.new("RGBA", (x2 - x1 + shadow_offset * 2, y2 - y1 + shadow_offset * 2), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_img)
    shadow_draw.rounded_rectangle(
        (shadow_offset, shadow_offset, x2 - x1 + shadow_offset, y2 - y1 + shadow_offset),
        radius=radius,
        fill=(200, 200, 220, 35),
    )
    img.alpha_composite(shadow_img, (x1 - shadow_offset, y1 - shadow_offset))

    border = ImageDraw.Draw(img)
    border.rounded_rectangle(
        (x1, y1, x2, y2),
        radius=radius,
        outline=Colors.BORDER,
        width=1,
    )


def _draw_mini_chart(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    height: int,
    color: tuple[int, int, int],
    base_value: float = 50,
) -> None:
    import random

    random.seed(hash((x, y, color)))

    points = []
    num_points = 30
    prev_value = base_value
    trend_accum = 0

    for i in range(num_points):
        noise = random.gauss(0, 4)
        trend_change = random.uniform(-0.8, 0.8)
        trend_accum = max(-8, min(8, trend_accum + trend_change))

        value = prev_value + noise + trend_accum
        value = max(15, min(85, value))

        px = x + (width / (num_points - 1)) * i
        py = y + height * (1 - value / 100)
        points.append((px, py))
        prev_value = value

    fill_points = points.copy()
    fill_points.append((x + width, y + height))
    fill_points.append((x, y + height))
    draw.polygon(fill_points, fill=(color[0], color[1], color[2], 40))

    if len(points) > 2:
        smooth_points = []
        for i in range(len(points) - 1):
            if i == 0:
                p0, p1, p2 = points[0], points[0], points[1]
            elif i == len(points) - 2:
                p0, p1, p2 = points[-2], points[-1], points[-1]
            else:
                p0, p1, p2 = points[i - 1], points[i], points[i + 1]

            cp1_x = p1[0] + (p2[0] - p0[0]) / 6
            cp1_y = p1[1] + (p2[1] - p0[1]) / 6

            if i < len(points) - 2:
                p3 = points[i + 2]
                cp2_x = p2[0] - (p3[0] - p1[0]) / 6
                cp2_y = p2[1] - (p3[1] - p1[1]) / 6
            else:
                cp2_x, cp2_y = p2[0], p2[1]

            smooth_points.append((p1[0], p1[1], cp1_x, cp1_y, cp2_x, cp2_y, p2[0], p2[1]))

        for pt in smooth_points:
            draw.line([(pt[0], pt[1]), (pt[2], pt[3])], fill=(color[0], color[1], color[2], 200), width=2)
            draw.line([(pt[4], pt[5]), (pt[6], pt[7])], fill=(color[0], color[1], color[2], 200), width=2)


def render_py(
    collected: dict[str, Any],
    bg_bytes: bytes,
    avatar_bytes: Optional[bytes] = None,
    bots: Optional[list[dict]] = None,
) -> bytes:
    """使用 PIL 生成状态图片（横向仪表盘卡片布局 - 仅显示header、resources、services）"""
    scale = 2

    def s(v: int) -> int:
        return int(v * scale)

    def text_size(draw_ctx: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
        bb = draw_ctx.textbbox((0, 0), text, font=font)
        return bb[2] - bb[0], bb[3] - bb[1]

    def fit_text(draw_ctx: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> str:
        if max_width <= 0:
            return ""
        if text_size(draw_ctx, text, font)[0] <= max_width:
            return text
        ellipsis = "..."
        if text_size(draw_ctx, ellipsis, font)[0] > max_width:
            return ""
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            cand = text[:mid] + ellipsis
            if text_size(draw_ctx, cand, font)[0] <= max_width:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo] + ellipsis

    # 横向布局：宽度900px，高度自适应（足够容纳所有内容）
    width = s(900)
    max_height = s(500)

    disk_usage = collected.get("disk_usage", []) or []

    font_h1 = _get_font(s(28))
    font_title = _get_font(s(18))
    font_base = _get_font(s(14))
    font_small = _get_font(s(12))
    font_tiny = _get_font(s(10))

    outer_pad = s(16)
    card_pad = s(16)
    gap = s(16)

    try:
        bg_img = Image.open(io.BytesIO(bg_bytes)).convert("RGB")
        bg_ratio = bg_img.width / bg_img.height
        if bg_ratio > width / max_height:
            new_height = bg_img.height
            new_width = int(new_height * width / max_height)
            bg_img = bg_img.crop(((bg_img.width - new_width) // 2, 0, (bg_img.width + new_width) // 2, new_height))
        bg_img = bg_img.resize((width, max_height), Image.Resampling.LANCZOS)
    except Exception:
        bg_img = Image.new("RGB", (width, max_height), Colors.BG_FALLBACK)

    img = Image.new("RGBA", (width, max_height), (0, 0, 0, 0))
    img.paste(bg_img.convert("RGBA"), (0, 0))
    mask_overlay = Image.new("RGBA", (width, max_height), Colors.BG_MASK)
    img.paste(mask_overlay, (0, 0), mask_overlay)
    draw = ImageDraw.Draw(img)

    x1 = outer_pad
    x2 = width - outer_pad
    content_w = x2 - x1
    y = outer_pad

    # ========== Header Card ==========
    header_h = s(80)
    _draw_glass_card(img, (x1, y, x2, y + header_h), radius=s(20), blur_radius=s(4))

    logo_x = x1 + card_pad
    logo_y = y + header_h // 2
    logo_r = s(24)
    logo_gradient = Image.new("RGBA", (logo_r * 2, logo_r * 2), (0, 0, 0, 0))
    logo_draw = ImageDraw.Draw(logo_gradient)
    for i in range(logo_r, 0, -1):
        alpha = int(255 * (1 - i / logo_r))
        ratio = i / logo_r
        r = int(Colors.RED[0] * ratio + Colors.PURPLE[0] * (1 - ratio))
        g = int(Colors.RED[1] * ratio + Colors.PURPLE[1] * (1 - ratio))
        b = int(Colors.RED[2] * ratio + Colors.PURPLE[2] * (1 - ratio))
        logo_draw.ellipse((logo_r - i, logo_r - i, logo_r + i, logo_r + i), fill=(r, g, b, alpha))
    img.paste(logo_gradient, (logo_x, logo_y - logo_r), logo_gradient)
    draw.text((logo_x + logo_r, logo_y), "A", fill=(255, 255, 255, 255), font=_get_font(s(32)), anchor="mm")

    tx = logo_x + logo_r * 2 + s(12)
    draw.text((tx, logo_y - s(8)), "AstrBot 运行状态", fill=Colors.PRIMARY_TEXT, font=font_h1)

    hostname = collected.get("hostname", "")
    sys_time = collected.get("system_run_time", "")
    os_info = collected.get("os_info", "")
    meta_text = f"{hostname} | 运行 {sys_time} | {os_info}"
    meta_text = fit_text(draw, meta_text, font_tiny, content_w - (tx - x1) - s(80))
    draw.text((tx, logo_y + s(12)), meta_text, fill=Colors.LIGHT_TEXT, font=font_tiny)

    time_text = collected.get("time", "")
    tw, _ = text_size(draw, time_text, font_title)
    draw.text((x2 - card_pad - tw, logo_y - s(8)), time_text, fill=Colors.PRIMARY_TEXT, font=font_title)
    version_text = collected.get("astrbot_version", "")
    vw, _ = text_size(draw, version_text, font_tiny)
    draw.text((x2 - card_pad - vw, logo_y + s(12)), version_text, fill=Colors.LIGHT_TEXT, font=font_tiny)

    y += header_h + gap

    # ========== Main Content (横向布局：资源概览在左，服务监控在右) ==========
    resources_w = content_w - s(280) - s(16)  # 资源概览宽度
    services_w = s(280)  # 服务监控宽度
    content_h = s(200)  # 内容区域高度

    # ========== Resources Card (资源概览 - 左侧) ==========
    _draw_glass_card(img, (x1, y, x1 + resources_w, y + content_h), radius=s(16), blur_radius=s(4))
    draw.text((x1 + card_pad, y + card_pad), "系统状态", fill=Colors.PRIMARY_TEXT, font=font_title)

    # 资源项目：CPU、内存、磁盘（纵向排列）
    resource_items = [
        {"title": "CPU", "color": Colors.CYAN},
        {"title": "内存", "color": Colors.GREEN},
        {"title": "磁盘", "color": Colors.PURPLE},
    ]

    cpu_percent = collected.get("cpu_percent", 0)
    mem_stat = collected.get("memory_stat")
    mem_percent = getattr(mem_stat, "percent", 0) if mem_stat else 0
    disk_percent = disk_usage[0].percent if disk_usage else 0

    resource_values = [cpu_percent, mem_percent, disk_percent]
    resource_units = ["%", "%", "%"]

    cpu_count = collected.get("cpu_count", "??")
    cpu_logical = collected.get("cpu_count_logical", "??")
    mem_used = _format_bytes(getattr(mem_stat, "used", 0)) if mem_stat else "0"
    mem_total = _format_bytes(getattr(mem_stat, "total", 0)) if mem_stat else "0"
    disk_used = _format_bytes(int(getattr(disk_usage[0], "used", 0))) if disk_usage else "0"
    disk_total = _format_bytes(int(getattr(disk_usage[0], "total", 0))) if disk_usage else "0"

    resource_subtitles = [
        f"{cpu_count}核 {cpu_logical}线程",
        f"{mem_used} / {mem_total}",
        f"{disk_used} / {disk_total}",
    ]

    resource_item_h = (content_h - s(40)) // 3  # 每个资源项高度
    for i, item in enumerate(resource_items):
        item_y = y + card_pad + s(30) + i * resource_item_h

        # 图标
        icon_x = x1 + card_pad + s(8)
        icon_y = item_y
        draw.rectangle((icon_x, icon_y, icon_x + s(32), icon_y + s(32)), fill=item["color"], width=0)

        # 标题
        title_x = icon_x + s(38)
        draw.text((title_x, item_y + s(4)), item["title"], fill=Colors.LIGHT_TEXT, font=font_tiny)

        # 数值
        value_text = f"{resource_values[i]:.1f}"
        draw.text((title_x, item_y + s(18)), value_text, fill=Colors.PRIMARY_TEXT, font=_get_font(s(26)))

        # 单位
        uw, _ = text_size(draw, resource_units[i], font_small)
        draw.text((title_x + s(45), item_y + s(22)), resource_units[i], fill=Colors.SECONDARY_TEXT, font=font_small)

        # 进度条
        bar_x = title_x
        bar_y = item_y + s(48)
        bar_w = resources_w - s(60)
        bar_h = s(6)
        draw.rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), fill=(210, 210, 225, 80))

        # 根据使用率决定进度条颜色
        percent = resource_values[i]
        if percent > 90:
            bar_color = Colors.RED
        elif percent > 75:
            bar_color = Colors.ORANGE
        else:
            bar_color = item["color"]

        draw.rectangle((bar_x, bar_y, bar_x + bar_w * (percent / 100), bar_y + bar_h), fill=bar_color)

        # 副标题
        draw.text((title_x, item_y + s(58)), resource_subtitles[i], fill=Colors.LIGHT_TEXT, font=font_tiny)

    # ========== Services Card (服务监控 - 右侧) ==========
    services_x = x1 + resources_w + s(16)
    services_h = content_h
    _draw_glass_card(img, (services_x, y, x2, y + services_h), radius=s(16), blur_radius=s(4))
    draw.text((services_x + card_pad, y + card_pad), "服务监控", fill=Colors.PRIMARY_TEXT, font=font_title)

    services_status = collected.get("services_status", [])
    service_gap = s(12)
    service_item_h = s(50)
    start_y = y + card_pad + s(30)

    for i, service in enumerate(services_status[:3]):
        item_y = start_y + i * (service_item_h + service_gap)

        # 状态指示条
        draw.line((services_x, item_y - s(4), x2, item_y - s(4)), fill=Colors.GREEN, width=s(3))

        # 图标
        icon_x = services_x + card_pad + s(8)
        icon_y = item_y + s(8)
        icon_size = s(32)

        status_color = Colors.GREEN
        if service.get("status") == "warning":
            status_color = Colors.ORANGE
            draw.line((services_x, item_y - s(4), x2, item_y - s(4)), fill=status_color, width=s(3))
        elif service.get("status") == "error":
            status_color = Colors.RED
            draw.line((services_x, item_y - s(4), x2, item_y - s(4)), fill=status_color, width=s(3))

        draw.ellipse((icon_x, icon_y, icon_x + icon_size, icon_y + icon_size), fill=status_color)

        # 服务名称
        name_x = icon_x + icon_size + s(10)
        draw.text((name_x, item_y), service.get("name", "未知"), fill=Colors.PRIMARY_TEXT, font=font_small)

        # 状态标签和延迟
        status_text = "正常"
        if service.get("status") == "warning":
            status_text = "警告"
        elif service.get("status") == "error":
            status_text = "异常"

        status_w, _ = text_size(draw, status_text, font_tiny)
        status_x = x2 - card_pad - s(60) - status_w
        draw.text((status_x, item_y), status_text, fill=Colors.SECONDARY_TEXT, font=font_tiny)

        ping_text = f"{service.get('ping', 0)}ms"
        ping_w, _ = text_size(draw, ping_text, font_tiny)
        draw.text((x2 - card_pad - ping_w, item_y), ping_text, fill=Colors.SECONDARY_TEXT, font=font_tiny)

    y += content_h + gap

    # ========== Footer ==========
    footer_h = s(40)
    _draw_glass_card(img, (x1, y, x2, y + footer_h), radius=s(12), blur_radius=s(3))

    footer_left = f"AstrBot {collected.get('astrbot_version', '?')}"
    footer_right = f"Powered by Python {collected.get('python_version', '').split()[0] if collected.get('python_version') else ''}"

    flw, _ = text_size(draw, footer_left, font_tiny)
    frw, _ = text_size(draw, footer_right, font_tiny)

    draw.text((x1 + card_pad, y + footer_h // 2), footer_left, fill=Colors.SECONDARY_TEXT, font=font_tiny, anchor="lm")
    draw.text((x2 - card_pad, y + footer_h // 2), footer_right, fill=Colors.SECONDARY_TEXT, font=font_tiny, anchor="rm")

    y += footer_h + gap

    # 计算最终高度，确保不超过最大高度
    final_h = min(max_height, y + outer_pad)
    final = img.crop((0, 0, width, final_h)).convert("RGB")

    output = io.BytesIO()
    final.save(output, format="PNG")
    return output.getvalue()
