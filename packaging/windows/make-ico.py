"""Makes AniCal.ico from a 256×256 PNG of the app icon (Pillow).

Usage: python make-ico.py ICON-256.png OUT.ico
"""

import sys

from PIL import Image

SIZES = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)]

Image.open(sys.argv[1]).convert("RGBA").save(sys.argv[2], sizes=SIZES)
