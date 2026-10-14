"""Cuts a PNG screenshot down for posting: one region, its blank stretches closed up, or rulers to read coordinates from.

Usage:
  python3 crop.py IMAGE.png [--box LEFT,TOP,RIGHT,BOTTOM] [--squeeze] [--gap N] [--edges N] [--out FILE.png] [--overwrite]
  python3 crop.py IMAGE.png --grid [--out FILE.png] [--overwrite]
  python3 crop.py IMAGE.png [--edges N]

Reads one PNG and writes one beside it, IMAGE-cropped.png, or IMAGE-grid.png for --grid, or the file --out names,
never over the input, and prints one line of JSON: the image's size, the file written and its size, the box as used,
the rows and columns the squeeze took out, and where --edges put its strips. With no operation it prints the size and
writes nothing.

--box keeps the pixels from LEFT,TOP up to but not including RIGHT,BOTTOM, clamped to the image. --squeeze then cuts
every run of identical rows longer than --gap (16 pixels) to --gap rows, then every such run of columns, so blank space
goes and no row or column that differs from its neighbour is lost. --grid writes a copy ruled every 100 pixels and
labelled with its coordinates, to read a box's edges from.

--edges N writes the first and last N rows of the result at its full width, to look at its edges at full size, as
RESULT-top.png and RESULT-bottom.png in a fresh temporary folder, never beside the evidence. With no other operation it
cuts them from the input, as IMAGE-top.png and IMAGE-bottom.png, to check a result already written. An N at or past the
height makes each strip the whole image. It does not go with --grid, whose copy is never evidence.

Any PNG that is not interlaced is read, at every depth and colour type. The output is 8-bit RGB, or RGBA where any
pixel is not opaque, with the source's sRGB, gAMA, cHRM, pHYs and iCCP chunks carried over and its other metadata not.
Python's standard library alone: no imaging library and no browser.
"""
import argparse, json, os, shutil, struct, sys, tempfile, zlib
from collections import namedtuple
from itertools import accumulate
from pathlib import Path

SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}  # samples a pixel by colour type: gray, RGB, palette, gray and alpha, RGBA
DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}  # the bit depths each defines
CRITICAL = (b"IHDR", b"PLTE", b"IDAT", b"IEND")
COLOUR = (b"sRGB", b"gAMA", b"cHRM", b"pHYs")  # carried over from any source
PROFILE = b"iCCP"  # carried over from an RGB, palette or RGBA source; a gray source's profile is a gray one
LIMIT = 256 * 1024 * 1024  # bytes: the most a file, or its filtered pixel data, may be
PIXELS = 40_000_000  # the most pixels an image may hold: decoded, each takes four bytes, whatever the file packed them in
SIDE = 65535  # the most pixels a side may be: each row and each column costs memory of its own
CHUNKS = 1 << 20  # the most chunks a file may hold: an image needs a handful, and each costs time to pass over
WANTED = (b"IHDR", b"PLTE", b"tRNS", b"sRGB", b"gAMA", b"cHRM", b"pHYs", b"iCCP")  # the chunks kept, the first of each
GAP, STEP = 16, 100  # pixels: what a squeezed run keeps, and how often the grid is ruled

Header = namedtuple("Header", "width height depth color channels bpp stride")
Image = namedtuple("Image", "width height rows extras")  # rows: bytes of width * 4, RGBA; extras: (kind, body) chunks


class Problem(Exception):
    """A reason the image cannot be read or written; its text is safe to print."""


# --- Reading a PNG

def kind_of(data):
    """What a file that is not a PNG is, by its first bytes."""
    if not data:
        return "an empty file"
    for magic, name in ((b"\xff\xd8\xff", "a JPEG"), (b"GIF8", "a GIF"), (b"BM", "a BMP"), (b"II*\0", "a TIFF"), (b"MM\0*", "a TIFF")):
        if data.startswith(magic):
            return name
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "a WebP"
    return "not an image this recognizes"


