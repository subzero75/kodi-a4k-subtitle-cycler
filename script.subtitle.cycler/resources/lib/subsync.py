"""Find how an external subtitle is shifted (and stretched) against reference timings.

Only start times are compared, so the reference can be in any language: an Arabic SRT can
be synced to the English subtitle track of the video.
"""
from bisect import bisect_left

# Frame-rate conversions that commonly cause subtitles to drift over the movie
SCALES = (1.0, 25 / 23.976, 23.976 / 25, 24 / 23.976, 23.976 / 24, 25 / 24, 24 / 25)
MAX_OFFSET = 5 * 60 * 1000  # ms
BIN = 100  # ms
TOLERANCE = 350  # ms


def _matches(ref, ext_sorted, a, b, tol=TOLERANCE):
    """Pairs (ref_start, ext_start) where a*ext+b lands within tol of a reference start."""
    shifted = [a * e + b for e in ext_sorted]
    pairs = []
    for r in ref:
        i = bisect_left(shifted, r)
        best = None
        for j in (i - 1, i):
            if 0 <= j < len(shifted):
                d = abs(shifted[j] - r)
                if d <= tol and (best is None or d < best[0]):
                    best = (d, ext_sorted[j])
        if best:
            pairs.append((r, best[1]))
    return pairs


def _fit(pairs):
    """Least-squares a, b for r = a*e + b."""
    n = len(pairs)
    se = sum(e for r, e in pairs)
    sr = sum(r for r, e in pairs)
    see = sum(e * e for r, e in pairs)
    ser = sum(e * r for r, e in pairs)
    den = n * see - se * se
    if n < 2 or den == 0:
        return 1.0, (sr - se) / max(n, 1)
    a = (n * ser - se * sr) / den
    return a, (sr - a * se) / n


def find_shift(ref_starts, ext_starts):
    """Return dict(a, b, matched, total) or None when no confident match was found."""
    ref = sorted(ref_starts)
    if len(ref) > 250:  # an even sample is as good and keeps this fast on a TV box
        step = len(ref) / 250.0
        ref = [ref[int(i * step)] for i in range(250)]
    ext = sorted(ext_starts)
    if len(ref) < 5 or len(ext) < 5:
        return None
    best = None
    for a in SCALES:
        bins = {}
        for r in ref:
            lo = (r - MAX_OFFSET) / a
            hi = (r + MAX_OFFSET) / a
            for e in ext[bisect_left(ext, lo):bisect_left(ext, hi)]:
                k = int((r - a * e) // BIN)
                bins[k] = bins.get(k, 0) + 1
        if not bins:
            continue
        # strongest offset, counting the neighbouring bins too
        weight = lambda k: bins[k] + bins.get(k - 1, 0) + bins.get(k + 1, 0)
        k = max(bins, key=weight)
        b = (k + 0.5) * BIN
        # strongest offset at least 2 s away: with unrelated timings both peaks are similar
        far = [weight(j) for j in bins if abs(j - k) > 2000 // BIN]
        runner = max(far) if far else 0
        pairs = _matches(ref, ext, a, b)
        if len(pairs) >= 3:
            fa, fb = _fit(pairs)
            if abs(fa - a) < 0.002:  # refine but stay at this frame-rate ratio
                pairs2 = _matches(ref, ext, fa, fb, tol=250)
                if len(pairs2) >= len(pairs) * 0.9:
                    a, b, pairs = fa, fb, pairs2
        score = len(pairs)
        if best is None or score > best['matched'] or (score == best['matched'] and abs(a - 1) < abs(best['a'] - 1)):
            best = {'a': a, 'b': b, 'matched': score, 'total': len(ref), 'peak': weight(k), 'runner': runner}
    if not best:
        return None
    # Confidence: enough reference lines line up, well above what a random offset gives
    if best['matched'] < max(8, 0.5 * len(ref)) or best['peak'] < 1.6 * best['runner'] + 2:
        return None
    return best


def apply(cues, a, b):
    """cues: list of [start, end, body]; returns shifted copies with times >= 0."""
    out = []
    for s, e, body in cues:
        ns = int(round(a * s + b))
        ne = int(round(a * e + b))
        if ne <= 0:
            continue
        out.append([max(0, ns), ne, body])
    return out
