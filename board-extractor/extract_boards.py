#!/usr/bin/env python3
"""Board snapshot extractor (Phase 3, step 1) — v2.

Scans a lecture video of a chalkboard and saves one clean still image per
"board state": each panel at its fullest, captured right before it gets
erased, plus the fullest clean view at the end of the lecture.

v2 lessons (from a real CSEN 19 lecture):
- Score ONLY the board region, found by color (green chalkboard), not the
  whole frame — desks/students otherwise dominate the signal.
- Track vertical strips of the board independently: professors fill several
  sliding panels and erase them one at a time, so a global score never dips.
- People in front of the board are occlusion, not content: a strip whose
  pixels stop looking like board+chalk is skipped, so a row of students
  posing for a photo can't win "fullest frame".
"""

import argparse
import csv
import os
import sys

import cv2
import numpy as np

ANALYSIS_WIDTH = 640  # downscale width for scoring

# HSV range that counts as "board surface". Chalk strokes are thin and don't
# flip a strip, so a written panel is still mostly board surface; a person
# blocking the panel covers that surface, which is how occlusion is detected.
BOARD_PRESETS = {
    # green chalkboard (incl. chalk-dust haze)
    "green": ((35, 20, 20), (105, 255, 220)),
    # dark slate / black chalkboard: dark, low-saturation pixels (chalk is
    # bright and excluded, but it's thin so the panel stays mostly "board")
    "dark": ((0, 0, 0), (180, 100, 130)),
}
# Active range — overwritten from --board in main().
BOARD_HSV_LO, BOARD_HSV_HI = BOARD_PRESETS["green"]


