"""Read the timing of an embedded text subtitle track from a Matroska (MKV) file.

Only the parts of the file that are needed are read, so this also works for streamed
files (debrid, Usenet): the headers, the cue index and a sample of subtitle blocks.
"""
import time

SEGMENT, SEEKHEAD, SEEK, SEEKID, SEEKPOS = 0x18538067, 0x114D9B74, 0x4DBB, 0x53AB, 0x53AC
INFO, TIMESCALE, DURATION = 0x1549A966, 0x2AD7B1, 0x4489
TRACKS, TRACKENTRY, TRACKNUMBER, TRACKTYPE, CODECID = 0x1654AE6B, 0xAE, 0xD7, 0x83, 0x86
LANGUAGE, LANGUAGE_BCP47, FLAGFORCED, FLAGDEFAULT, TRACKNAME = 0x22B59C, 0x22B59D, 0x55AA, 0x88, 0x536E
CUES, CUEPOINT, CUETIME, CUETRACKPOS = 0x1C53BB6B, 0xBB, 0xB3, 0xB7
CUETRACK, CUECLUSTERPOS, CUERELPOS = 0xF7, 0xF1, 0xF0
CLUSTER, CLUSTERTIME, SIMPLEBLOCK, BLOCKGROUP, BLOCK, BLOCKDURATION = (
    0x1F43B675, 0xE7, 0xA3, 0xA0, 0xA1, 0x9B)
UNKNOWN = -1
TEXT_CODECS = ('S_TEXT/UTF8', 'S_TEXT/ASS', 'S_TEXT/SSA', 'S_TEXT/WEBVTT', 'S_TEXT/ASCII')


class Limit(Exception):
    pass


class Source:
    """Random access reader with a small chunk cache and a byte/time budget."""

    def __init__(self, f, max_bytes=150 * 1024 * 1024, max_seconds=25):
        self.f = f  # object with seek(pos) and read(n) -> bytes
        self.chunks = {}
        self.read_total = 0
        self.max_bytes = max_bytes
        self.deadline = time.time() + max_seconds
        self.chunk = 64 * 1024

    def read(self, pos, n):
        out = bytearray()
        while n > 0:
            base = pos - pos % self.chunk
            data = self.chunks.get(base)
            if data is None:
                if self.read_total > self.max_bytes or time.time() > self.deadline:
                    raise Limit()
                self.f.seek(base)
                data = self.f.read(self.chunk)
                self.read_total += len(data)
                if len(self.chunks) > 256:
                    self.chunks.clear()
                self.chunks[base] = data
            off = pos - base
            piece = data[off:off + n]
            if not piece:
                break
            out += piece
            pos += len(piece)
            n -= len(piece)
        return bytes(out)


def _vint(src, pos, is_id):
    first = src.read(pos, 1)
    if not first:
        raise EOFError
    b = first[0]
    length = 1
    mask = 0x80
    while length <= 8 and not b & mask:
        mask >>= 1
        length += 1
    if length > 8:
        raise ValueError('bad vint at %d' % pos)
    raw = src.read(pos, length)
    value = raw[0] if is_id else raw[0] & (mask - 1)
    for c in raw[1:]:
        value = (value << 8) | c
    if not is_id and value == (1 << (7 * length)) - 1:
        value = UNKNOWN
    return value, length


def header(src, pos):
    """(element id, data size, data start) of the element at pos."""
    eid, n1 = _vint(src, pos, True)
    size, n2 = _vint(src, pos + n1, False)
    return eid, size, pos + n1 + n2


def children(src, start, end):
    pos = start
    while end == UNKNOWN or pos < end:
        try:
            eid, size, data = header(src, pos)
        except EOFError:
            return
        yield eid, size, data
        if size == UNKNOWN:
            return
        pos = data + size


def _uint(src, pos, size):
    v = 0
    for c in src.read(pos, size):
        v = (v << 8) | c
    return v


def _float(src, pos, size):
    import struct
    raw = src.read(pos, size)
    return struct.unpack('>f' if size == 4 else '>d', raw)[0]


def _str(src, pos, size):
    return src.read(pos, size).split(b'\0')[0].decode('utf-8', 'replace')


