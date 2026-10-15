"""QR codes, for pairing a phone: the display's ⚙ shows one as SVG, `init.py pair` in the terminal.

Byte mode only, error correction M, versions 1-20 (up to 666 bytes; a pairing link is ~150).
Standard library only, like the rest of labhud. The construction follows ISO/IEC 18004 the way
Project Nayuki's QR Code generator (MIT) lays it out: function patterns, data with Reed-Solomon
blocks interleaved, the zigzag placement, then the mask with the lowest penalty.
"""

# Indexed by version (0 unused), for error correction level M.
ECC_PER_BLOCK = (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26)
BLOCKS = (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16)
FORMAT_M = 0        # the two format bits of level M
MAX_VERSION = 20


def _raw_modules(ver):
    """Modules left for data and error correction once the function patterns are drawn."""
    n = (16 * ver + 128) * ver + 64
    if ver >= 2:
        align = ver // 7 + 2
        n -= (25 * align - 10) * align - 55
        if ver >= 7:
            n -= 36
    return n


def _data_codewords(ver):
    return _raw_modules(ver) // 8 - ECC_PER_BLOCK[ver] * BLOCKS[ver]


def _mul(x, y):
    """Multiplication in GF(2^8) modulo x^8 + x^4 + x^3 + x^2 + 1."""
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree):
    out = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            out[j] = _mul(out[j], root)
            if j + 1 < degree:
                out[j] ^= out[j + 1]
        root = _mul(root, 0x02)
    return out


def _rs_remainder(data, divisor):
    out = [0] * len(divisor)
    for b in data:
        factor = b ^ out.pop(0)
        out.append(0)
        for i, coef in enumerate(divisor):
            out[i] ^= _mul(coef, factor)
    return out


def _alignment_positions(ver, size):
    if ver == 1:
        return []
    align = ver // 7 + 2
    step = (ver * 8 + align * 3 + 5) // (align * 4 - 4) * 2
    out = [6]
    pos = size - 7
    for _ in range(align - 1):
        out.insert(1, pos)
        pos -= step
    return out


