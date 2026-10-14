"""crop.py's guarantees. Run with: python3 -m unittest discover -s skills/ado-evidence/tests

Every input PNG here is written by this file's own encoder, which imports nothing from crop.py, and what crop.py
writes is read back by a decoder of this file's own, so neither side is graded by its own rules. The tests in
InTheBrowser capture pages with a real headless Chrome, and skip where none is found.
"""
import base64, io, json, os, random, shutil, struct, subprocess, sys, tempfile, threading, time, tracemalloc, unittest, zlib
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "ado-evidence" / "scripts"
CROP = SCRIPTS / "crop.py"
sys.path.insert(0, str(SCRIPTS))
import crop  # noqa: E402

SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
WHITE, BLACK = b"\xff\xff\xff\xff", b"\x00\x00\x00\xff"


# --- The test's own PNG writer: any colour type and depth, a filter type per row, and every way a file can be wrong.

def chunk(kind, body, bad_crc=False):
    crc = zlib.crc32(kind + body) ^ (0xFFFFFFFF if bad_crc else 0)
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)


def pack_row(samples, depth):
    """One scanline's samples packed at depth bits each, most significant first, the last byte padded."""
    if depth == 8:
        return bytes(samples)
    if depth == 16:
        return b"".join(struct.pack(">H", s) for s in samples)
    out, acc, n = bytearray(), 0, 0
    for s in samples:
        acc, n = (acc << depth) | s, n + depth
        if n == 8:
            out.append(acc)
            acc = n = 0
    if n:
        out.append(acc << (8 - n))
    return bytes(out)


def predict(kind, raw, prev, bpp, i):
    """The prediction PNG filter kind makes for byte i of a row, from the row's own earlier bytes and the row above."""
    a = raw[i - bpp] if i >= bpp else 0
    b = prev[i]
    c = prev[i - bpp] if i >= bpp else 0
    if kind == 0:
        return 0
    if kind == 1:
        return a
    if kind == 2:
        return b
    if kind == 3:
        return (a + b) >> 1
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else b if pb <= pc else c


def filtered(rows, bpp, filters):
    """The scanlines as PNG stores them: a filter byte, then each byte less its prediction. filters is one filter type
    for every row, or one per row."""
    out, prev = bytearray(), bytes(len(rows[0]))
    for y, raw in enumerate(rows):
        kind = filters[y] if isinstance(filters, (list, tuple)) else filters
        out.append(kind)
        out += bytes((raw[i] - predict(kind, raw, prev, bpp, i)) & 255 for i in range(len(raw)))
        prev = raw
    return bytes(out)


def png(pixels, color=6, depth=8, filters=0, palette=None, trns=None, interlace=0, extras=(), bad_crc=(), split=False,
        size=None, idat=None):
    """A PNG of pixels, rows of per-pixel sample tuples at depth bits. extras are (kind, body) chunks written before
    IDAT; bad_crc names the kinds whose CRC is written wrong; split cuts the IDAT in two at an odd byte; size is an IHDR
    size other than the pixels' own; idat replaces the compressed pixel data."""
    height, width = len(pixels), len(pixels[0])
    rows = [pack_row([s for px in row for s in px], depth) for row in pixels]
    bpp = max(1, CHANNELS[color] * depth // 8)
    data = zlib.compress(filtered(rows, bpp, filters)) if idat is None else idat
    w, h = size or (width, height)
    out = SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, depth, color, 0, 0, interlace), b"IHDR" in bad_crc)
    if palette is not None:
        out += chunk(b"PLTE", b"".join(bytes(c) for c in palette), b"PLTE" in bad_crc)
    if trns is not None:
        out += chunk(b"tRNS", trns, b"tRNS" in bad_crc)
    for kind, body in extras:
        out += chunk(kind, body, kind in bad_crc)
    if split:
        cut = len(data) // 2 | 1
        out += chunk(b"IDAT", data[:cut], b"IDAT" in bad_crc) + chunk(b"IDAT", data[cut:])
    else:
        out += chunk(b"IDAT", data, b"IDAT" in bad_crc)
    return out + chunk(b"IEND", b"", b"IEND" in bad_crc)


