# Game servers: players online, asked the game's own way rather than "is the port open".
#
#   LABHUD_GAMES   name=kind://host:port, comma separated; kind is
#                  a2s        Valve's A2S_INFO (Source and Steam games: CS, Rust, ARK, Valheim,
#                             7 Days, Palworld...: use the query port, often the game port + 1)
#                  minecraft  Java edition's status ping (25565)
#                  e.g. rust=a2s://192.0.2.70:28017,mc=minecraft://192.0.2.71:25565
#
# Data: games.{online, total, players, servers}; `servers` is a card list (list = "games.servers"),
# and every server also as games.<name>.{up, players, max, map, version}.

import concurrent.futures
import json
import socket
import struct
import urllib.parse

from ._common import env, source

A2S_INFO = b"\xff\xff\xff\xffTSource Engine Query\x00"


def _cstr(data, i):
    end = data.index(b"\x00", i)
    return data[i:end].decode("utf-8", "replace"), end + 1


def parse_a2s(data):
    """{name, map, game, players, max, bots} from an A2S_INFO answer (header 0x49)."""
    if data[:5] != b"\xff\xff\xff\xffI":
        raise ValueError("not an A2S_INFO answer")
    i = 6  # header + protocol byte
    name, i = _cstr(data, i)
    game_map, i = _cstr(data, i)
    _folder, i = _cstr(data, i)
    game, i = _cstr(data, i)
    i += 2  # app id
    players, maxp, bots = data[i], data[i + 1], data[i + 2]
    return {"name": name, "map": game_map, "game": game, "players": players, "max": maxp, "bots": bots}


def query_a2s(host, port, timeout=3):
    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        s.sendto(A2S_INFO, (host, port))
        data, _ = s.recvfrom(1400)
        if data[:5] == b"\xff\xff\xff\xffA":  # a challenge: ask again with it (since late 2020)
            s.sendto(A2S_INFO + data[5:9], (host, port))
            data, _ = s.recvfrom(1400)
    return parse_a2s(data)


def _varint(n):
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def _read_varint(sock):
    n = shift = 0
    while True:
        b = sock.recv(1)
        if not b:
            raise OSError("closed")
        n |= (b[0] & 0x7F) << shift
        if not b[0] & 0x80:
            return n
        shift += 7
        if shift > 35:
            raise ValueError("bad varint")


def query_minecraft(host, port, timeout=4):
    with socket.create_connection((host, port), timeout=timeout) as s:
        h = host.encode()
        body = _varint(0) + _varint(767) + _varint(len(h)) + h + struct.pack(">H", port) + _varint(1)
        s.sendall(_varint(len(body)) + body + _varint(1) + _varint(0))
        _read_varint(s)  # packet length
        _read_varint(s)  # packet id
        size = _read_varint(s)
        raw = b""
        while len(raw) < size:
            chunk = s.recv(size - len(raw))
            if not chunk:
                raise OSError("closed")
            raw += chunk
    st = json.loads(raw)
    desc = st.get("description")
    motd = desc.get("text", "") if isinstance(desc, dict) else str(desc or "")
    return {"name": motd, "players": (st.get("players") or {}).get("online", 0),
            "max": (st.get("players") or {}).get("max", 0), "version": (st.get("version") or {}).get("name", "")}


def servers(value):
    out = []
    for item in (x.strip() for x in value.split(",") if x.strip()):
        name, _, url = item.partition("=")
        u = urllib.parse.urlsplit(url.strip())
        if u.scheme not in ("a2s", "minecraft") or not u.hostname or not u.port:
            raise ValueError(f"LABHUD_GAMES: {item!r} is not name=a2s://host:port or name=minecraft://host:port")
        out.append((name.strip().lower(), u.scheme, u.hostname, u.port))
    return out


@source("games", every=30, env=("GAMES",),
        title="Game servers", about="Players online on Steam (A2S) and Minecraft servers, asked the game's own way.",
        hints={"GAMES": "name=a2s://host:query-port,name=minecraft://host:25565"})
def games():
    targets = servers(env("GAMES"))

    def one(t):
        name, kind, host, port = t
        try:
            info = query_a2s(host, port) if kind == "a2s" else query_minecraft(host, port)
            return name, dict(info, up=True)
        except (OSError, ValueError, IndexError, KeyError):
            return name, {"up": False}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        found = dict(pool.map(one, targets))
    rows = []
    for name, _, _, _ in targets:
        g = found[name]
        if g["up"]:
            extra = g.get("map") or g.get("version") or ""
            rows.append({"name": name, "value": f"{g['players']}/{g['max']}" + (f" · {extra}" if extra else "")})
        else:
            rows.append({"name": name, "value": "offline", "bad": True})
    out = {name: g for name, g in found.items()}
    out.update(online=sum(1 for g in found.values() if g["up"]), total=len(found),
               players=sum(g.get("players", 0) for g in found.values()), servers=rows)
    return out