def chunks(data, name="the file"):
    """A PNG's chunks up to IEND, as (parts, idat): the first chunk of each kind in WANTED by its kind, in the order
    met, and the IDAT data joined. Nothing else is kept, so a file padded with chunks costs no memory, and one of more
    than CHUNKS chunks is refused. A length past the end of the file is a file cut short. A critical chunk whose CRC
    fails refuses the file, as libpng does; an ancillary one whose CRC fails is dropped."""
    if not data.startswith(SIGNATURE):
        raise Problem(f"not a PNG: {name} is {kind_of(data)}; save the screenshot as PNG")
    parts, idat, pos, count = {}, bytearray(), len(SIGNATURE), 0
    while True:
        if pos + 8 > len(data):
            raise Problem("the PNG is cut short: it ends before its IEND chunk")
        n = int.from_bytes(data[pos:pos + 4], "big")
        kind = data[pos + 4:pos + 8]
        label = kind.decode("ascii") if kind.isalpha() else repr(bytes(kind))  # a type is four letters; anything else is shown escaped
        if pos + 12 + n > len(data):
            raise Problem(f"the PNG is cut short in its {label} chunk")
        body = data[pos + 8:pos + 8 + n]
        intact = zlib.crc32(kind + body) == int.from_bytes(data[pos + 8 + n:pos + 12 + n], "big")
        if kind in CRITICAL and not intact:
            raise Problem(f"the PNG's {label} chunk fails its CRC check: the file is damaged")
        count += 1
        if count > CHUNKS:
            raise Problem(f"the PNG holds more than {CHUNKS} chunks, which no image needs")
        if not parts and kind != b"IHDR":
            raise Problem("the PNG does not begin with its IHDR chunk")
        if kind == b"IDAT":
            idat += body
        elif intact and kind in WANTED and kind not in parts:
            parts[kind] = body
        pos += 12 + n
        if kind == b"IEND":
            return parts, bytes(idat)


