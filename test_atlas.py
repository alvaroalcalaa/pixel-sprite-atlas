import binascii
import copy
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from atlas import build, load_source, parse, png_bytes, svg_preview


def source(rows=None):
    return {'palette': {'X': '#FF8040FF', 's': '#10203080'},
            'sprites': [{'name': 'hero', 'rows': rows or ['....', '.Xs.', '....']}]}


class AtlasTests(unittest.TestCase):
    def test_trim_and_original_position(self):
        w, h, rgba, m = build(source(), 1, 1)
        f = m['frames']['hero']
        self.assertEqual((w, h), (4, 3))
        self.assertEqual(f['sourceSize'], {'w': 4, 'h': 3})
        self.assertEqual(f['spriteSourceSize'], {'x': 1, 'y': 1, 'w': 2, 'h': 1})
        self.assertEqual(f['frame'], {'x': 1, 'y': 1, 'w': 2, 'h': 1})
        self.assertEqual(rgba[(1*w+1)*4:(1*w+3)*4], bytes([255,128,64,255,16,32,48,128]))

    def test_padding_is_transparent(self):
        w, h, rgba, _ = build(source(), 1, 1)
        self.assertEqual(rgba[:w*4], bytes(w*4))
        self.assertEqual(rgba[-w*4:], bytes(w*4))

    def test_packing_no_overlap(self):
        d = source(['XX', 'XX'])
        d['sprites'] += [{'name': str(i), 'rows': ['X']} for i in range(5)]
        w, h, _, m = build(d, 3, 2)
        occupied = set()
        for sprite in m['frames'].values():
            f = sprite['frame']
            cells = {(x, y) for x in range(f['x'], f['x']+f['w']) for y in range(f['y'], f['y']+f['h'])}
            self.assertFalse(occupied & cells)
            self.assertTrue(all(0 <= x < w and 0 <= y < h for x,y in cells))
            occupied |= cells
        self.assertEqual((w,h), (18,12))

    def test_zero_padding_and_no_trim(self):
        w,h,_,m = build(source(['XX','XX']),1,0)
        self.assertEqual((w,h),(2,2))
        self.assertFalse(m['frames']['hero']['trimmed'])

    def test_fully_transparent_rejected(self):
        for rows in (['..'], ['XX']):
            d=source(rows)
            if rows==['XX']:
                d['palette']['X']='#FF000000'
            with self.assertRaises(ValueError):
                parse(d)

    def test_invisible_rgb_normalized(self):
        d=source(['XXX'])
        d['palette']['z']='#FF000000'
        d['sprites'][0]['rows']=['XzX']
        _,_,rgba,_=build(d,1,0)
        self.assertEqual(rgba[4:8],bytes(4))

    def test_png_crc_dimensions_and_exact_rgba(self):
        w,h,rgba,_=build(source(),1,1)
        png=png_bytes(w,h,rgba)
        self.assertEqual(png[:8],b'\x89PNG\r\n\x1a\n')
        offset=8
        chunks={}
        while offset<len(png):
            size=struct.unpack('>I',png[offset:offset+4])[0]
            kind=png[offset+4:offset+8]
            data=png[offset+8:offset+8+size]
            crc=struct.unpack('>I',png[offset+8+size:offset+12+size])[0]
            self.assertEqual(crc,binascii.crc32(kind+data)&0xffffffff)
            chunks[kind]=data
            offset+=12+size
        self.assertEqual(struct.unpack('>IIBBBBB',chunks[b'IHDR']),(w,h,8,6,0,0,0))
        raw=zlib.decompress(chunks[b'IDAT'])
        stride=1+w*4
        self.assertTrue(all(raw[y*stride]==0 for y in range(h)))
        self.assertEqual(b''.join(raw[y*stride+1:(y+1)*stride] for y in range(h)),rgba)
        self.assertIn(b'IEND',chunks)

    def test_repeatable_and_preview_alpha(self):
        self.assertEqual(build(source()),build(source()))
        w,h,rgba,_=build(source())
        svg=svg_preview(w,h,rgba)
        self.assertIn('fill-opacity="0.501961"',svg)
        self.assertIn('shape-rendering="crispEdges"',svg)

    def test_unknown_symbol_and_bad_rows(self):
        for rows in (['X?'], ['XX','X'], [''], [1]):
            with self.assertRaises(ValueError):
                parse(source(rows))

    def test_palette_restrictions(self):
        for palette in ({'.':'#FFFFFFFF'},{'XX':'#FFFFFFFF'},{'X':'red'}):
            d=source()
            d['palette']=palette
            with self.assertRaises(ValueError):
                parse(d)

    def test_duplicate_names(self):
        d=source()
        d['sprites'].append(copy.deepcopy(d['sprites'][0]))
        with self.assertRaises(ValueError):
            parse(d)

    def test_argument_limits(self):
        for c,p in ((0,1),(17,1),(1,9),(True,1)):
            with self.assertRaises(ValueError):
                build(source(),c,p)

    def test_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'bad.json'
            p.write_text('{"palette":{},"palette":{}}')
            with self.assertRaises(ValueError):
                load_source(p)

    def test_cli_exports_and_input_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            p=root/'source.json'
            original=json.dumps(source())
            p.write_text(original)
            cli=[sys.executable,str(Path(__file__).with_name('atlas.py')),str(p),'--out-dir',str(root/'out')]
            r=subprocess.run(cli,capture_output=True)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual(json.loads((root/'out/atlas.json').read_text())['image'],'atlas.png')
            self.assertTrue((root/'out/preview.svg').exists())
            p=root/'out/atlas.json'
            p.write_text(original)
            cli[2]=str(p)
            self.assertEqual(subprocess.run(cli,capture_output=True).returncode,2)
            self.assertEqual(p.read_text(),original)


if __name__=='__main__':
    unittest.main()
