#!/usr/bin/env python3
"""Draw overview sheets of a run's images, labelled with their asset IDs.

    contact_sheet.py RUN_DIR [--per-sheet 12] [--tile 360]

Writes RUN_DIR/source/contact-sheet-01.png, -02.png, ... from the images listed
in RUN_DIR/input.json (never from a file-name pattern). A sheet is only for
choosing which figures to open one by one; it is not a substitute for looking
at each figure you describe.

Needs Pillow. Without it, prints a note and exits 3 so the caller can open the
images individually instead. An image that cannot be decoded is drawn as a
labelled "unreadable" tile and listed on stdout, never left blank silently.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("run", type=Path)
    parser.add_argument("--per-sheet", type=int, default=12)
    parser.add_argument("--tile", type=int, default=360)
    args = parser.parse_args(argv)
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("contact_sheet: Pillow is not installed; open the images one by one instead", file=sys.stderr)
        return 3

    run = args.run.expanduser()
    record = json.loads((run / "input.json").read_text(encoding="utf-8"))
    assets, seen = [], set()
    for entry in record["images"]:
        if entry.get("status") == "resolved" and entry["asset_id"] not in seen:
            seen.add(entry["asset_id"])
            assets.append(entry)
    if not assets:
        print("contact_sheet: no resolved images")
        return 0

    tile, label_h, cols = args.tile, 22, 4
    font = ImageFont.load_default()
    unreadable = []
    sheets = []
    for start in range(0, len(assets), args.per_sheet):
        chunk = assets[start:start + args.per_sheet]
        rows = (len(chunk) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * tile, rows * (tile + label_h)), "white")
        draw = ImageDraw.Draw(sheet)
        for index, entry in enumerate(chunk):
            x, y = (index % cols) * tile, (index // cols) * (tile + label_h)
            label = f"{entry['asset_id']}  line {entry['line']}"
            try:
                with Image.open(run / entry["snapshot"]) as image:
                    image = image.convert("RGB")
                    image.thumbnail((tile - 8, tile - 8))
                    sheet.paste(image, (x + (tile - image.width) // 2, y + label_h + (tile - image.height) // 2))
            except Exception:  # noqa: BLE001 - any decode failure is reported
                unreadable.append(entry["asset_id"])
                label += "  UNREADABLE"
                draw.rectangle([x + 4, y + label_h + 4, x + tile - 4, y + label_h + tile - 4], outline="red")
            draw.text((x + 4, y + 4), label, fill="black", font=font)
            draw.rectangle([x, y, x + tile - 1, y + tile + label_h - 1], outline="#bbbbbb")
        path = run / "source" / f"contact-sheet-{len(sheets) + 1:02d}.png"
        sheet.save(path)
        sheets.append(path)
    for path in sheets:
        print(path)
    if unreadable:
        print("unreadable: " + ", ".join(unreadable))
    return 0


if __name__ == "__main__":
    sys.exit(main())