class MKV:
    def __init__(self, src):
        self.src = src
        self.timescale = 1000000  # ns per tick
        self.duration_ms = 0
        self.tracks = []
        self.cues = []  # (time_ms, track, cluster_pos, rel_pos or None)
        eid, size, data = header(src, 0)
        if eid != 0x1A45DFA3:
            raise ValueError('not a Matroska file')
        eid, size, data = header(src, data + size)
        if eid != SEGMENT:
            raise ValueError('no Segment')
        self.seg = data
        self.seg_end = UNKNOWN if size == UNKNOWN else data + size
        found = {}  # element id -> (data start, size)
        seeks = {}
        for eid, size, pos in children(src, self.seg, self.seg_end):
            if eid == CLUSTER:
                break
            found.setdefault(eid, (pos, size))
            if eid == SEEKHEAD:
                for sid, ssize, spos in children(src, pos, pos + size):
                    if sid != SEEK:
                        continue
                    target = offset = None
                    for k, ksize, kpos in children(src, spos, spos + ssize):
                        if k == SEEKID:
                            target = _uint(src, kpos, ksize)
                        elif k == SEEKPOS:
                            offset = _uint(src, kpos, ksize)
                    if target and offset is not None:
                        seeks.setdefault(target, self.seg + offset)
        for target, at in seeks.items():
            if target not in found and target in (INFO, TRACKS, CUES):
                eid, size, data = header(src, at)
                if eid == target:
                    found[target] = (data, size)
        if INFO in found:
            self._info(*found[INFO])
        if TRACKS in found:
            self._tracks(*found[TRACKS])
        if CUES in found:
            self._cues(*found[CUES])

    def _info(self, pos, size):
        for eid, esize, data in children(self.src, pos, pos + size):
            if eid == TIMESCALE:
                self.timescale = _uint(self.src, data, esize)
            elif eid == DURATION:
                self.duration_ms = _float(self.src, data, esize)
        self.duration_ms = self.duration_ms * self.timescale / 1e6

    def _tracks(self, pos, tsize):
        for eid, size, data in children(self.src, pos, pos + tsize):
            if eid != TRACKENTRY:
                continue
            t = {'forced': 0, 'default': 1, 'language': 'eng', 'name': ''}
            for k, ksize, kpos in children(self.src, data, data + size):
                if k == TRACKNUMBER:
                    t['number'] = _uint(self.src, kpos, ksize)
                elif k == TRACKTYPE:
                    t['type'] = _uint(self.src, kpos, ksize)
                elif k == CODECID:
                    t['codec'] = _str(self.src, kpos, ksize)
                elif k in (LANGUAGE, LANGUAGE_BCP47):
                    t['language'] = _str(self.src, kpos, ksize)
                elif k == FLAGFORCED:
                    t['forced'] = _uint(self.src, kpos, ksize)
                elif k == FLAGDEFAULT:
                    t['default'] = _uint(self.src, kpos, ksize)
                elif k == TRACKNAME:
                    t['name'] = _str(self.src, kpos, ksize)
            self.tracks.append(t)

    def _cues(self, pos, csize):
        for eid, size, data in children(self.src, pos, pos + csize):
            if eid != CUEPOINT:
                continue
            t = None
            entries = []
            for k, ksize, kpos in children(self.src, data, data + size):
                if k == CUETIME:
                    t = _uint(self.src, kpos, ksize)
                elif k == CUETRACKPOS:
                    tr = cp = rp = None
                    for j, jsize, jpos in children(self.src, kpos, kpos + ksize):
                        if j == CUETRACK:
                            tr = _uint(self.src, jpos, jsize)
                        elif j == CUECLUSTERPOS:
                            cp = _uint(self.src, jpos, jsize)
                        elif j == CUERELPOS:
                            rp = _uint(self.src, jpos, jsize)
                    entries.append((tr, cp, rp))
            if t is None:
                continue
            for tr, cp, rp in entries:
                if cp is not None:
                    self.cues.append((t * self.timescale / 1e6, tr, self.seg + cp, rp))

    # ------------------------------------------------------------------ blocks

    def text_tracks(self):
        return [t for t in self.tracks if t.get('type') == 0x11 and t.get('codec') in TEXT_CODECS]

    def _cluster(self, pos):
        """(cluster data start, cluster end, cluster timestamp ms)."""
        eid, size, data = header(self.src, pos)
        if eid != CLUSTER:
            raise ValueError('no cluster at %d' % pos)
        end = UNKNOWN if size == UNKNOWN else data + size
        ts = 0
        for k, ksize, kpos in children(self.src, data, end):
            if k == CLUSTERTIME:
                ts = _uint(self.src, kpos, ksize)
                break
        return data, end, ts

    def _block(self, eid, size, pos, cluster_ts, track):
        """(start_ms, end_ms) of a subtitle block, or None if it belongs to another track."""
        dur = None
        if eid == BLOCKGROUP:
            bpos = bsize = None
            for k, ksize, kpos in children(self.src, pos, pos + size):
                if k == BLOCK:
                    bpos, bsize = kpos, ksize
                elif k == BLOCKDURATION:
                    dur = _uint(self.src, kpos, ksize)
            if bpos is None:
                return None
            pos, size = bpos, bsize
        tr, n = _vint(self.src, pos, False)
        if tr != track:
            return None
        head = self.src.read(pos + n, 3)
        rel = int.from_bytes(head[:2], 'big', signed=True)
        start = (cluster_ts + rel) * self.timescale / 1e6
        end = start + dur * self.timescale / 1e6 if dur else None
        return start, end

    def _scan_cluster(self, pos, track, out):
        data, end, ts = self._cluster(pos)
        nxt = None
        for eid, size, epos in children(self.src, data, end):
            if eid in (SIMPLEBLOCK, BLOCKGROUP):
                ev = self._block(eid, size, epos, ts, track)
                if ev:
                    out.append(ev)
            elif eid == CLUSTER:
                nxt = epos
                break
        if end != UNKNOWN:
            nxt = end
        return nxt, ts * self.timescale / 1e6

    def events(self, track, max_events=80, log=None):
        """Sample of (start_ms, end_ms) for the subtitle track."""
        num = track['number']
        out = []
        try:
            sub_cues = [c for c in self.cues if c[1] == num]
            if sub_cues:
                step = max(1, len(sub_cues) // max_events)
                for t, tr, cpos, rel in sub_cues[::step]:
                    try:
                        data, end, ts = self._cluster(cpos)
                    except (EOFError, ValueError):
                        continue
                    if rel is not None:
                        eid, size, epos = header(self.src, data + rel)
                        ev = self._block(eid, size, epos, ts, num)
                        if ev:
                            out.append(ev)
                    else:
                        self._scan_cluster(cpos, num, out)
                if log:
                    log('read %d subtitle blocks via cues (%d KB)' % (len(out), self.src.read_total // 1024))
                return _dedupe(out)
            # No cue entries for the subtitle track: scan a few windows spread over the video
            vids = sorted({(round(c[0]), c[2]) for c in self.cues})
            if not vids:
                return []
            duration = self.duration_ms or vids[-1][0]
            self.src.chunk = 1024 * 1024  # sequential reading
            windows = 12
            per_window = max(6, max_events // windows)
            for i in range(windows):
                target = duration * (i + 0.5) / windows
                start = [v for v in vids if v[0] <= target] or vids[:1]
                pos = start[-1][1]
                got = []
                t0 = None
                while pos and len(got) < per_window:
                    try:
                        pos, cts = self._scan_cluster(pos, num, got)
                    except (EOFError, ValueError):
                        break  # end of the clusters (e.g. Cues or end of file)
                    t0 = cts if t0 is None else t0
                    if cts - t0 > 90000:
                        break
                out += got
            if log:
                log('read %d subtitle blocks by scanning (%d MB)' % (len(out), self.src.read_total // 1048576))
        except Limit:
            if log:
                log('read limit reached after %d blocks' % len(out))
        return _dedupe(out)


def _dedupe(events):
    seen = set()
    res = []
    for s, e in sorted(events):
        k = round(s)
        if k not in seen:
            seen.add(k)
            res.append((s, e))
    return res


def pick_track(tracks):
    """Prefer full (non-forced) text tracks; English or the default one first."""
    full = [t for t in tracks if not t.get('forced') and 'forced' not in t.get('name', '').lower()]
    pool = full or tracks
    pool.sort(key=lambda t: (not t.get('language', '').startswith('en'), not t.get('default')))
    return pool[0] if pool else None