def fmt_ts(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:01d}h{(s % 3600) // 60:02d}m{s % 60:02d}s"


def ink_score(gray: np.ndarray) -> float:
    """Fraction of pixels that look like stroke edges (chalk lines)."""
    lap = cv2.Laplacian(gray, cv2.CV_16S, ksize=3)
    return float((np.abs(lap) > 40).mean())


def board_mask(bgr: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    return cv2.inRange(hsv, BOARD_HSV_LO, BOARD_HSV_HI)


def grab_frame(cap: cv2.VideoCapture, t: float):
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
    ok, frame = cap.read()
    return frame if ok else None


def small(frame: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    scale = ANALYSIS_WIDTH / w
    return cv2.resize(frame, (ANALYSIS_WIDTH, int(h * scale)))


def detect_board_bbox(cap: cv2.VideoCapture, duration: float):
    """Find the board rectangle from the median of sampled frames.

    The median over time removes the professor (they move around), leaving
    the static green board, which we locate by color.
    """
    frames = []
    for t in np.linspace(duration * 0.1, duration * 0.9, 15):
        f = grab_frame(cap, float(t))
        if f is not None:
            frames.append(small(f))
    if not frames:
        return None, None
    med = np.median(np.stack(frames), axis=0).astype(np.uint8)

    mask = board_mask(med)
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8)
    )
    contours, _ = cv2.findContours(
        mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return None, med
    big = max(contours, key=cv2.contourArea)
    x, y, w, h = cv2.boundingRect(big)
    if w * h < 0.08 * med.shape[0] * med.shape[1]:
        return None, med  # too small to be the board
    return (x, y, w, h), med


class StripTracker:
    """Watches one vertical strip of the board for fill/erase cycles."""

    def __init__(self, idx: int, args):
        self.idx = idx
        self.args = args
        self.peak = 0.0
        self.best_ink = -1.0
        self.best_t = None

    def update(self, t: float, ink: float, occluded: bool):
        """Returns the timestamp to snapshot if an erase just ended a cycle."""
        snap_t = None
        if occluded:
            return None
        if (
            self.peak >= self.args.min_ink
            and ink < self.peak * self.args.erase_ratio - 0.005
        ):
            snap_t = self.best_t
            self.peak = 0.0
            self.best_ink = -1.0
            self.best_t = None
        self.peak = max(self.peak, ink)
        if ink > self.best_ink:
            self.best_ink = ink
            self.best_t = t
        return snap_t


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("video", help="path to the lecture video (.mp4/.ts)")
    ap.add_argument("-o", "--out", default="output", help="output directory")
    ap.add_argument(
        "--interval", type=float, default=5.0, help="seconds between samples"
    )
    ap.add_argument(
        "--strips", type=int, default=8, help="vertical strips across the board"
    )
    ap.add_argument(
        "--erase-ratio",
        type=float,
        default=0.6,
        help="strip cycle ends when its ink falls below peak*ratio",
    )
    ap.add_argument(
        "--occl-max",
        type=float,
        default=0.30,
        help="strip is 'occluded' if more than this fraction is not board-colored",
    )
    ap.add_argument(
        "--min-ink",
        type=float,
        default=0.02,
        help="ignore strips with less stroke density than this (empty panel)",
    )
    ap.add_argument(
        "--dup-thresh",
        type=float,
        default=3.0,
        help="skip snapshot if it differs from a saved one less than this",
    )
    ap.add_argument(
        "--board",
        choices=sorted(BOARD_PRESETS),
        default="green",
        help="board surface color: 'green' chalkboard or 'dark' slate/black",
    )
    ap.add_argument(
        "--debug-csv",
        action="store_true",
        help="write per-sample strip scores to scores.csv for tuning",
    )
    args = ap.parse_args()

    global BOARD_HSV_LO, BOARD_HSV_HI
    BOARD_HSV_LO, BOARD_HSV_HI = BOARD_PRESETS[args.board]

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        print(f"error: cannot open {args.video}", file=sys.stderr)
        return 1
    os.makedirs(args.out, exist_ok=True)

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    nframes = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = nframes / fps if nframes else 0
    print(f"video: {os.path.basename(args.video)}")
    print(f"duration: {fmt_ts(duration)}, sampling every {args.interval:.0f}s")

    bbox, med = detect_board_bbox(cap, duration)
    if bbox is None:
        h = med.shape[0] if med is not None else int(
            ANALYSIS_WIDTH * 9 / 16
        )
        bbox = (0, 0, ANALYSIS_WIDTH, int(h * 0.6))
        print("warning: board not found by color; using top 60% of frame")
    bx, by, bw, bh = bbox
    print(f"board region (analysis px): x={bx} y={by} w={bw} h={bh}")
    if med is not None:
        cv2.imwrite(
            os.path.join(args.out, "board-region.png"),
            cv2.rectangle(med.copy(), (bx, by), (bx + bw, by + bh), (0, 0, 255), 2),
        )

    # Full-resolution crop box (for saving snapshots).
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    scale = src_w / ANALYSIS_WIDTH
    pad = 8  # a little context around the board
    full_box = (
        max(0, int(bx * scale) - pad),
        max(0, int(by * scale) - pad),
        int((bx + bw) * scale) + pad,
        int((by + bh) * scale) + pad,
    )

    def board_crop_small(frame_small):
        return frame_small[by : by + bh, bx : bx + bw]

    trackers = [StripTracker(i, args) for i in range(args.strips)]
    saved = []  # (t, small board gray) of saved snapshots
    count = 0
    debug_rows = []

    # Best clean full view of the board (for the end-of-lecture snapshot).
    final_best = {"t": None, "score": -1.0}

    def save_snapshot(t: float, label: str):
        nonlocal count
        frame = grab_frame(cap, t)
        if frame is None:
            return
        sm_gray = cv2.cvtColor(
            board_crop_small(small(frame)), cv2.COLOR_BGR2GRAY
        )
        for _, prev in saved:
            if float(cv2.absdiff(sm_gray, prev).mean()) < args.dup_thresh:
                return  # near-duplicate of something already saved
        x0, y0, x1, y1 = full_box
        crop = frame[y0:y1, x0:x1]
        count += 1
        name = f"board-{count:02d}_{fmt_ts(t)}.png"
        cv2.imwrite(os.path.join(args.out, name), crop)
        saved.append((t, sm_gray))
        print(f"  saved {name} ({label})")

    # ---- main scan ----
    pending_snaps = []  # timestamps to save (deferred so seeks stay ordered)
    t = 0.0
    while duration == 0 or t <= duration + args.interval:
        frame = grab_frame(cap, t)
        if frame is None:
            break
        sm = small(frame)
        board_bgr = board_crop_small(sm)
        board_gray = cv2.cvtColor(board_bgr, cv2.COLOR_BGR2GRAY)
        board_gray = cv2.GaussianBlur(board_gray, (3, 3), 0)
        not_board = 255 - board_mask(board_bgr)

        sw = board_bgr.shape[1] // args.strips
        clean_inks = []
        n_occluded = 0
        for i, tr in enumerate(trackers):
            x0 = i * sw
            x1 = (i + 1) * sw if i < args.strips - 1 else board_bgr.shape[1]
            ink = ink_score(board_gray[:, x0:x1])
            occl = float((not_board[:, x0:x1] > 0).mean()) > args.occl_max
            n_occluded += occl
            if not occl:
                clean_inks.append(ink)
            snap_t = tr.update(t, ink, occl)
            if snap_t is not None:
                pending_snaps.append((snap_t, f"strip {i + 1} erased at {fmt_ts(t)}"))
            if args.debug_csv:
                debug_rows.append(
                    (round(t, 1), i, round(ink, 5), int(occl))
                )

        # Candidate for the final "everything on the board" snapshot:
        # at most one strip blocked (the professor), maximal total ink.
        if n_occluded <= 1 and clean_inks:
            score = sum(clean_inks)
            if score >= final_best["score"]:
                final_best.update(t=t, score=score)

        if t % 600 < args.interval:
            print(f"  …scanned {fmt_ts(t)}")
        t += args.interval

    # ---- save results ----
    for snap_t, label in pending_snaps:
        save_snapshot(snap_t, label)
    if final_best["t"] is not None:
        save_snapshot(final_best["t"], "fullest clean view")
    cap.release()

    if args.debug_csv:
        with open(os.path.join(args.out, "scores.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t_seconds", "strip", "ink", "occluded"])
            w.writerows(debug_rows)
        print(f"wrote scores.csv ({len(debug_rows)} rows)")

    print(f"done: {count} board snapshot(s) in {args.out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
