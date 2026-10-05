import math
import sys
from PIL import Image, ImageDraw, ImageFont

SS = 16  # supersampling factor

BODY = (120, 200, 160, 255)
BODY_DARK = (40, 110, 85, 255)
NUCLEUS = (60, 140, 110, 255)
CILIA = (40, 110, 85, 255)


def slipper_outline(cx, cy, a, b, tilt_deg, n=240):
    """Slipper-shaped (paramecium) outline: ellipse, fatter rear, oral groove on one side."""
    pts = []
    t0 = math.radians(tilt_deg)
    for i in range(n):
        t = 2 * math.pi * i / n
        x = a * math.cos(t)
        # rear (x<0) slightly fatter than front
        w = b * (1.0 + 0.12 * (-math.cos(t)))
        y = w * math.sin(t)
        # oral groove: dent on the lower side around the middle
        if math.sin(t) < 0:
            d = math.exp(-((math.cos(t) - 0.1) ** 2) / 0.08)
            y += b * 0.18 * d
        xr = x * math.cos(t0) - y * math.sin(t0)
        yr = x * math.sin(t0) + y * math.cos(t0)
        pts.append((cx + xr, cy + yr))
    return pts


def draw_body(size, with_nucleus=True, cilia=True, tilt=-35, bw=0.25, aw=0.43):
    S = size * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = S / 2
    a = S * aw
    b = S * bw
    outline = slipper_outline(cx, cy, a, b, tilt)

    if cilia:
        # short cilia ticks along the outline normal
        n = len(outline)
        step = 12
        L = S * 0.07
        for i in range(0, n, step):
            p0 = outline[i - 1]
            p1 = outline[(i + 1) % n]
            tx, ty = p1[0] - p0[0], p1[1] - p0[1]
            ln = math.hypot(tx, ty) or 1
            nx, ny = ty / ln, -tx / ln
            px, py = outline[i]
            d.line([(px, py), (px + nx * L, py + ny * L)], fill=CILIA, width=max(1, int(S * 0.035)))

    d.polygon(outline, fill=BODY)
    d.line(outline + [outline[0]], fill=BODY_DARK, width=max(1, int(S * 0.055)), joint="curve")

    if with_nucleus:
        t0 = math.radians(tilt)
        nx, ny = cx + S * 0.02, cy - S * 0.02
        ra, rb = S * 0.12, S * 0.075
        pts = []
        for i in range(60):
            t = 2 * math.pi * i / 60
            x, y = ra * math.cos(t), rb * math.sin(t)
            pts.append((nx + x * math.cos(t0) - y * math.sin(t0), ny + x * math.sin(t0) + y * math.cos(t0)))
        d.polygon(pts, fill=NUCLEUS)
    return img


def add_text(img, text):
    S = img.size[0]
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", int(S * 0.36))
    bbox = d.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (S - w) / 2 - bbox[0]
    y = (S - h) / 2 - bbox[1]
    d.text((x, y), text, font=font, fill=(255, 255, 255, 255),
           stroke_width=int(S * 0.04), stroke_fill=BODY_DARK)
    return img


def finish(img, size):
    return img.resize((size, size), Image.LANCZOS)


def main(out_dir):
    plain24 = finish(draw_body(24), 24)
    plain16 = finish(draw_body(16, cilia=False), 16)
    fc24 = finish(add_text(draw_body(24, with_nucleus=False, tilt=-12, bw=0.29, aw=0.40), "FC"), 24)
    plain24.save(f"{out_dir}/paramecium-24.png")
    plain16.save(f"{out_dir}/paramecium-16.png")
    fc24.save(f"{out_dir}/freecad-part-24.png")

    # preview sheet: actual size + 8x nearest-neighbour zoom
    sheet = Image.new("RGBA", (24 * 8 * 3 + 40, 24 * 8 + 60), (215, 215, 210, 255))
    for i, im in enumerate([plain24, plain16, fc24]):
        z = im.resize((im.size[0] * 8, im.size[1] * 8), Image.NEAREST)
        sheet.alpha_composite(z, (10 + i * (24 * 8 + 10), 10))
        sheet.alpha_composite(im, (10 + i * (24 * 8 + 10), 24 * 8 + 25))
    sheet.save(f"{out_dir}/preview.png")


if __name__ == "__main__":
    main(sys.argv[1])
