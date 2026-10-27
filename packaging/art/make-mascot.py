"""Draws Koyomi, the setup wizard's mascot, in one SVG per pose.

Koyomi (暦, "calendar") is a tear-off calendar page with AniCal's red header
band, binder rings for ears and big anime eyes, drawn flat with bold
outlines in the manner of Japanese public-service mascots. Run it after
changing a pose (it needs rsvg-convert for the PNG copies the app shows, which
load without an SVG loader in the Windows and macOS builds):

    python3 packaging/art/make-mascot.py anical/data/setup
"""

import subprocess
import sys
from pathlib import Path

INK = "#2b2440"
PAGE = "#fffdf8"
PAGE_SHADE = "#efe9f5"
RED = "#e8423f"
RED_DARK = "#c42b2b"
RING = "#5e5c64"
IRIS_TOP = "#b26bd6"
IRIS_BOTTOM = "#5b2a86"
BLUSH = "#ff8fa3"
GOLD = "#f6c42d"
GOLD_DARK = "#d39b12"
GREEN = "#33b06a"
BLUE = "#3584e4"
W = 6  # outline width

DEFS = f"""
  <defs>
    <linearGradient id="iris" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{IRIS_TOP}"/>
      <stop offset="1" stop-color="{IRIS_BOTTOM}"/>
    </linearGradient>
  </defs>"""


def limb(x1, y1, x2, y2, width=20):
    """An arm or leg: an outlined capsule from (x1, y1) to (x2, y2) with a round end."""
    return (
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{INK}" stroke-width="{width + 2 * W}" stroke-linecap="round"/>'
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{PAGE}" stroke-width="{width}" stroke-linecap="round"/>'
    )


def body(mouth="smile", eyes="open"):
    parts = [
        f'<ellipse cx="120" cy="286" rx="70" ry="10" fill="{INK}" opacity="0.12"/>',
        # legs, behind the page
        limb(96, 236, 92, 268, 22),
        limb(144, 236, 148, 268, 22),
        # binder rings (ears), behind the band
        f'<rect x="78" y="40" width="18" height="44" rx="9" fill="{RING}" stroke="{INK}" stroke-width="{W}"/>',
        f'<rect x="144" y="40" width="18" height="44" rx="9" fill="{RING}" stroke="{INK}" stroke-width="{W}"/>',
        # the page: thickness, then face
        f'<rect x="46" y="70" width="148" height="176" rx="36" fill="{PAGE_SHADE}" stroke="{INK}" stroke-width="{W}"/>',
        f'<rect x="46" y="62" width="148" height="176" rx="36" fill="{PAGE}" stroke="{INK}" stroke-width="{W}"/>',
        # header band
        f'<path d="M82 62 H158 A36 36 0 0 1 194 98 V110 H46 V98 A36 36 0 0 1 82 62 Z" fill="{RED}" stroke="{INK}" '
        f'stroke-width="{W}" stroke-linejoin="round"/>',
        f'<path d="M58 100 H182" stroke="{RED_DARK}" stroke-width="5" stroke-linecap="round" opacity="0.5"/>',
        # tear-off perforation
        f'<path d="M58 116 H182" stroke="{INK}" stroke-width="3" stroke-dasharray="4 7" stroke-linecap="round" opacity="0.35"/>',
    ]
    if eyes == "open":
        for cx in (92, 148):
            parts += [
                f'<ellipse cx="{cx}" cy="160" rx="18" ry="24" fill="{INK}"/>',
                f'<ellipse cx="{cx}" cy="165" rx="14" ry="18" fill="url(#iris)"/>',
                f'<ellipse cx="{cx}" cy="168" rx="7" ry="9" fill="{INK}"/>',
                f'<ellipse cx="{cx - 6}" cy="151" rx="6.5" ry="8" fill="#ffffff"/>',
                f'<circle cx="{cx + 7}" cy="174" r="3" fill="#ffffff" opacity="0.9"/>',
            ]
    else:  # happy, closed eyes: ^ ^
        for cx in (92, 148):
            parts.append(
                f'<path d="M{cx - 16} 166 Q{cx} 144 {cx + 16} 166" fill="none" stroke="{INK}" stroke-width="7" '
                f'stroke-linecap="round"/>'
            )
    parts += [
        f'<ellipse cx="72" cy="192" rx="12" ry="7" fill="{BLUSH}" opacity="0.75"/>',
        f'<ellipse cx="168" cy="192" rx="12" ry="7" fill="{BLUSH}" opacity="0.75"/>',
    ]
    if mouth == "smile":
        parts.append(f'<path d="M110 196 Q120 206 130 196" fill="none" stroke="{INK}" stroke-width="5" stroke-linecap="round"/>')
    else:  # open, cheerful
        parts.append(
            f'<path d="M108 194 Q120 216 132 194 Z" fill="{RED_DARK}" stroke="{INK}" stroke-width="4" stroke-linejoin="round"/>'
        )
    return "\n  ".join(parts)


def sparkle(x, y, size, color=GOLD):
    s = size
    return (
        f'<path d="M{x} {y - s} L{x + s * 0.28} {y - s * 0.28} L{x + s} {y} L{x + s * 0.28} {y + s * 0.28} '
        f'L{x} {y + s} L{x - s * 0.28} {y + s * 0.28} L{x - s} {y} L{x - s * 0.28} {y - s * 0.28} Z" fill="{color}" '
        f'stroke="{INK}" stroke-width="3" stroke-linejoin="round"/>'
    )