def rgba_of(pixels, color, depth, palette=None, trns=None):
    """What crop.py should make of pixels: 8-bit RGBA rows, read as the PNG specification reads them. A 16-bit
    sample keeps its high byte, a shallower gray sample is scaled to 255, a palette entry past tRNS is opaque, and a
    gray or RGB pixel equal to the tRNS colour key is transparent."""
    top = (1 << depth) - 1
    key = struct.unpack(">" + "H" * (len(trns) // 2), trns) if trns is not None and color in (0, 2) else None

    def sample8(s):
        return s >> 8 if depth == 16 else s * 255 // top
    out = []
    for row in pixels:
        line = bytearray()
        for px in row:
            if color == 3:
                r, g, b = palette[px[0]]
                a = trns[px[0]] if trns is not None and px[0] < len(trns) else 255
            elif color == 0:
                r = g = b = sample8(px[0])
                a = 255
            elif color == 2:
                (r, g, b), a = map(sample8, px), 255
            elif color == 4:
                r = g = b = sample8(px[0])
                a = sample8(px[1])
            else:
                r, g, b, a = map(sample8, px)
            if key is not None and tuple(px) == key:
                a = 0
            line += bytes((r, g, b, a))
        out.append(bytes(line))
    return out


def sample_rows(width, height, color, depth, rnd, choices=None):
    """Random pixels: per-pixel sample tuples, each sample below choices (the palette's size) or the depth's top."""
    limit = choices or (1 << depth)
    return [[tuple(rnd.randrange(limit) for _ in range(CHANNELS[color])) for _ in range(width)] for _ in range(height)]


# --- The test's own reader for what crop.py writes: 8-bit RGB or RGBA, not interlaced, every filter.

def chunk_kinds(data):
    """The chunk types of a PNG, in order."""
    kinds, pos = [], 8
    while pos < len(data):
        n = int.from_bytes(data[pos:pos + 4], "big")
        kinds.append(data[pos + 4:pos + 8])
        pos += 12 + n
    return kinds


def read(data):
    """(width, height, colour type, RGBA rows) of a PNG crop.py wrote, each row widened to RGBA for comparison."""
    assert data[:8] == SIGNATURE, data[:8]
    pos, idat, header = 8, b"", None
    while pos < len(data):
        n = int.from_bytes(data[pos:pos + 4], "big")
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
        assert zlib.crc32(kind + body) == int.from_bytes(data[pos + 8 + n:pos + 12 + n], "big"), kind
        pos += 12 + n
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
    width, height, depth, color, _, _, interlace = header
    assert depth == 8 and color in (2, 6) and not interlace, header
    bpp = 3 if color == 2 else 4
    stride, raw, rows, prev = width * bpp, zlib.decompress(idat), [], bytes(width * bpp)
    assert len(raw) == height * (stride + 1), (len(raw), height, stride)
    for y in range(height):
        start = y * (stride + 1)
        kind, line = raw[start], bytearray(raw[start + 1:start + 1 + stride])
        for i in range(stride) if kind else ():
            line[i] = (line[i] + predict(kind, line, prev, bpp, i)) & 255
        if color == 2:
            widened = bytearray(width * 4)
            widened[0::4], widened[1::4], widened[2::4], widened[3::4] = line[0::3], line[1::3], line[2::3], b"\xff" * width
            line = widened
        rows.append(bytes(line))
        prev = line
    return width, height, color, rows


def written(path, data):
    path.write_bytes(data)
    return path


# --- Reading

class Reads(unittest.TestCase):
    """read_png gives 8-bit RGBA rows for every colour type and depth PNG defines, at width 13 so a sub-byte row has
    padding bits, with every filter undone."""

    def decode(self, data, **limits):
        with tempfile.TemporaryDirectory() as tmp:
            return crop.read_png(written(Path(tmp) / "in.png", data), **limits)

    def test_reads_each_colour_type_and_depth(self):
        rnd = random.Random(7)
        for color, depths in ((0, (1, 2, 4, 8, 16)), (2, (8, 16)), (3, (1, 2, 4, 8)), (4, (8, 16)), (6, (8, 16))):
            for depth in depths:
                with self.subTest(color=color, depth=depth):
                    palette = trns = None
                    if color == 3:
                        palette = [tuple(rnd.randrange(256) for _ in range(3)) for _ in range(min(1 << depth, 200))]
                        trns = bytes(rnd.randrange(256) for _ in range(min(3, len(palette))))  # shorter than the palette
                    pixels = sample_rows(13, 5, color, depth, rnd, palette and len(palette))
                    if color in (0, 2):
                        trns = b"".join(struct.pack(">H", s) for s in pixels[2][3])  # one pixel's colour is the key
                    data = png(pixels, color, depth, filters=[y % 5 for y in range(5)], palette=palette, trns=trns)
                    image = self.decode(data)
                    self.assertEqual((image.width, image.height), (13, 5))
                    self.assertEqual(image.rows, rgba_of(pixels, color, depth, palette, trns))

    def test_each_filter_undoes_at_every_bytes_per_pixel(self):
        rnd = random.Random(11)
        for color, depth in ((0, 8), (4, 8), (2, 8), (6, 8), (2, 16), (6, 16)):  # 1, 2, 3, 4, 6 and 8 bytes a pixel
            pixels = sample_rows(9, 4, color, depth, rnd)
            for filters in (0, 1, 2, 3, 4, [4, 3, 2, 1]):
                with self.subTest(color=color, depth=depth, filters=filters):
                    self.assertEqual(self.decode(png(pixels, color, depth, filters=filters)).rows, rgba_of(pixels, color, depth))

    def test_the_fast_paths_match_the_plain_loop(self):
        for n in (1, 2, 3, 7, 8, 9, 100, 4095, 4096, 4097, 10241):
            line, prev = os.urandom(n), os.urandom(n)
            with self.subTest(n=n):
                self.assertEqual(crop.undo_up(bytearray(line), prev), crop.undo(2, bytearray(line), prev, 1))
                for bpp in range(1, 9):
                    self.assertEqual(crop.undo_sub(bytearray(line), bpp), crop.undo(1, bytearray(line), prev, bpp))

    def test_decodes_only_the_rows_and_columns_a_box_needs(self):
        pixels = sample_rows(13, 7, 6, 8, random.Random(3))
        data = png(pixels, filters=[4, 3, 2, 1, 0, 4, 3])
        whole = self.decode(data)
        with patch.object(crop, "undo", wraps=crop.undo) as plain, patch.object(crop, "undo_up", wraps=crop.undo_up) as up, \
                patch.object(crop, "undo_sub", wraps=crop.undo_sub) as sub:
            part = self.decode(data, rows_needed=3, cols_needed=5)
        # three rows were unfiltered (Paeth, Average, Up), each of the 20 bytes that hold five pixels, and no more
        self.assertEqual([len(c.args[1]) for c in plain.call_args_list], [20, 20])
        self.assertEqual([len(c.args[0]) for c in up.call_args_list], [20])
        self.assertEqual(sub.call_args_list, [])
        self.assertEqual((part.width, part.height), (5, 3))
        self.assertEqual(part.rows, [row[:5 * 4] for row in whole.rows[:3]])
        beyond = self.decode(data, rows_needed=70, cols_needed=50)  # more than there is: the whole image
        self.assertEqual((beyond.width, beyond.height, beyond.rows), (13, 7, whole.rows))

    def test_an_idat_split_at_an_odd_byte_is_read(self):
        pixels = sample_rows(13, 5, 6, 8, random.Random(5))
        self.assertEqual(self.decode(png(pixels, split=True)).rows, rgba_of(pixels, 6, 8))

    def test_colour_chunks_are_kept_for_colour_sources_only(self):
        gama, srgb, chrm = (b"gAMA", struct.pack(">I", 45455)), (b"sRGB", b"\0"), (b"cHRM", bytes(32))
        phys, iccp, text = (b"pHYs", struct.pack(">IIB", 2835, 2835, 1)), (b"iCCP", b"p\0\0" + zlib.compress(b"x")), (b"tEXt", b"k\0v")
        rnd = random.Random(9)
        for color, depth, kept in ((2, 8, [gama, srgb, chrm, phys, iccp]), (6, 16, [gama, srgb, chrm, phys, iccp]),
                                   (3, 8, [gama, srgb, chrm, phys, iccp]), (0, 8, [gama, srgb, chrm, phys]), (4, 8, [gama, srgb, chrm, phys])):
            with self.subTest(color=color):
                palette = [(1, 2, 3), (4, 5, 6)] if color == 3 else None
                pixels = sample_rows(4, 2, color, depth, rnd, palette and len(palette))
                image = self.decode(png(pixels, color, depth, palette=palette, extras=[gama, srgb, chrm, phys, iccp, text]))
                self.assertEqual(image.extras, kept)


class Refuses(unittest.TestCase):
    """A file that is not a non-interlaced PNG, or is damaged or crafted, is refused with one line that says so, and
    never costs more memory than its header declares."""

    def refusal(self, data, **limits):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(crop.Problem) as caught:
                crop.read_png(written(Path(tmp) / "in.png", data), **limits)
        message = str(caught.exception)
        self.assertNotIn("\n", message)
        return message

    def test_refuses_what_is_not_png_and_says_what_it_is(self):
        self.assertIn("JPEG", self.refusal(b"\xff\xd8\xff\xe0" + bytes(20)))
        self.assertIn("GIF", self.refusal(b"GIF89a" + bytes(20)))
        self.assertIn("BMP", self.refusal(b"BM" + bytes(20)))
        self.assertIn("WebP", self.refusal(b"RIFF\0\0\0\0WEBPVP8 " + bytes(20)))
        self.assertIn("empty", self.refusal(b""))
        for data in (b"\xff\xd8\xff\xe0" + bytes(20), b"hello world, not an image at all"):
            self.assertIn("save the screenshot as PNG", self.refusal(data))

    def test_refuses_interlace(self):
        pixels = sample_rows(4, 4, 6, 8, random.Random(1))
        self.assertIn("interlaced", self.refusal(png(pixels, interlace=1)))

    def test_refuses_a_combination_png_does_not_define(self):
        for color, depth in ((2, 4), (6, 4), (3, 16), (4, 4), (1, 8), (0, 3)):
            with self.subTest(color=color, depth=depth), self.assertRaises(crop.Problem):
                crop.header(struct.pack(">IIBBBBB", 4, 4, depth, color, 0, 0, 0))
        with self.assertRaises(crop.Problem):
            crop.header(struct.pack(">IIBBBBB", 0, 4, 8, 6, 0, 0, 0))  # no pixels

    def test_a_bad_crc_refuses_a_critical_chunk_and_drops_an_ancillary_one(self):
        palette = [(1, 2, 3), (4, 5, 6)]
        pixels = sample_rows(4, 2, 3, 8, random.Random(2), len(palette))
        for kind in (b"IHDR", b"PLTE", b"IDAT", b"IEND"):
            with self.subTest(kind=kind):
                message = self.refusal(png(pixels, 3, 8, palette=palette, bad_crc=(kind,)))
                self.assertIn(kind.decode(), message)
                self.assertIn("CRC", message)
        gama, text = (b"gAMA", struct.pack(">I", 45455)), (b"tEXt", b"k\0v")
        with tempfile.TemporaryDirectory() as tmp:
            data = png(pixels, 3, 8, palette=palette, extras=[gama, text], bad_crc=(b"gAMA", b"tEXt"))
            image = crop.read_png(written(Path(tmp) / "in.png", data))
        self.assertEqual(image.rows, rgba_of(pixels, 3, 8, palette))
        self.assertEqual(image.extras, [])

    def test_refuses_a_truncated_file(self):
        pixels = sample_rows(13, 5, 6, 8, random.Random(4))
        data = png(pixels)
        for cut in (len(data) - 1, len(data) - 20, 20):
            with self.subTest(cut=cut):
                self.assertIn("cut short", self.refusal(data[:cut]))
        rows = [pack_row([s for px in row for s in px], 8) for row in pixels]
        short = zlib.compress(filtered(rows[:-1], 4, 0))  # a complete stream one row short
        self.assertIn("cut short", self.refusal(png(pixels, idat=short)))
        cut_stream = zlib.compress(filtered(rows, 4, 0))[:-6]  # a stream that stops before its end
        self.assertIn("cut short", self.refusal(png(pixels, idat=cut_stream)))

    def test_a_chunk_named_with_control_characters_is_still_refused_on_one_line(self):
        data = png(sample_rows(4, 4, 6, 8, random.Random(14)))
        cut = data[:33] + struct.pack(">I", 99) + b"\n\x00\r\t" + b"xyz"  # a chunk of 99 bytes, named in control characters, cut short
        message = self.refusal(cut)
        self.assertIn("cut short", message)
        self.assertEqual(len(message.splitlines()), 1)
        self.assertNotIn("\r", message)

    def test_bounds_an_inflate_bomb(self):
        pixels = sample_rows(64, 64, 6, 8, random.Random(6))
        bomb = zlib.compress(bytes(50_000_000), 9)  # 50 MB of zeros, under 50 KB compressed
        tracemalloc.start()
        try:
            message = self.refusal(png(pixels, idat=bomb))
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertIn("longer than its header declares", message)
        self.assertLess(peak, 4 * 1024 * 1024)

    def test_refuses_an_oversized_header_before_allocating(self):
        pixels = sample_rows(4, 4, 6, 8, random.Random(8))
        started = time.monotonic()
        message = self.refusal(png(pixels, size=(100_000, 100_000)))
        self.assertLess(time.monotonic() - started, 1)
        self.assertIn("100000 x 100000", message)
        self.assertIn("larger than", message)
        # A 1-bit image packs 32 pixels into the bytes one RGBA pixel takes: the cap is on what decoding makes of it.
        started = time.monotonic()
        message = self.refusal(png([[(0,)] * 8] * 2, 0, 1, size=(65535, 32000)))
        self.assertLess(time.monotonic() - started, 1)
        self.assertIn("65535 x 32000", message)
        self.assertIn("megapixels", message)
        with self.assertRaises(crop.Problem):
            crop.header(struct.pack(">IIBBBBB", 8000, 6000, 8, 2, 0, 0, 0))  # 48 megapixels of RGB
        crop.header(struct.pack(">IIBBBBB", 7680, 4320, 8, 6, 0, 0, 0))  # 8K is read
        crop.header(struct.pack(">IIBBBBB", 1200, 30000, 8, 6, 0, 0, 0))  # and a full-page capture of a long page
        # Each row and each column costs memory of its own, so a side is capped too: 1 x 40,000,000 is inside the
        # pixel cap and cost gigabytes.
        for width, height in ((1, 40_000_000), (40_000_000, 1), (65536, 600), (600, 65536)):
            with self.subTest(size=(width, height)), self.assertRaises(crop.Problem) as caught:
                crop.header(struct.pack(">IIBBBBB", width, height, 1, 0, 0, 0, 0))
            self.assertIn("65535", str(caught.exception))
        crop.header(struct.pack(">IIBBBBB", 65535, 600, 1, 0, 0, 0, 0))

    def test_chunks_cost_nothing_past_the_ones_an_image_needs(self):
        # A 1 x 1 image followed by 200,000 empty tEXt chunks and 200,000 gAMA chunks: none of them is kept, and only
        # the first gAMA reaches the output. Past a million chunks the file is refused, which no image needs.
        pixels = [[(1, 2, 3, 255)]]
        spam = chunk(b"tEXt", b"") * 200_000 + chunk(b"gAMA", struct.pack(">I", 45455)) + chunk(b"gAMA", struct.pack(">I", 1)) * 200_000
        data = png(pixels)
        data = data[:-12] + spam + data[-12:]  # before IEND
        with tempfile.TemporaryDirectory() as tmp:
            source = written(Path(tmp) / "spam.png", data)
            tracemalloc.start()
            try:
                image = crop.read_png(source)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            self.assertEqual(image.rows, rgba_of(pixels, 6, 8))
            self.assertEqual(image.extras, [(b"gAMA", struct.pack(">I", 45455))])
            self.assertLess(peak, 4 * len(data))
            too_many = png(pixels)
            too_many = too_many[:-12] + chunk(b"tEXt", b"") * (crop.CHUNKS + 1) + too_many[-12:]
            with self.assertRaises(crop.Problem) as caught:
                crop.read_png(written(Path(tmp) / "more.png", too_many))
            self.assertIn("chunks", str(caught.exception))
        # IDAT in many pieces is still one stream
        rows = [pack_row([s for px in row for s in px], 8) for row in sample_rows(13, 5, 6, 8, random.Random(17))]
        stream = zlib.compress(filtered(rows, 4, 0))
        pieces = b"".join(chunk(b"IDAT", stream[i:i + 7]) for i in range(0, len(stream), 7))
        many = SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", 13, 5, 8, 6, 0, 0, 0)) + pieces + chunk(b"IEND", b"")
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(len(crop.read_png(written(Path(tmp) / "pieces.png", many)).rows), 5)

    def test_refuses_a_file_past_the_limit_without_reading_it_all(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(crop, "LIMIT", 100_000):
            big = written(Path(tmp) / "big.png", SIGNATURE + bytes(200_000))
            with self.assertRaises(crop.Problem) as caught:
                crop.load(big)
            self.assertIn("larger than", str(caught.exception))
            if not hasattr(os, "mkfifo"):
                return
            endless = Path(tmp) / "endless.png"  # a pipe: a size of zero, and no end
            os.mkfifo(endless)

            def feed():
                try:
                    with open(endless, "wb") as f:
                        while True:
                            f.write(bytes(65536))
                except OSError:
                    pass
            threading.Thread(target=feed, daemon=True).start()
            started = time.monotonic()
            with self.assertRaises(crop.Problem) as caught:
                crop.load(endless)
            self.assertLess(time.monotonic() - started, 5)
            self.assertIn("larger than", str(caught.exception))

    def test_refuses_a_palette_index_past_the_palette_and_a_missing_palette(self):
        palette = [(1, 2, 3), (4, 5, 6)]
        pixels = [[(0,), (1,), (3,), (1,)], [(0,), (0,), (0,), (0,)]]
        self.assertIn("palette", self.refusal(png(pixels, 3, 2, palette=palette)))
        self.assertIn("palette", self.refusal(png(pixels, 3, 2)))


# --- Writing, boxing and squeezing

def image_of(rows):
    """An Image of RGBA rows, each bytes of the same width."""
    return crop.Image(len(rows[0]) // 4, len(rows), list(rows), [])


def blank(n, width, colour=WHITE):
    return [colour * width] * n


def text_rows(n, width, x0=0, x1=None, y0=0, ink=BLACK, paper=WHITE):
    """n rows of text-like content between columns x0 and x1: a deterministic pattern in which every row differs from
    the one before it and every column from the one before it, as anti-aliased text does."""
    x1 = width if x1 is None else x1
    return [b"".join(ink if x0 <= x < x1 and (x * 7 + (y0 + y) * 13) % 5 == 0 else paper for x in range(width))
            for y in range(n)]


def squeezed_by_hand(rows, gap, row_gap=None):
    """The squeeze rule applied the slow, obvious way: of each run of identical rows the first row_gap (gap, unless
    given) are kept, then of each run of identical columns the first gap; returns (rows, height, width)."""
    def kept(items, limit):
        out, run = [], 0
        for i, item in enumerate(items):
            run = run + 1 if i and item == items[i - 1] else 1
            if run <= limit:
                out.append(i)
        return out
    rows = [rows[i] for i in kept(rows, gap if row_gap is None else row_gap)]
    width = len(rows[0]) // 4
    columns = [b"".join(row[x * 4:(x + 1) * 4] for row in rows) for x in range(width)]
    keep = kept(columns, gap)
    return [b"".join(row[x * 4:(x + 1) * 4] for x in keep) for row in rows], len(rows), len(keep)


class Writes(unittest.TestCase):
    """encode_png writes 8-bit RGB, or RGBA where any pixel is not opaque, with the colour chunks carried over right
    after the header, and write() replaces a file whole or not at all."""

    def test_output_is_rgb_unless_alpha_is_below_255(self):
        rows = rgba_of(sample_rows(13, 5, 2, 8, random.Random(12)), 2, 8)
        self.assertEqual(read(crop.encode_png(image_of(rows))), (13, 5, 2, rows))
        clear = list(rows)
        clear[2] = clear[2][:4 * 4] + bytes([1, 2, 3, 254]) + clear[2][5 * 4:]
        self.assertEqual(read(crop.encode_png(image_of(clear))), (13, 5, 6, clear))

    def test_colour_chunks_are_written_after_the_header_with_their_crcs(self):
        extras = [(b"gAMA", struct.pack(">I", 45455)), (b"iCCP", b"p\0\0" + zlib.compress(b"x"))]
        data = crop.encode_png(crop.Image(2, 1, [bytes([1, 2, 3, 255, 4, 5, 6, 255])], extras))
        self.assertEqual(chunk_kinds(data), [b"IHDR", b"gAMA", b"iCCP", b"IDAT", b"IEND"])
        self.assertEqual(read(data)[3], [bytes([1, 2, 3, 255, 4, 5, 6, 255])])  # and every CRC checks

    def test_write_replaces_a_file_whole_or_not_at_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.png"
            target.write_bytes(b"before")
            crop.write(target, b"after")
            self.assertEqual(target.read_bytes(), b"after")
            with self.assertRaises(TypeError):
                crop.write(target, None)  # a write that fails part way
            self.assertEqual(target.read_bytes(), b"after")
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["out.png"])  # and no part file is left
            with self.assertRaises(crop.Problem) as caught:
                crop.write(Path(tmp) / "missing" / "out.png", b"x")
            self.assertIn("cannot write", str(caught.exception))

    def test_write_gives_the_file_the_permissions_an_ordinary_file_gets(self):
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "plain.png"
            plain.write_bytes(b"x")
            crop.write(Path(tmp) / "out.png", b"x")
            self.assertEqual((Path(tmp) / "out.png").stat().st_mode & 0o777, plain.stat().st_mode & 0o777)

    def test_write_keeps_the_permissions_of_a_file_it_replaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            private = Path(tmp) / "private.png"
            private.write_bytes(b"before")
            os.chmod(private, 0o600)
            before = private.stat().st_mode & 0o777
            crop.write(private, b"after")
            self.assertEqual((private.read_bytes(), private.stat().st_mode & 0o777), (b"after", before))

    @unittest.skipIf(os.name == "nt", "Windows limits the whole path, not the name")
    def test_write_takes_a_name_as_long_as_the_file_system_allows(self):
        with tempfile.TemporaryDirectory() as tmp:
            longest = Path(tmp) / ("x" * 251 + ".png")
            crop.write(longest, b"x")
            self.assertEqual(longest.read_bytes(), b"x")


class Boxes(unittest.TestCase):
    """box keeps the pixels from LEFT,TOP up to RIGHT,BOTTOM, clamped to the image, and refuses an empty box with the
    image's size in the message."""

    def setUp(self):
        self.image = image_of(rgba_of(sample_rows(13, 7, 6, 8, random.Random(13)), 6, 8))

    def test_box_keeps_the_region_clamped_to_the_image(self):
        cut, used = crop.box(self.image, 2, 1, 6, 4)
        self.assertEqual(used, (2, 1, 6, 4))
        self.assertEqual((cut.width, cut.height), (4, 3))
        self.assertEqual(cut.rows, [row[2 * 4:6 * 4] for row in self.image.rows[1:4]])
        cut, used = crop.box(self.image, -5, -5, 100, 100)
        self.assertEqual(used, (0, 0, 13, 7))
        self.assertEqual((cut.rows, cut.extras), (self.image.rows, self.image.extras))

    def test_an_empty_or_reversed_box_is_refused_with_the_size(self):
        for left, top, right, bottom in ((5, 5, 5, 9), (5, 5, 9, 5), (6, 1, 2, 4), (20, 0, 30, 5), (0, 7, 13, 9)):
            with self.subTest(box=(left, top, right, bottom)):
                with self.assertRaises(crop.Problem) as caught:
                    crop.box(self.image, left, top, right, bottom)
                self.assertIn("13 x 7", str(caught.exception))


class Squeezes(unittest.TestCase):
    """squeeze cuts every run of identical rows, then of identical columns, longer than the gap to the gap, and keeps
    every row and column that differs from its neighbour."""

    def check(self, rows, gap=16):
        """squeeze against the rule applied by hand; returns what squeeze gave."""
        result, cut_rows, cut_columns = crop.squeeze(image_of(rows), gap)
        expected, height, width = squeezed_by_hand(rows, gap)
        self.assertEqual(result.rows, expected)
        self.assertEqual((result.width, result.height), (width, height))
        self.assertEqual((cut_rows, cut_columns), (len(rows) - height, len(rows[0]) // 4 - width))
        return result, cut_rows, cut_columns

    def test_squeeze_keeps_gap_rows_of_a_run_and_every_differing_row(self):
        w, gap = 8, 16
        rows = (text_rows(3, w) + blank(gap - 1, w) + text_rows(3, w, y0=3) + blank(gap, w) + text_rows(3, w, y0=6)
                + blank(gap + 1, w) + text_rows(3, w, y0=9) + blank(200, w) + text_rows(3, w, y0=12))
        result, cut_rows, cut_columns = self.check(rows, gap)
        self.assertEqual((cut_rows, cut_columns), (1 + 200 - gap, 0))
        self.assertEqual(result.height, 15 + (gap - 1) + gap + gap + gap)
        # every row of the result is a row of the input, in order
        it = iter(rows)
        for row in result.rows:
            self.assertTrue(any(row == candidate for candidate in it))

    def test_squeeze_cuts_identical_columns_after_the_rows_and_loses_nothing(self):
        w, gap = 300, 16
        rows = text_rows(4, w, 0, 19) + blank(50, w) + text_rows(4, w, 220, 300, y0=4) + blank(40, w)
        result, cut_rows, cut_columns = self.check(rows, gap)
        self.assertEqual(result.width, 19 + gap + 80)  # the blank columns 19 to 220 keep a gap's worth
        self.assertEqual(cut_columns, 201 - gap)
        self.assertEqual(result.rows[0][35 * 4:], rows[0][220 * 4:])  # the right-hand text is intact, a gap on
        # The columns cut after the rows are the columns that would be cut with every row kept: a row cut was
        # identical to a row kept, so no difference between two columns went with it.
        _, _, width_with_every_row = squeezed_by_hand(rows, gap, row_gap=10 ** 6)
        self.assertEqual(width_with_every_row, result.width)

    def test_squeeze_leaves_periodic_content_and_counts_alpha(self):
        a, b = (BLACK + WHITE) * 4, (WHITE + BLACK) * 4
        zebra = [a, b] * 20
        result, cut_rows, cut_columns = self.check(zebra)
        self.assertEqual((cut_rows, cut_columns), (0, 0))
        clear = WHITE[:3] + b"\x80"
        rows = blank(30, 8) + [clear * 8] + blank(30, 8)  # same colour, another alpha: a row of its own
        result, cut_rows, cut_columns = self.check(rows)
        self.assertEqual(result.height, 16 + 1 + 16)

    def test_squeeze_with_a_gap_of_one_keeps_one_of_each_run_and_refuses_less(self):
        rows = blank(30, 8) + text_rows(2, 8) + blank(30, 8)
        result, cut_rows, _ = self.check(rows, 1)
        self.assertEqual(result.height, 4)
        with self.assertRaises(crop.Problem):
            crop.squeeze(image_of(rows), 0)

    def test_the_ssms_shape(self):
        # A full-screen window: query text at the top of a blank editor, a one-row results grid in a blank pane, and a
        # status bar with text at both ends. The squeeze alone closes the editor, the pane and the bar's middle.
        w = 2000
        yellow = b"\xff\xf0\x80\xff"
        bar = [row_left[:220 * 4] + yellow * (1380 - 220) + row_right[1380 * 4:]
               for row_left, row_right in zip(text_rows(50, w, 10, 220, y0=0, paper=yellow), text_rows(50, w, 1380, 1990, y0=50, paper=yellow))]
        rows = text_rows(60, w, 50, 700) + blank(540, w) + text_rows(40, w, 10, 180, y0=60) + blank(510, w) + bar
        result, cut_rows, cut_columns = self.check(rows)
        self.assertEqual(result.height, 60 + 16 + 40 + 16 + 50)
        self.assertEqual(cut_rows, (540 - 16) + (510 - 16))
        self.assertEqual(cut_columns, 680 - 16)  # columns 700 to 1380 are blank in every row
        self.assertEqual(result.width, 2000 - (680 - 16))
        status = result.rows[-1]
        self.assertEqual(status[(700 + 16) * 4:], bar[-1][1380 * 4:])  # the server details now sit a gap from the text

    def test_a_bar_chart_shrinks_like_blank_space_which_is_the_known_limit(self):
        w, red = 600, b"\xff\x00\x00\xff"
        bars = []
        for length in (100, 300, 500):
            bars += [red * length + WHITE * (w - length)] * 10 + blank(5, w)
        result, _, _ = self.check(bars)
        self.assertLess(result.width, 100)  # every bar is now about a gap long: size-coded content must not be squeezed


# --- The command line

def run_crop(*args, cwd, env=None):
    """crop.py run whole, as the skill runs it, from cwd."""
    return subprocess.run([sys.executable, str(CROP), *map(str, args)], cwd=cwd, env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=120)


class CommandLine(unittest.TestCase):
    """crop.py prints one line of JSON, writes beside the input or at --out and never over the input or over another
    file unasked, puts the strips --edges asks for in a fresh temporary folder and nowhere else, and refuses with one
    line and no traceback. Each run is given a temporary folder of the test's own, where --edges makes its folder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "shots").mkdir()
        self.scratch = self.dir / "scratch"
        self.scratch.mkdir()
        self.env = dict(os.environ, TMPDIR=str(self.scratch))  # Python's tempfile looks at TMPDIR first, on Windows too
        self.pixels = sample_rows(13, 5, 6, 8, random.Random(21))
        self.rows = rgba_of(self.pixels, 6, 8)
        self.source = written(self.dir / "shots" / "in.png", png(self.pixels, filters=[4, 3, 2, 1, 0]))

    def tearDown(self):
        self.tmp.cleanup()

    def ok(self, *args):
        proc = run_crop(*args, cwd=self.dir, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")
        lines = proc.stdout.splitlines()
        self.assertEqual(len(lines), 1, proc.stdout)
        return json.loads(lines[0])

    def refused(self, *args, code=1):
        proc = run_crop(*args, cwd=self.dir, env=self.env)
        self.assertEqual(proc.returncode, code, proc.stdout + proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertNotIn("Traceback", proc.stderr)
        return proc.stderr.strip()

    def files(self):
        return sorted(p.name for p in (self.dir / "shots").iterdir())

    def test_no_operation_prints_the_size_and_writes_nothing(self):
        out = self.ok("shots/in.png")
        self.assertEqual(out, {"image": str(self.source.resolve()), "width": 13, "height": 5, "out": None, "out_width": None,
                               "out_height": None, "box": None, "squeezed_rows": 0, "squeezed_columns": 0, "edges": None})
        self.assertEqual(self.files(), ["in.png"])

    def test_box_writes_the_default_name_beside_the_input(self):
        out = self.ok("shots/in.png", "--box", "2,1,6,4")
        self.assertEqual(self.files(), ["in-cropped.png", "in.png"])
        self.assertEqual(out["out"], str((self.dir / "shots" / "in-cropped.png").resolve()))
        self.assertEqual((out["out_width"], out["out_height"], out["box"], out["edges"]), (4, 3, [2, 1, 6, 4], None))
        self.assertEqual(list(self.scratch.iterdir()), [])  # no strips unasked
        self.assertEqual(read((self.dir / "shots" / "in-cropped.png").read_bytes())[3], [row[2 * 4:6 * 4] for row in self.rows[1:4]])
        out = self.ok("shots/in.png", "--box", "-5,-5,100,100")  # clamped, and the default name replaced unasked
        self.assertEqual(out["box"], [0, 0, 13, 5])
        self.assertEqual(read((self.dir / "shots" / "in-cropped.png").read_bytes())[3], self.rows)

    def test_squeeze_counts_what_it_cut_and_box_comes_first(self):
        rows = text_rows(3, 40, 0, 10) + blank(30, 40) + text_rows(3, 40, 30, 40, y0=3)
        written(self.dir / "shots" / "blank.png", png([[tuple(row[x * 4:(x + 1) * 4]) for x in range(40)] for row in rows]))
        out = self.ok("shots/blank.png", "--squeeze", "--gap", "4")
        expected, height, width = squeezed_by_hand(rows, 4)
        self.assertEqual((out["out_width"], out["out_height"]), (width, height))
        self.assertEqual((out["squeezed_rows"], out["squeezed_columns"]), (len(rows) - height, 40 - width))
        self.assertEqual(read((self.dir / "shots" / "blank-cropped.png").read_bytes())[3], expected)
        out = self.ok("shots/blank.png", "--box", "0,0,20,36", "--squeeze")  # the box, then the squeeze of the box
        expected, height, width = squeezed_by_hand([row[:20 * 4] for row in rows], 16)
        self.assertEqual((out["box"], out["out_width"], out["out_height"]), ([0, 0, 20, 36], width, height))
        self.assertEqual(read((self.dir / "shots" / "blank-cropped.png").read_bytes())[3], expected)

    def test_out_names_the_file_and_never_the_input_or_another_file_unasked(self):
        out = self.ok("shots/in.png", "--box", "0,0,2,2", "--out", "evidence.png")
        self.assertEqual(out["out"], str((self.dir / "evidence.png").resolve()))
        self.assertTrue((self.dir / "evidence.png").exists())
        message = self.refused("shots/in.png", "--box", "0,0,2,2", "--out", "evidence.png")
        self.assertIn("exists", message)
        self.assertIn("--overwrite", message)
        self.ok("shots/in.png", "--box", "0,0,3,3", "--out", "evidence.png", "--overwrite")
        self.assertEqual(read((self.dir / "evidence.png").read_bytes())[:2], (3, 3))
        for same in ("shots/in.png", "shots/../shots/in.png", str(self.source)):
            with self.subTest(out=same):
                self.assertIn("input", self.refused("shots/in.png", "--box", "0,0,2,2", "--out", same, "--overwrite"))
        if (self.dir / "shots" / "IN.PNG").exists():  # a case-insensitive file system: the same file by another name
            self.assertIn("input", self.refused("shots/in.png", "--box", "0,0,2,2", "--out", "shots/IN.PNG", "--overwrite"))
        self.assertEqual(read(self.source.read_bytes())[3], self.rows)  # the input is as it was

    def test_refusals_are_one_line_and_bad_arguments_exit_2(self):
        written(self.dir / "shots" / "photo.png", b"\xff\xd8\xff\xe0" + bytes(40))
        message = self.refused("shots/photo.png", "--squeeze")
        self.assertIn("JPEG", message)
        self.assertEqual(len(message.splitlines()), 1)
        self.assertEqual(self.files(), ["in.png", "photo.png"])
        self.assertIn("LEFT,TOP,RIGHT,BOTTOM", self.refused("shots/in.png", "--box", "1,2,3", code=2))
        self.refused("shots/in.png", "--box", "a,b,c,d", code=2)
        self.assertIn("--squeeze", self.refused("shots/in.png", "--gap", "8"))
        self.assertIn("--gap", self.refused("shots/in.png", "--squeeze", "--gap", "0"))
        self.assertIn("--grid", self.refused("shots/in.png", "--grid", "--squeeze"))
        self.assertIn("holds no pixel", self.refused("shots/in.png", "--box", "20,0,30,5"))
        self.assertIn("folder", self.refused("shots/in.png", "--box", "0,0,2,2", "--out", "shots"))
        self.assertIn("folder", self.refused("shots/in.png", "--box", "0,0,2,2", "--out", "shots", "--overwrite"))
        self.assertIn("13 x 5", self.refused("shots/in.png", "--box", "20,0,30,5"))
        self.assertIn("cannot read", self.refused("shots/none.png"))

    def test_a_path_outside_ascii_comes_back_in_the_json(self):
        source = written(self.dir / "shots" / "snímek 写真.png", png(self.pixels))
        out = self.ok(source, "--box", "0,0,2,2")
        self.assertEqual(out["image"], str(source.resolve()))
        self.assertTrue((self.dir / "shots" / "snímek 写真-cropped.png").exists())

    def strips(self, out):
        """The two strips the JSON line names, as (path, what the test's own reader makes of the file) each."""
        return [(Path(out["edges"][end]), read(Path(out["edges"][end]).read_bytes())) for end in ("top", "bottom")]

    def test_edges_writes_the_top_and_bottom_of_the_result_into_a_fresh_temporary_folder(self):
        gama = (b"gAMA", struct.pack(">I", 45455))
        pixels = sample_rows(13, 30, 6, 8, random.Random(41))
        written(self.dir / "shots" / "tall.png", png(pixels, extras=[gama]))
        (self.dir / "evidence").mkdir()
        out = self.ok("shots/tall.png", "--box", "2,1,11,29", "--edges", "5", "--out", "evidence/query.png")
        result = [row[2 * 4:11 * 4] for row in rgba_of(pixels, 6, 8)[1:29]]
        self.assertEqual(read((self.dir / "evidence" / "query.png").read_bytes())[3], result)  # the result as without it
        (top, (width, height, _, top_rows)), (bottom, (_, _, _, bottom_rows)) = self.strips(out)
        self.assertEqual((top.name, bottom.name), ("query-top.png", "query-bottom.png"))
        self.assertEqual((width, height), (9, 5))  # the result's full width
        self.assertEqual((top_rows, bottom_rows), (result[:5], result[-5:]))
        self.assertEqual(chunk_kinds(top.read_bytes()), [b"IHDR", b"gAMA", b"IDAT", b"IEND"])  # encoded as the result is
        folder = top.parent
        self.assertEqual((bottom.parent, folder.parent), (folder, self.scratch.resolve()))  # a folder of their own
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["query-bottom.png", "query-top.png"])  # no part file
        self.assertEqual(sorted(p.name for p in (self.dir / "evidence").iterdir()), ["query.png"])  # none beside the evidence
        self.assertEqual(self.files(), ["in.png", "tall.png"])  # or beside the input
        again = self.ok("shots/tall.png", "--box", "2,1,11,29", "--edges", "5", "--out", "evidence/query.png", "--overwrite")
        self.assertNotEqual(Path(again["edges"]["top"]).parent, folder)  # a fresh folder each run

    def test_edges_with_squeeze_are_of_the_squeezed_result(self):
        rows = text_rows(3, 40, 0, 10) + blank(30, 40) + text_rows(3, 40, 30, 40, y0=3)
        written(self.dir / "shots" / "blank.png", png([[tuple(row[x * 4:(x + 1) * 4]) for x in range(40)] for row in rows]))
        out = self.ok("shots/blank.png", "--squeeze", "--gap", "4", "--edges", "4")
        expected, height, width = squeezed_by_hand(rows, 4)
        self.assertEqual((out["out_width"], out["out_height"]), (width, height))
        (top, (top_width, _, _, top_rows)), (bottom, (_, _, _, bottom_rows)) = self.strips(out)
        self.assertEqual((top.name, bottom.name), ("blank-cropped-top.png", "blank-cropped-bottom.png"))
        self.assertEqual((top_width, top_rows, bottom_rows), (width, expected[:4], expected[-4:]))
        self.assertEqual(self.files(), ["blank-cropped.png", "blank.png", "in.png"])

    def test_edges_on_its_own_cuts_the_input_and_writes_nothing_beside_it(self):
        out = self.ok("shots/in.png", "--edges", "2")
        self.assertEqual((out["out"], out["out_width"], out["out_height"], out["box"]), (None, None, None, None))
        (top, (_, _, _, top_rows)), (bottom, (_, _, _, bottom_rows)) = self.strips(out)
        self.assertEqual((top.name, bottom.name), ("in-top.png", "in-bottom.png"))
        self.assertEqual((top_rows, bottom_rows), (self.rows[:2], self.rows[-2:]))
        out = self.ok("shots/in.png", "--edges", "2", "--out", "evidence.png")  # nothing to cut, so --out names nothing
        self.assertEqual((out["out"], Path(out["edges"]["top"]).name), (None, "in-top.png"))
        self.assertFalse((self.dir / "evidence.png").exists())
        self.assertEqual(self.files(), ["in.png"])
        self.assertEqual(read(self.source.read_bytes())[3], self.rows)  # the input is as it was

    def test_edges_of_one_row_and_at_or_past_the_height(self):
        (_, (_, _, _, top_rows)), (_, (_, _, _, bottom_rows)) = self.strips(self.ok("shots/in.png", "--edges", "1"))
        self.assertEqual((top_rows, bottom_rows), (self.rows[:1], self.rows[-1:]))
        for n in ("5", "6", "1000"):  # the image is 5 rows tall: each strip is the whole of it
            with self.subTest(n=n):
                (_, (_, _, _, top_rows)), (_, (_, _, _, bottom_rows)) = self.strips(self.ok("shots/in.png", "--edges", n))
                self.assertEqual((top_rows, bottom_rows), (self.rows, self.rows))
        out = self.ok("shots/in.png", "--box", "0,0,13,3", "--edges", "4")  # the result's height counts, not the input's
        self.assertEqual([strip[3] for _, strip in self.strips(out)], [self.rows[:3], self.rows[:3]])

    def test_edges_refusals_write_nothing(self):
        for n in ("0", "-3"):
            with self.subTest(n=n):
                message = self.refused("shots/in.png", "--box", "0,0,2,2", "--edges", n)
                self.assertIn("--edges", message)
                self.assertIn("at least 1", message)
                self.assertEqual(len(message.splitlines()), 1)
        self.refused("shots/in.png", "--edges", "x", code=2)
        self.refused("shots/in.png", "--edges", code=2)
        self.assertIn("--grid", self.refused("shots/in.png", "--grid", "--edges", "80"))
        written(self.dir / "query.png", b"before")
        self.assertIn("exists", self.refused("shots/in.png", "--box", "0,0,2,2", "--edges", "1", "--out", "query.png"))
        self.assertIn("cannot write", self.refused("shots/in.png", "--box", "0,0,2,2", "--edges", "1", "--out", "nowhere/query.png"))
        self.assertEqual((self.dir / "query.png").read_bytes(), b"before")
        self.assertEqual((self.files(), list(self.scratch.iterdir())), (["in.png"], []))

    def test_a_run_that_fails_while_writing_leaves_nothing_behind(self):
        # Whichever of the three files cannot be written, the result is not written and the strips' folder is gone.
        real = crop.write
        for failing in ("query-top.png", "query-bottom.png", "query.png"):
            def write(path, data, failing=failing):
                if Path(path).name == failing:
                    raise crop.Problem(f"cannot write {path}: no space left on device")
                real(path, data)
            with self.subTest(failing=failing):
                with patch.object(crop, "write", write), patch.object(tempfile, "tempdir", str(self.scratch)), \
                        patch("sys.stdout", io.StringIO()) as stdout, patch("sys.stderr", io.StringIO()), \
                        self.assertRaises(SystemExit) as caught:
                    crop.main([str(self.source), "--box", "0,0,2,2", "--edges", "1", "--out", str(self.dir / "query.png")])
                self.assertIn("cannot write", str(caught.exception.code))
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(list(self.scratch.iterdir()), [])
                self.assertFalse((self.dir / "query.png").exists())
        with patch.object(tempfile, "mkdtemp", side_effect=PermissionError(13, "Permission denied")), \
                patch("sys.stdout", io.StringIO()) as stdout, patch("sys.stderr", io.StringIO()), \
                self.assertRaises(SystemExit) as caught:  # no temporary folder to be had: refused on one line
            crop.main([str(self.source), "--box", "0,0,2,2", "--edges", "1", "--out", str(self.dir / "query.png")])
        self.assertEqual(str(caught.exception.code), "cannot make a temporary folder for --edges: Permission denied")
        self.assertEqual(stdout.getvalue(), "")
        self.assertFalse((self.dir / "query.png").exists())


# --- The grid

class Grid(unittest.TestCase):
    """--grid rules a copy every 100 pixels with dashes of black and white, labels each rule with its coordinate in
    digits scaled to the image, and changes nothing else."""

    GRAY = b"\x80\x80\x80\xff"

    def test_digits_scale_with_the_width_but_a_label_never_reaches_the_next_rule(self):
        for width, scale in ((13, 2), (499, 2), (1000, 2), (1500, 3), (2000, 4), (3840, 5), (5120, 5), (12000, 4)):
            self.assertEqual(crop.scale_for(width), scale, width)
            label = crop.rendered(str(((width - 1) // 100) * 100), scale)  # the widest label, at the last rule
            self.assertLessEqual(scale + len(label[0]), 100)
        # a tall image's left-edge labels have a digit more: they must not reach the first rule either
        self.assertEqual(crop.scale_for(2500, 400), 5)
        self.assertEqual(crop.scale_for(2500, 10100), 4)
        self.assertLessEqual(4 + len(crop.rendered("10000", 4)[0]), 100)

    def test_labels_stay_apart_on_wide_images(self):
        for width in (2560, 3840, 5120):
            ruled = crop.grid(image_of(blank(60, width, self.GRAY)))
            s = crop.scale_for(width)
            left_out = 0
            for x in range(100, width, 100):
                bitmap = crop.rendered(str(x), s)
                if x + s + len(bitmap[0]) > width:  # no room right of its rule: left out, the pixels there untouched
                    left_out += 1
                    self.assertEqual(ruled.rows[s][(x + 1) * 4:(x + 2) * 4], self.GRAY, (width, x))
                else:
                    drawn = [[ruled.rows[s + r][(x + s + c) * 4:(x + s + c + 1) * 4] == BLACK for c in range(len(bitmap[0]))]
                             for r in range(len(bitmap))]
                    self.assertEqual(drawn, bitmap, (width, x))  # intact: no neighbour painted over it
            self.assertLessEqual(left_out, 1, width)

    def test_the_font_is_three_by_five_with_a_unit_of_padding(self):
        one = crop.rendered("1", 1)
        self.assertEqual(["".join("#" if b else "." for b in row) for row in one],
                         [".....", "..#..", ".##..", "..#..", "..#..", ".###.", "....."])
        ten = crop.rendered("10", 2)
        self.assertEqual((len(ten), len(ten[0])), (14, 2 * (3 + 1 + 3 + 2)))
        for ch in "0123456789":
            self.assertEqual(len(crop.DIGITS[ch]), 5)

    def test_rules_every_hundred_pixels_and_leaves_the_rest(self):
        image = image_of(blank(250, 450, self.GRAY))
        ruled = crop.grid(image)
        self.assertEqual((ruled.width, ruled.height, ruled.extras), (450, 250, []))
        s, dash = crop.scale_for(450), 4 * crop.scale_for(450)
        for x in (100, 200, 300, 400):
            column = [ruled.rows[y][x * 4:(x + 1) * 4] for y in range(8 * dash, 250)]  # below the labels, at a dash's start
            self.assertEqual(set(column), {BLACK, WHITE}, x)
            self.assertEqual(column[:dash], [BLACK] * dash)  # dashes dash long, black first
            self.assertEqual(column[dash:2 * dash], [WHITE] * dash)
        for y in (100, 200):
            row = [ruled.rows[y][x * 4:(x + 1) * 4] for x in range(60, 450)]
            self.assertEqual(set(row), {BLACK, WHITE}, y)
        for x, y in ((50, 50), (150, 150), (449, 249), (99, 100 + 30), (101, 230), (350, 199)):
            self.assertEqual(ruled.rows[y][x * 4:(x + 1) * 4], self.GRAY, (x, y))
        self.assertEqual(image.rows, blank(250, 450, self.GRAY))  # the image given is not changed

    def test_each_rule_is_labelled_with_its_coordinate(self):
        image = image_of(blank(250, 450, self.GRAY))
        ruled = crop.grid(image)
        s = crop.scale_for(450)

        def drawn(x0, y0, bitmap):
            return [[ruled.rows[y0 + r][(x0 + c) * 4:(x0 + c + 1) * 4] == BLACK for c in range(len(bitmap[0]))]
                    for r in range(len(bitmap))]

        def paper(x0, y0, bitmap):
            return {ruled.rows[y0 + r][(x0 + c) * 4:(x0 + c + 1) * 4] for r in range(len(bitmap))
                    for c in range(len(bitmap[0])) if not bitmap[r][c]}
        for x in (100, 200, 300, 400):
            bitmap = crop.rendered(str(x), s)
            self.assertEqual(drawn(x + s, s, bitmap), bitmap, x)  # along the top edge, just right of the rule
            self.assertEqual(paper(x + s, s, bitmap), {WHITE})
        for y in (100, 200):
            bitmap = crop.rendered(str(y), s)
            self.assertEqual(drawn(s, y + s, bitmap), bitmap, y)  # along the left edge, just below the rule
            self.assertEqual(paper(s, y + s, bitmap), {WHITE})

    def test_a_label_with_no_room_is_left_out(self):
        ruled = crop.grid(image_of(blank(120, 110, self.GRAY)))  # the rule at x = 100 has 9 pixels to its right
        for y in range(120):
            if y != 100:  # the rule at y = 100 crosses them
                self.assertEqual(ruled.rows[y][101 * 4:], self.GRAY * 9, y)
        ruled = crop.grid(image_of(blank(110, 120, self.GRAY)))  # and the rule at y = 100 has 9 rows below it
        for y in range(101, 110):
            row = ruled.rows[y]
            self.assertEqual(row[:100 * 4] + row[101 * 4:], self.GRAY * 119, y)  # the rule at x = 100 crosses them

    def test_grid_on_the_command_line_names_its_file_and_keeps_the_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            pixels = sample_rows(130, 120, 6, 8, random.Random(31))
            source = written(Path(tmp) / "in.png", png(pixels))
            proc = run_crop("in.png", "--grid", cwd=tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            out = json.loads(proc.stdout)
            self.assertEqual((out["out"], out["out_width"], out["out_height"], out["box"]), (str((Path(tmp) / "in-grid.png").resolve()), 130, 120, None))
            self.assertEqual(read((Path(tmp) / "in-grid.png").read_bytes())[3], crop.grid(image_of(rgba_of(pixels, 6, 8))).rows)
            self.assertEqual(read(source.read_bytes())[3], rgba_of(pixels, 6, 8))


# --- In a real browser, and against the clock

def find_chrome():
    """Chrome, Chromium or Edge where each installs on Windows, macOS and Linux, as api-call's render.py finds them,
    or None."""
    local = os.environ.get("LOCALAPPDATA")
    candidates = [os.environ.get("CHROME"),
                  r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                  r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                  local and os.path.join(local, r"Google\Chrome\Application\chrome.exe"),
                  r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                  r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Chromium.app/Contents/MacOS/Chromium",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    for c in filter(None, candidates):
        if os.path.exists(c):
            return c
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "microsoft-edge-stable"):
        found = shutil.which(name)
        if found:
            return found
    return None


class InTheBrowser(unittest.TestCase):
    """crop.py reads what Chrome writes, and Chrome reads what crop.py writes, checked in a real headless browser.
    Skipped where none is found."""

    @classmethod
    def setUpClass(cls):
        cls.browser = find_chrome()
        if not cls.browser:
            raise unittest.SkipTest("no Chrome, Chromium or Edge found; set CHROME to its path")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def shown(self, name, data, width, height):
        """A page showing the PNG data at 1:1 in its corner, captured by the browser at width x height: the capture."""
        page = self.dir / f"{name}.html"
        page.write_text('<!doctype html><meta charset="utf-8"><style>html,body{margin:0;background:#fff}img{display:block}</style>'
                        f'<img src="data:image/png;base64,{base64.b64encode(data).decode()}">', encoding="utf-8")
        shot = self.dir / f"{name}.png"
        subprocess.run([self.browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--window-size={width},{height}",
                        f"--screenshot={shot}", page.as_uri()], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
        self.assertTrue(shot.exists(), "the browser did not write the capture")
        return shot

    def test_reads_what_chrome_writes_and_chrome_reads_what_it_writes(self):
        # A window-like page: checkered content (every row and column differing from its neighbour) at the top left,
        # in the middle left, and at both ends of a footer, with blank space between and to the right.
        a, b, white = (255, 0, 0, 255), (0, 0, 255, 255), (255, 255, 255, 255)
        width, height = 600, 900

        def pixel(x, y):
            content = (y < 50 and x < 300) or (700 <= y < 750 and x < 200) or (y >= 850 and (x < 100 or x >= 500))
            return (a if (x + y) % 2 == 0 else b) if content else white
        pixels = [[pixel(x, y) for x in range(width)] for y in range(height)]
        expected_rows = rgba_of(pixels, 6, 8)
        shot = self.shown("page", png(pixels), width, height)
        image = crop.read_png(shot)
        self.assertEqual((image.width, image.height), (width, height))
        self.assertEqual(image.rows, expected_rows)  # crop.py reads Chrome's PNG pixel for pixel

        proc = run_crop(shot, "--squeeze", cwd=self.dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual((out["out_width"], out["out_height"]), (300 + 16 + 100, 50 + 16 + 50 + 16 + 50))
        self.assertEqual((out["squeezed_rows"], out["squeezed_columns"]), ((650 - 16) + (100 - 16), 200 - 16))
        squeezed = (self.dir / "page-cropped.png").read_bytes()
        expected, h, w = squeezed_by_hand(expected_rows, 16)
        self.assertEqual(read(squeezed)[3], expected)

        again = self.shown("again", squeezed, w, h)  # Chrome shows what crop.py wrote as the same pixels
        self.assertEqual(crop.read_png(again).rows, expected)

        proc = run_crop(shot, "--box", "0,840,120,900", "--out", "footer.png", cwd=self.dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(read((self.dir / "footer.png").read_bytes())[3], [row[:120 * 4] for row in expected_rows[840:900]])


class Timing(unittest.TestCase):
    def test_a_full_hd_paeth_screenshot_is_cut_within_ten_seconds(self):
        # Every row under Paeth, the slowest filter to undo, with random residuals: 1.3 s on an M-series Mac.
        width, height = 1920, 1080
        raw = b"".join(b"\x04" + os.urandom(width * 4) for _ in range(height))
        data = (SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b""))
        with tempfile.TemporaryDirectory() as tmp:
            written(Path(tmp) / "big.png", data)
            started = time.monotonic()
            proc = run_crop("big.png", "--box", "100,100,1800,1000", cwd=tmp)
            elapsed = time.monotonic() - started
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["out_width"], 1700)
        self.assertLess(elapsed, 10, f"{elapsed:.1f} s")


if __name__ == "__main__":
    unittest.main()
