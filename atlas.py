"""Build small RGBA PNG sprite atlases from original text pixel art."""
import argparse
import binascii
import html
import json
from pathlib import Path
import re
import struct
import sys
import zlib


def unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError('Clave JSON repetida: ' + key)
        obj[key] = value
    return obj


def load_source(path):
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('La entrada supera 1 MiB')
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_object)


def parse(data):
    if not isinstance(data, dict) or not isinstance(data.get('palette'), dict):
        raise ValueError('Se requiere palette como objeto')
    palette = {'.': (0, 0, 0, 0)}
    for key, color in data['palette'].items():
        if not isinstance(key, str) or len(key) != 1 or key == '.':
            raise ValueError('Cada símbolo debe ser un carácter; el punto está reservado')
        if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{8}', color):
            raise ValueError('Los colores deben usar #RRGGBBAA')
        palette[key] = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5, 7))
    sprites = data.get('sprites')
    if not isinstance(sprites, list) or not 1 <= len(sprites) <= 64:
        raise ValueError('Se requieren de 1 a 64 sprites')
    parsed = []
    names = set()
    for sprite in sprites:
        if not isinstance(sprite, dict):
            raise ValueError('Sprite no válido')
        name, rows = sprite.get('name'), sprite.get('rows')
        if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', name) or name in names:
            raise ValueError('Nombre inválido o duplicado; usa letras ASCII, números, guion y guion bajo')
        names.add(name)
        if not isinstance(rows, list) or not 1 <= len(rows) <= 64 or any(not isinstance(r, str) for r in rows):
            raise ValueError('rows debe contener entre 1 y 64 cadenas')
        width = len(rows[0])
        if not 1 <= width <= 64 or any(len(r) != width for r in rows):
            raise ValueError('Cada sprite debe ser rectangular, de hasta 64 × 64 píxeles')
        if any(ch not in palette for row in rows for ch in row):
            raise ValueError('Símbolo sin color en la paleta')
        pixels = [[palette[ch] for ch in row] for row in rows]
        visible = [(x, y) for y, row in enumerate(pixels) for x, rgba in enumerate(row) if rgba[3] > 0]
        if not visible:
            raise ValueError('No se admiten sprites completamente transparentes')
        left, top = min(x for x, y in visible), min(y for x, y in visible)
        right, bottom = max(x for x, y in visible) + 1, max(y for x, y in visible) + 1
        parsed.append({'name': name, 'sourceSize': {'w': width, 'h': len(rows)},
                       'spriteSourceSize': {'x': left, 'y': top, 'w': right-left, 'h': bottom-top},
                       'pixels': [row[left:right] for row in pixels[top:bottom]]})
    return parsed


def build(data, columns=4, padding=1):
    if type(columns) is not int or not 1 <= columns <= 16:
        raise ValueError('columns debe estar entre 1 y 16')
    if type(padding) is not int or not 0 <= padding <= 8:
        raise ValueError('padding debe estar entre 0 y 8')
    sprites = parse(data)
    columns = min(columns, len(sprites))
    cell_w = max(s['spriteSourceSize']['w'] for s in sprites) + 2 * padding
    cell_h = max(s['spriteSourceSize']['h'] for s in sprites) + 2 * padding
    width, height = columns * cell_w, ((len(sprites) + columns - 1) // columns) * cell_h
    canvas = bytearray(width * height * 4)
    frames = {}
    for i, sprite in enumerate(sprites):
        x, y = (i % columns) * cell_w + padding, (i // columns) * cell_h + padding
        pixels = sprite['pixels']
        for dy, row in enumerate(pixels):
            for dx, rgba in enumerate(row):
                # Normalize invisible RGB so transparent margins are reproducible.
                rgba = rgba if rgba[3] else (0, 0, 0, 0)
                offset = ((y + dy) * width + x + dx) * 4
                canvas[offset:offset + 4] = bytes(rgba)
        frames[sprite['name']] = {'frame': {'x': x, 'y': y, 'w': len(pixels[0]), 'h': len(pixels)},
            'sourceSize': sprite['sourceSize'], 'spriteSourceSize': sprite['spriteSourceSize'],
            'trimmed': sprite['sourceSize'] != {'w': len(pixels[0]), 'h': len(pixels)}, 'rotated': False}
    metadata = {'format': 'pixel-sprite-atlas-v1', 'image': 'atlas.png',
                'size': {'w': width, 'h': height}, 'padding': padding, 'frames': frames}
    return width, height, bytes(canvas), metadata


def chunk(kind, payload):
    return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', binascii.crc32(kind + payload) & 0xffffffff)


def png_bytes(width, height, rgba):
    if len(rgba) != width * height * 4:
        raise ValueError('Tamaño RGBA incorrecto')
    raw = b''.join(b'\x00' + rgba[y * width * 4:(y + 1) * width * 4] for y in range(height))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def svg_preview(width, height, rgba):
    shapes = []
    for y in range(height):
        for x in range(width):
            r, g, b, a = rgba[(y * width + x) * 4:(y * width + x + 1) * 4]
            if a:
                shapes.append(f'<rect x="{x}" y="{y}" width="1" height="1" fill="#{r:02x}{g:02x}{b:02x}" fill-opacity="{a/255:.6f}"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width*8}" height="{height*8}" viewBox="0 0 {width} {height}" shape-rendering="crispEdges">'
            '<title>' + html.escape('Atlas de sprites · vista ampliada 8×') + '</title>' + ''.join(shapes) + '</svg>\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--out-dir', type=Path, default=Path('build'))
    parser.add_argument('--columns', type=int, default=4)
    parser.add_argument('--padding', type=int, default=1)
    args = parser.parse_args(argv)
    try:
        targets = [args.out_dir / name for name in ('atlas.png', 'atlas.json', 'preview.svg')]
        if args.source.resolve() in [p.resolve() for p in targets]:
            raise ValueError('La salida no puede sobrescribir la entrada')
        w, h, pixels, meta = build(load_source(args.source), args.columns, args.padding)
        encoded_png = png_bytes(w, h, pixels)
        encoded_json = json.dumps(meta, ensure_ascii=False, indent=2) + '\n'
        encoded_svg = svg_preview(w, h, pixels)
        args.out_dir.mkdir(parents=True, exist_ok=True)
        targets[0].write_bytes(encoded_png)
        targets[1].write_text(encoded_json, encoding='utf-8')
        targets[2].write_text(encoded_svg, encoding='utf-8')
        print(f'{len(meta["frames"])} sprites · {w} × {h} píxeles · {args.out_dir}')
        return 0
    except (ValueError, OSError, RecursionError) as exc:
        print('Error: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