def key(x, y):
    """A big golden key, bow at (x, y), pointing up and right."""
    return (
        f'<g transform="rotate(-35 {x} {y})">'
        f'<rect x="{x + 14}" y="{y - 7}" width="62" height="14" rx="6" fill="{GOLD}" stroke="{INK}" stroke-width="{W}"/>'
        f'<rect x="{x + 58}" y="{y + 5}" width="10" height="18" rx="3" fill="{GOLD}" stroke="{INK}" stroke-width="{W}"/>'
        f'<rect x="{x + 42}" y="{y + 5}" width="10" height="13" rx="3" fill="{GOLD}" stroke="{INK}" stroke-width="{W}"/>'
        f'<circle cx="{x}" cy="{y}" r="22" fill="{GOLD}" stroke="{INK}" stroke-width="{W}"/>'
        f'<circle cx="{x}" cy="{y}" r="8" fill="{GOLD_DARK}" stroke="{INK}" stroke-width="4"/>'
        "</g>"
    )


def card(x, y):
    """A member card with a check mark: signing in."""
    return (
        f'<g transform="rotate(-10 {x} {y})">'
        f'<rect x="{x - 40}" y="{y - 28}" width="80" height="56" rx="9" fill="{BLUE}" stroke="{INK}" stroke-width="{W}"/>'
        f'<circle cx="{x - 18}" cy="{y - 4}" r="11" fill="{PAGE}" stroke="{INK}" stroke-width="3"/>'
        f'<rect x="{x + 2}" y="{y - 12}" width="28" height="6" rx="3" fill="{PAGE}"/>'
        f'<rect x="{x + 2}" y="{y + 1}" width="20" height="6" rx="3" fill="{PAGE}" opacity="0.8"/>'
        f'<circle cx="{x + 34}" cy="{y + 22}" r="15" fill="{GREEN}" stroke="{INK}" stroke-width="{W - 1}"/>'
        f'<path d="M{x + 27} {y + 22} L{x + 32} {y + 28} L{x + 42} {y + 16}" fill="none" stroke="{PAGE}" '
        f'stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>'
        "</g>"
    )


def play_sign(x, y):
    """A round play-button sign on a stick."""
    return (
        f'<rect x="{x - 5}" y="{y + 26}" width="10" height="46" rx="5" fill="#b08455" stroke="{INK}" stroke-width="4"/>'
        f'<circle cx="{x}" cy="{y}" r="32" fill="{RED}" stroke="{INK}" stroke-width="{W}"/>'
        f'<path d="M{x - 10} {y - 16} L{x + 16} {y} L{x - 10} {y + 16} Z" fill="{PAGE}" stroke="{INK}" stroke-width="3" '
        f'stroke-linejoin="round"/>'
    )


def hand(x, y):
    return f'<circle cx="{x}" cy="{y}" r="13" fill="{PAGE}" stroke="{INK}" stroke-width="{W}"/>'


def wave_lines(x, y):
    return (
        f'<path d="M{x} {y - 14} q8 -6 14 0 M{x + 6} {y + 2} q8 -6 14 0" fill="none" stroke="{INK}" stroke-width="4" '
        f'stroke-linecap="round"/>'
    )


POSES = {
    # Step 1: hello, one arm waving.
    "welcome": lambda: [
        limb(52, 176, 30, 214),
        hand(30, 214),
        limb(188, 168, 214, 116),
        hand(214, 112),
        wave_lines(212, 82),
        body("open"),
    ],
    # Step 2: holding up the key (the client ID).
    "client-id": lambda: [
        limb(52, 176, 30, 214),
        hand(30, 214),
        limb(188, 170, 216, 128),
        body("smile"),
        key(222, 118),
        hand(216, 128),
        sparkle(282, 40, 12),
    ],
    # Step 3: showing a member card (signing in).
    "sign-in": lambda: [
        limb(52, 172, 28, 150),
        hand(28, 148),
        limb(188, 186, 214, 176),
        body("smile"),
        card(240, 160),
        hand(214, 176),
    ],
    # Step 4: holding a play sign (playback).
    "playback": lambda: [
        limb(52, 176, 30, 214),
        hand(30, 214),
        limb(188, 174, 226, 150),
        body("open"),
        play_sign(236, 92),
        hand(234, 150),
    ],
    # All set: both arms up, eyes closed with joy.
    "done": lambda: [
        limb(52, 168, 26, 116),
        hand(26, 112),
        limb(188, 168, 214, 116),
        hand(214, 112),
        body("open", eyes="happy"),
        sparkle(26, 60, 14),
        sparkle(250, 70, 13, BLUE),
        sparkle(232, 240, 10, GREEN),
        sparkle(276, 150, 9),
    ],
}


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for name, pose in POSES.items():
        content = "\n  ".join(pose())
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300" viewBox="0 0 300 300">'
            f"{DEFS}\n  {content}\n</svg>\n"
        )
        path = out / f"mascot-{name}.svg"
        path.write_text(svg, encoding="utf-8")
        png = path.with_suffix(".png")
        subprocess.run(["rsvg-convert", "-w", "600", "-h", "600", str(path), "-o", str(png)], check=True)
        print(path, png)


if __name__ == "__main__":
    main()