def header(body):
    """The IHDR chunk read: the Header, with the samples a pixel holds, the bytes a filter steps by and a row's bytes.
    Refuses what PNG does not define, an interlaced image, and one past PIXELS pixels or LIMIT bytes of filtered pixel
    data, before anything is allocated for it. A 1-bit image packs 32 pixels into the bytes one decoded pixel takes, so
    the pixels are capped as well as the bytes."""
    if len(body) != 13:
        raise Problem("the PNG's header is not 13 bytes")
    width, height, depth, color, compression, filter_method, interlace = struct.unpack(">IIBBBBB", body)
    if color not in CHANNELS or depth not in DEPTHS[color]:
        raise Problem(f"a PNG of colour type {color} at {depth} bits, which PNG does not define")
    if compression or filter_method:
        raise Problem("a PNG with a compression or filter method PNG does not define")
    if interlace:
        raise Problem("an interlaced PNG, which this does not read; save it again without interlacing")
    if not width or not height:
        raise Problem("a PNG with no pixels")
    if width > SIDE or height > SIDE:
        raise Problem(f"a PNG of {width} x {height} is larger than this reads: no side may be more than {SIDE} pixels")
    if width * height > PIXELS:
        raise Problem(f"a PNG of {width} x {height} is larger than this reads: {width * height // 1_000_000} megapixels, "
                      f"where the limit is {PIXELS // 1_000_000}")
    channels = CHANNELS[color]
    stride = (width * channels * depth + 7) // 8
    if (stride + 1) * height > LIMIT:
        raise Problem(f"a PNG of {width} x {height} at {channels * depth} bits a pixel is larger than this reads: "
                      f"{((stride + 1) * height) >> 20} MB of pixel data, where the limit is {LIMIT >> 20}")
    return Header(width, height, depth, color, channels, max(1, channels * depth // 8), stride)


def inflate(idat, expected):
    """The filtered scanlines, exactly expected bytes: the IDAT data decompressed with no more than that read out, so
    a crafted stream costs no more memory than the header declared."""
    try:
        raw = zlib.decompressobj().decompress(idat, expected + 1)
    except zlib.error as e:
        raise Problem(f"the PNG's pixel data does not decompress: {e}") from None
    if len(raw) > expected:
        raise Problem("the PNG's pixel data is longer than its header declares")
    if len(raw) < expected:
        raise Problem("the PNG is cut short: its pixel data ends before its last row")
    return raw


def undo(kind, line, prev, bpp):
    """A scanline's filter undone in place the plain way, byte by byte from its prediction: the reference the fast
    paths are checked against, and what Average and Paeth rows use. A row holds the bytes of the pixels wanted, and
    prev the row above as reconstructed; a filter looks only up and left, so the bytes to the right are not needed."""
    n = len(line)
    if kind == 1:
        for i in range(bpp, n):
            line[i] = (line[i] + line[i - bpp]) & 255
    elif kind == 2:
        for i in range(n):
            line[i] = (line[i] + prev[i]) & 255
    elif kind == 3:
        for i in range(min(bpp, n)):
            line[i] = (line[i] + (prev[i] >> 1)) & 255
        for i in range(bpp, n):
            line[i] = (line[i] + ((line[i - bpp] + prev[i]) >> 1)) & 255
    elif kind == 4:
        for i in range(min(bpp, n)):
            line[i] = (line[i] + prev[i]) & 255
        for i in range(bpp, n):
            a, b, c = line[i - bpp], prev[i], prev[i - bpp]
            pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
            line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
    elif kind:
        raise Problem(f"a row uses filter type {kind}, which PNG does not define")
    return line


def undo_up(line, prev):
    """The Up filter undone with big integers: the row's bytes at odd and at even offsets each summed with the row
    above in lanes two bytes wide, where a carry lands in a zero byte and goes no further. A fraction of a millisecond
    a row, where the loop in undo() takes most of a second over a screenshot's height."""
    n = len(line)
    if not n:
        return line
    odd = int.from_bytes((b"\x00\xff" * ((n + 1) // 2))[:n], "big")
    even = int.from_bytes((b"\xff\x00" * ((n + 1) // 2))[:n], "big")
    a, b = int.from_bytes(line, "big"), int.from_bytes(prev, "big")
    total = (((a & odd) + (b & odd)) & odd) | (((a & even) + (b & even)) & even)
    line[:] = total.to_bytes(n, "big")
    return line


def undo_sub(line, bpp):
    """The Sub filter undone as a running sum along each of the bpp byte positions of a pixel, accumulated in C."""
    for k in range(bpp):
        line[k::bpp] = bytes(accumulate(line[k::bpp], lambda x, y: (x + y) & 255))
    return line


def unfilter(raw, h, rows_needed=None, cols_needed=None):
    """The scanlines reconstructed: the first rows_needed of them, and of each the bytes that hold its first
    cols_needed pixels, or all of it."""
    rows = h.height if rows_needed is None else max(0, min(rows_needed, h.height))
    pixels = h.width if cols_needed is None else max(0, min(cols_needed, h.width))
    width = h.stride if cols_needed is None else (pixels * h.channels * h.depth + 7) // 8
    out, prev = [], bytes(width)
    for y in range(rows):
        start = y * (h.stride + 1)
        kind = raw[start]
        line = bytearray(raw[start + 1:start + 1 + width])
        if kind == 2:
            undo_up(line, prev)
        elif kind == 1:
            undo_sub(line, h.bpp)
        elif kind:
            undo(kind, line, prev, h.bpp)
        out.append(line)
        prev = line
    return out


def scale_table(depth):
    """A translation table taking a gray sample of depth bits to its 8-bit value."""
    top = (1 << depth) - 1
    return bytes([v * 255 // top for v in range(top + 1)] + [0] * (255 - top))


UNPACKED = {}  # by depth: each byte's value as the 8 // depth samples it packs, a byte each


def unpacked(line, depth, pixels):
    """The first pixels samples of a scanline of depth bits a sample, one sample a pixel, each in a byte."""
    table = UNPACKED.get(depth)
    if table is None:
        per, mask = 8 // depth, (1 << depth) - 1
        table = UNPACKED[depth] = [bytes((b >> (8 - depth * (i + 1))) & mask for i in range(per)) for b in range(256)]
    return b"".join(map(table.__getitem__, line))[:pixels]


def keyed(line, h, trns, pixels):
    """The alpha of each pixel against a tRNS colour key: 0 where the pixel's samples equal the key, else 255."""
    n = h.channels
    if len(trns) != 2 * n:
        raise Problem("the PNG's tRNS chunk is malformed")
    key = struct.unpack(f">{n}H", trns)
    if h.depth == 16:
        values = struct.unpack(f">{pixels * n}H", line[:pixels * n * 2])
    elif h.depth == 8:
        values = line[:pixels * n]
    else:
        values = unpacked(line, h.depth, pixels)
    return bytes(0 if tuple(values[i * n:(i + 1) * n]) == key else 255 for i in range(pixels))


def rgba(line, h, palette, trns, pixels):
    """One reconstructed scanline as pixels RGBA pixels of a byte a sample: a 16-bit sample by its high byte, a
    shallower gray sample scaled to 255, a palette index through PLTE and tRNS, and a gray or RGB pixel equal to the
    tRNS colour key transparent."""
    if h.depth == 16:
        samples = bytes(line[0::2])
    elif h.depth == 8:
        samples = bytes(line)
    else:
        samples = unpacked(line, h.depth, pixels)
    out = bytearray(pixels * 4)
    out[3::4] = b"\xff" * pixels
    if h.color == 6:
        out[:] = samples[:pixels * 4]
    elif h.color == 4:
        out[0::4] = out[1::4] = out[2::4] = samples[0::2]
        out[3::4] = samples[1::2]
    elif h.color == 2:
        out[0::4], out[1::4], out[2::4] = samples[0::3], samples[1::3], samples[2::3]
    elif h.color == 0:
        out[0::4] = out[1::4] = out[2::4] = samples if h.depth >= 8 else samples.translate(scale_table(h.depth))
    else:
        entries = len(palette) // 3
        if entries and max(samples, default=0) >= entries:
            raise Problem(f"a pixel names palette entry {max(samples)} of a palette of {entries}")
        for k in range(3):
            out[k::4] = samples.translate(palette[k::3].ljust(256, b"\0"))
        if trns:
            out[3::4] = samples.translate(trns[:256].ljust(256, b"\xff"))
    if trns and h.color in (0, 2):
        out[3::4] = keyed(line, h, trns, pixels)
    return bytes(out)


def load(path):
    """The file read: its Header and its chunks. No more than LIMIT bytes and one are read, so a file past the limit, or
    a pipe that never ends, is refused at that point."""
    path = Path(path)
    data = bytearray()
    try:
        with path.open("rb") as f:
            while len(data) <= LIMIT:  # a megabyte at a time: read(n) sets n bytes aside before it reads
                piece = f.read(min(1 << 20, LIMIT + 1 - len(data)))
                if not piece:
                    break
                data += piece
    except OSError as e:
        raise Problem(f"cannot read {path}: {e.strerror or e}") from None
    data = bytes(data)
    if len(data) > LIMIT:
        raise Problem(f"{path.name} is larger than {LIMIT >> 20} MB, which is more than this reads")
    parts = chunks(data, path.name)
    return header(parts[0][b"IHDR"]), parts


def decode(h, parts, rows_needed=None, cols_needed=None):
    """The image the chunks hold as RGBA rows: its first rows_needed rows and cols_needed columns, or all of it, with
    the colour chunks to carry over to a copy."""
    found, idat = parts
    palette, trns = found.get(b"PLTE"), found.get(b"tRNS")
    if h.color == 3 and not palette:
        raise Problem("the PNG has a palette colour type and no palette")
    if palette is not None and (len(palette) % 3 or len(palette) > 768):
        raise Problem("the PNG's palette is malformed")
    raw = inflate(idat, (h.stride + 1) * h.height)
    pixels = h.width if cols_needed is None else max(0, min(cols_needed, h.width))
    rows = [rgba(line, h, palette, trns, pixels) for line in unfilter(raw, h, rows_needed, cols_needed)]
    extras = [(kind, body) for kind, body in found.items() if kind in COLOUR or (kind == PROFILE and h.color in (2, 3, 6))]
    return Image(pixels, len(rows), rows, extras)


def read_png(path, rows_needed=None, cols_needed=None):
    """The PNG at path as an Image: its first rows_needed rows and cols_needed columns, or all of it."""
    h, parts = load(path)
    return decode(h, parts, rows_needed, cols_needed)


# --- Cutting

def clamp(width, height, left, top, right, bottom):
    """The box as used on an image of width x height: its edges held to the image. A box that holds no pixel is
    refused, with the image's size, so the next attempt can be right."""
    l, t, r, b = max(0, left), max(0, top), min(width, right), min(height, bottom)
    if r <= l or b <= t:
        raise Problem(f"the box {left},{top},{right},{bottom} holds no pixel of a {width} x {height} image")
    return l, t, r, b


def box(image, left, top, right, bottom):
    """The pixels from (left, top) up to but not including (right, bottom), clamped to the image: (Image, the box as
    used)."""
    l, t, r, b = clamp(image.width, image.height, left, top, right, bottom)
    return Image(r - l, b - t, [row[l * 4:r * 4] for row in image.rows[t:b]], image.extras), (l, t, r, b)


def runs_kept(same, gap):
    """The indices kept of a sequence whose item i is identical to item i - 1 where same[i] is true: of every run of
    identical items, the first gap."""
    kept, run = [], 0
    for i, repeat in enumerate(same):
        run = run + 1 if repeat else 1
        if run <= gap:
            kept.append(i)
    return kept


def squeeze(image, gap):
    """The image with every run of identical rows, then every run of identical columns, longer than gap cut to gap:
    (Image, rows cut, columns cut). Every row of the result is a row of the image and every column a column of it, in
    their order; a row or column goes only when it is identical to the one before it. The columns are compared after
    the rows are cut, which loses nothing: every row cut was identical to a row kept, so any difference between two
    columns that it held, a kept row holds too."""
    if gap < 1:
        raise Problem("--gap must be at least 1")
    rows = image.rows
    rows = [rows[i] for i in runs_kept([i > 0 and rows[i] == rows[i - 1] for i in range(len(rows))], gap)]
    width = image.width
    # Each row as one integer, xor'd with itself shifted a pixel along: a pixel's four bytes come out zero where it
    # equals the one before it. Or'd over the rows, a column's four bytes are zero only where it equals the column
    # before it in every row.
    diff = 0
    for row in rows:
        v = int.from_bytes(row, "big")
        diff |= v ^ (v >> 32)
    d = diff.to_bytes(width * 4, "big")
    kept = runs_kept([x > 0 and d[x * 4:(x + 1) * 4] == b"\0\0\0\0" for x in range(width)], gap)
    if len(kept) < width:
        spans = []
        for x in kept:
            if spans and spans[-1][1] == x:
                spans[-1][1] = x + 1
            else:
                spans.append([x, x + 1])
        rows = [b"".join(row[a * 4:b * 4] for a, b in spans) for row in rows]
    return Image(len(kept), len(rows), rows, image.extras), image.height - len(rows), width - len(kept)


def strips(image, n):
    """The first and last n rows of the image at its full width, as two Images, to look at its edges at full size;
    each is the whole image where n is at or past its height."""
    n = min(n, image.height)
    return (Image(image.width, n, image.rows[:n], image.extras),
            Image(image.width, n, image.rows[image.height - n:], image.extras))


# --- The grid

INK, PAPER = b"\x00\x00\x00\xff", b"\xff\xff\xff\xff"
DIGITS = {"0": ("111", "101", "101", "101", "111"), "1": ("010", "110", "010", "010", "111"),
          "2": ("111", "001", "111", "100", "111"), "3": ("111", "001", "111", "001", "111"),
          "4": ("101", "101", "111", "001", "001"), "5": ("111", "100", "111", "001", "111"),
          "6": ("111", "100", "111", "101", "111"), "7": ("111", "001", "001", "001", "001"),
          "8": ("111", "101", "111", "101", "111"), "9": ("111", "101", "111", "001", "111")}


def scale_for(width, height=None, step=STEP):
    """The pixels a unit of the grid's digits takes: more on a wider image, which is looked at scaled down, but never
    so many that the widest label, a unit from its rule, reaches the next rule: along the top edge, where the labels
    sit a step apart, or along the left edge, where a tall image's labels have a digit more."""
    def digits(length):
        return len(str((max(length - 1, step) // step) * step))  # the last rule's label
    units = max(digits(width), digits(height or 0)) * 4 + 1  # three units a digit, one between, one each side
    return max(2, min(width // 500, step // (units + 1)))


def rendered(text, scale):
    """text in the 3 x 5 digit font at scale pixels a unit, a unit apart and a unit of padding around: rows of
    booleans, True where the ink goes."""
    glyphs = [DIGITS[ch] for ch in text]
    width = len(glyphs) * 4 + 1
    rows = []
    for r in range(7):
        bits = [False] * width
        if 1 <= r <= 5:
            for g, glyph in enumerate(glyphs):
                for c, bit in enumerate(glyph[r - 1]):
                    bits[1 + g * 4 + c] = bit == "1"
        rows.append(bits)
    return [[bit for bit in row for _ in range(scale)] for row in rows for _ in range(scale)]


def stamp(rows, width, height, text, x, y, scale):
    """text drawn in black on a white box with its top left at (x, y), when the whole of it fits inside the image;
    a label with no room is left out rather than moved onto its rule or its neighbour."""
    bitmap = rendered(text, scale)
    if x + len(bitmap[0]) > width or y + len(bitmap) > height:
        return
    for r, bits in enumerate(bitmap):
        row = rows[y + r]
        for c, ink in enumerate(bits):
            row[(x + c) * 4:(x + c + 1) * 4] = INK if ink else PAPER


def grid(image, step=STEP):
    """A copy ruled every step pixels with dashes of black and white, visible on any background, each rule labelled
    with its coordinate along the top edge or the left edge in digits scaled to the image: a copy to read a box's
    edges from, never evidence."""
    scale = scale_for(image.width, image.height)
    dash = 4 * scale
    rows = [bytearray(row) for row in image.rows]
    for x in range(step, image.width, step):
        for y in range(image.height):
            rows[y][x * 4:(x + 1) * 4] = INK if (y // dash) % 2 == 0 else PAPER
    for y in range(step, image.height, step):
        row = rows[y]
        for x in range(image.width):
            row[x * 4:(x + 1) * 4] = INK if (x // dash) % 2 == 0 else PAPER
    for x in range(step, image.width, step):
        stamp(rows, image.width, image.height, str(x), x + scale, scale, scale)
    for y in range(step, image.height, step):
        stamp(rows, image.width, image.height, str(y), scale, y + scale, scale)
    return Image(image.width, image.height, [bytes(row) for row in rows], image.extras)


# --- Writing a PNG

def chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def encode_png(image):
    """The image as a PNG: 8-bit RGB, or RGBA where any pixel is not opaque, every row unfiltered, with the colour
    chunks carried over written after the header."""
    opaque = b"\xff" * image.width
    color = 2 if all(row[3::4] == opaque for row in image.rows) else 6
    raw = bytearray()
    for row in image.rows:
        raw += b"\0"
        if color == 2:
            rgb = bytearray(row)
            del rgb[3::4]
            raw += rgb
        else:
            raw += row
    out = [SIGNATURE, chunk(b"IHDR", struct.pack(">IIBBBBB", image.width, image.height, 8, color, 0, 0, 0))]
    out += [chunk(kind, body) for kind, body in image.extras]
    out += [chunk(b"IDAT", zlib.compress(raw, 6)), chunk(b"IEND", b"")]
    return b"".join(out)


def write(path, data):
    """data to path by way of a temporary file in the same folder, moved into place whole, so a failure leaves no half
    file behind for anything to pick up."""
    path, tmp = Path(path), None
    try:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".crop-", suffix=".part")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        try:
            mode = os.stat(path).st_mode & 0o777  # a file replaced keeps its permissions
        except OSError:
            mask = os.umask(0)  # a new file gets an ordinary file's, where mkstemp made a private one
            os.umask(mask)
            mode = 0o666 & ~mask
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        tmp = None
    except OSError as e:
        raise Problem(f"cannot write {path}: {e.strerror or e}") from None
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.chmod(tmp, 0o600)  # the mode copied may be read-only, which Windows will not delete
            except OSError:
                pass
            os.unlink(tmp)


def scratch():
    """A fresh folder of its own in the system's temporary folder, for the strips --edges writes: never the evidence
    folder, so a strip cannot be posted by mistake."""
    try:
        return Path(tempfile.mkdtemp(prefix="crop-edges-"))
    except OSError as e:
        raise Problem(f"cannot make a temporary folder for --edges: {e.strerror or e}") from None


# --- The command line

def parse_box(text):
    """--box as LEFT,TOP,RIGHT,BOTTOM, four integers; anything else is an argparse error, exit 2."""
    try:
        edges = tuple(int(part) for part in text.split(","))
    except ValueError:
        edges = ()
    if len(edges) != 4:
        raise argparse.ArgumentTypeError("a box is LEFT,TOP,RIGHT,BOTTOM in pixels, such as 40,120,900,600")
    return edges


def output_for(source, out, suffix, overwrite=False):
    """Where the result goes: --out as given, else IMAGE-<suffix>.png beside the input. The input itself is never
    written over, and a file --out names is replaced only with --overwrite; the default name is this script's own to
    replace."""
    path = Path(out) if out else source.with_name(f"{source.stem}-{suffix}.png")
    if path.is_dir():
        raise Problem(f"{path} is a folder; --out names a file")
    if path.exists():
        if os.path.samefile(path, source):
            raise Problem(f"{path} is the input, which is never written over; leave --out out or name another file")
        if out and not overwrite:
            raise Problem(f"{path} exists; --overwrite replaces it, or --out names another file")
    return path


def run(args):
    """What the command line asks for, done; returns what the JSON line reports. The strips --edges asks for are
    written first and the result last, and a failure at any of them removes the strips' folder, so a run that fails
    leaves nothing behind."""
    source = Path(args.image)
    if args.grid and (args.box or args.squeeze or args.edges is not None):
        raise Problem("--grid goes on its own: it draws rulers on a copy of the image, to read a box from")
    if args.gap is not None and not args.squeeze:
        raise Problem("--gap goes with --squeeze: it is the pixels a squeezed run keeps")
    gap = GAP if args.gap is None else args.gap
    if args.squeeze and gap < 1:
        raise Problem("--gap must be at least 1")
    if args.edges is not None and args.edges < 1:
        raise Problem("--edges must be at least 1: it is the rows each strip holds")
    h, parts = load(source)
    result = {"image": str(source.resolve()), "width": h.width, "height": h.height, "out": None, "out_width": None,
              "out_height": None, "box": None, "squeezed_rows": 0, "squeezed_columns": 0, "edges": None}
    cut = bool(args.box or args.squeeze or args.grid)
    if not (cut or args.edges):
        return result
    out = output_for(source, args.out, "grid" if args.grid else "cropped", args.overwrite) if cut else None
    if args.box:
        used = clamp(h.width, h.height, *args.box)
        image, used = box(decode(h, parts, used[3], used[2]), *used)
        result["box"] = list(used)
    else:
        image = decode(h, parts)
    if args.squeeze:
        image, result["squeezed_rows"], result["squeezed_columns"] = squeeze(image, gap)
    if args.grid:
        image = grid(image)
    folder = None
    try:
        if args.edges:
            folder, paths = scratch(), {}
            for end, strip in zip(("top", "bottom"), strips(image, args.edges)):
                paths[end] = folder / f"{(out or source).stem}-{end}.png"
                write(paths[end], encode_png(strip))
            result["edges"] = {end: str(path.resolve()) for end, path in paths.items()}
        if out:
            write(out, encode_png(image))
            result.update(out=str(out.resolve()), out_width=image.width, out_height=image.height)
    except BaseException:
        if folder:
            shutil.rmtree(folder, ignore_errors=True)
        raise
    return result


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # Windows writes a piped stdout in the ANSI code page, which lacks most of Unicode
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("image", help="the PNG to cut down")
    p.add_argument("--box", type=parse_box, metavar="LEFT,TOP,RIGHT,BOTTOM",
                   help="the region to keep, in pixels; RIGHT and BOTTOM are not included")
    p.add_argument("--squeeze", action="store_true", help="cut every run of identical rows, then columns, longer than --gap to --gap")
    p.add_argument("--gap", type=int, metavar="N", help=f"the pixels a squeezed run keeps (default {GAP})")
    p.add_argument("--grid", action="store_true", help="write a copy ruled every 100 pixels, to read a box from")
    p.add_argument("--edges", type=int, metavar="N",
                   help="write the first and last N rows of the result at full width to a fresh temporary folder, "
                        "to look at its edges; with no other operation, of the input")
    p.add_argument("--out", metavar="FILE", help="the file to write (default IMAGE-cropped.png, or IMAGE-grid.png)")
    p.add_argument("--overwrite", action="store_true", help="let --out replace a file that exists")
    argv = sys.argv[1:] if argv is None else list(argv)
    for i in range(len(argv) - 1):  # a box with a negative edge, --box -5,0,900,600, would read as an option without its value
        if argv[i] == "--box":
            argv[i:i + 2] = [f"--box={argv[i + 1]}"]
            break
    args = p.parse_args(argv)
    try:
        print(json.dumps(run(args)))
    except Problem as e:
        sys.exit(str(e))
    except Exception as e:  # anything unforeseen is shown without a traceback
        sys.exit(f"stopped by {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