class _Grid:
    def __init__(self, ver):
        self.ver = ver
        self.size = ver * 4 + 17
        self.dark = [[False] * self.size for _ in range(self.size)]
        self.fixed = [[False] * self.size for _ in range(self.size)]

    def put(self, x, y, dark):
        self.dark[y][x] = dark
        self.fixed[y][x] = True

    def functions(self):
        s = self.size
        for i in range(s):
            self.put(6, i, i % 2 == 0)
            self.put(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (s - 4, 3), (3, s - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < s and 0 <= y < s:
                        self.put(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = _alignment_positions(self.ver, s)
        last = len(pos) - 1
        for i, ax in enumerate(pos):
            for j, ay in enumerate(pos):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.put(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.format_bits(0)
        if self.ver >= 7:
            rem = self.ver
            for _ in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            bits = self.ver << 12 | rem
            for i in range(18):
                bit = (bits >> i) & 1 == 1
                a, b = s - 11 + i % 3, i // 3
                self.put(a, b, bit)
                self.put(b, a, bit)

    def format_bits(self, mask):
        data = FORMAT_M << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        bit = [(bits >> i) & 1 == 1 for i in range(15)]
        s = self.size
        for i in range(6):
            self.put(8, i, bit[i])
        self.put(8, 7, bit[6])
        self.put(8, 8, bit[7])
        self.put(7, 8, bit[8])
        for i in range(9, 15):
            self.put(14 - i, 8, bit[i])
        for i in range(8):
            self.put(s - 1 - i, 8, bit[i])
        for i in range(8, 15):
            self.put(8, s - 15 + i, bit[i])
        self.put(8, s - 8, True)

    def codewords(self, data):
        s, i = self.size, 0
        right = s - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(s):
                for j in range(2):
                    x = right - j
                    y = s - 1 - vert if ((right + 1) & 2) == 0 else vert
                    if not self.fixed[y][x] and i < len(data) * 8:
                        self.dark[y][x] = (data[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def apply_mask(self, mask):
        test = MASKS[mask]
        for y in range(self.size):
            for x in range(self.size):
                if not self.fixed[y][x] and test(x, y):
                    self.dark[y][x] = not self.dark[y][x]


MASKS = (
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
)


def _penalty(m):
    """Rules 1, 2 and 4 of the standard (runs, 2x2 blocks, balance). Rule 3 (finder-like patterns)
    only nudges the choice and is left out: every mask gives a valid code."""
    s, score = len(m), 0
    for lines in (m, list(zip(*m))):
        for row in lines:
            run, last = 0, None
            for v in row:
                if v == last:
                    run += 1
                    if run == 5:
                        score += 3
                    elif run > 5:
                        score += 1
                else:
                    run, last = 1, v
    for y in range(s - 1):
        for x in range(s - 1):
            if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    dark = sum(map(sum, m))
    score += abs(dark * 20 - s * s * 10) // (s * s) * 10
    return score


def encode(text):
    """The QR code for `text` as rows of booleans (True = dark), without the quiet zone."""
    data = text.encode("utf-8")
    for ver in range(1, MAX_VERSION + 1):
        count_bits = 8 if ver <= 9 else 16
        capacity = _data_codewords(ver)
        if 4 + count_bits + len(data) * 8 <= capacity * 8:
            break
    else:
        raise ValueError(f"too long for a QR code here ({len(data)} bytes)")
    bits = [0, 1, 0, 0] + [(len(data) >> i) & 1 for i in reversed(range(count_bits))]
    for b in data:
        bits += [(b >> i) & 1 for i in reversed(range(8))]
    bits += [0] * min(4, capacity * 8 - len(bits))
    bits += [0] * (-len(bits) % 8)
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(words) < capacity:
        words.append(pad)
        pad ^= 0xEC ^ 0x11

    blocks_n, ecc_len = BLOCKS[ver], ECC_PER_BLOCK[ver]
    raw = _raw_modules(ver) // 8
    short_n = blocks_n - raw % blocks_n
    short_len = raw // blocks_n
    divisor = _rs_divisor(ecc_len)
    blocks, k = [], 0
    for i in range(blocks_n):
        part = words[k:k + short_len - ecc_len + (0 if i < short_n else 1)]
        k += len(part)
        ecc = _rs_remainder(part, divisor)
        if i < short_n:
            part = part + [0]
        blocks.append(part + ecc)
    final = [blk[i] for i in range(len(blocks[0])) for j, blk in enumerate(blocks)
             if i != short_len - ecc_len or j >= short_n]

    grid = _Grid(ver)
    grid.functions()
    grid.codewords(final)
    best, best_score = None, None
    for mask in range(8):
        grid.apply_mask(mask)
        grid.format_bits(mask)
        score = _penalty(grid.dark)
        if best_score is None or score < best_score:
            best, best_score = mask, score
        grid.apply_mask(mask)   # XOR again: back to unmasked
    grid.apply_mask(best)
    grid.format_bits(best)
    return grid.dark


def svg(text, border=4):
    """The code as a small SVG: one path, black on white, scaled by whoever shows it."""
    m = encode(text)
    n = len(m) + 2 * border
    path = "".join(f"M{x + border},{y + border}h1v1h-1z" for y, row in enumerate(m) for x, v in enumerate(row) if v)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" shape-rendering="crispEdges">'
            f'<rect width="{n}" height="{n}" fill="#fff"/><path d="{path}" fill="#000"/></svg>')


def terminal(text, border=2):
    """The code for a terminal: two rows per line with half blocks, black on white whatever the
    terminal's own colours (a phone does not read a light-on-dark code reliably)."""
    m = encode(text)
    s = len(m)
    full = [[False] * (s + 2 * border) for _ in range(border)]
    full += [[False] * border + row + [False] * border for row in m]
    full += [[False] * (s + 2 * border) for _ in range(border + (s + 2 * border) % 2)]
    lines = []
    for y in range(0, len(full) - 1, 2):
        chars = "".join(" ▀▄█"[top + 2 * bottom] for top, bottom in zip(full[y], full[y + 1]))
        lines.append(f"\033[30;47m{chars}\033[0m")
    return "\n".join(lines)
