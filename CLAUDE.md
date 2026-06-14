# Lecture-to-Notes Synthesizer

Pipeline that turns a recorded university lecture (Panopto video + captions)
into a textbook-quality LaTeX study guide PDF. Owner: Omer (obelyaev@scu.edu),
student at Santa Clara University. Full spec: `What it does.md`,
build order: `5-phase-plan.md`, gold-standard output example:
`Lecture 26 - Trees and Spanning Trees.pdf` (LuaTeX, theorem/proof style,
native vector diagrams).

## Key decisions (do not re-litigate)

- **Everything becomes native LaTeX.** Board diagrams are redrawn as TikZ,
  never embedded as cleaned-up screenshots. The bar is the example PDF.
- **★ exam flags must be high-precision.** Only explicit assessment
  references ("on the final", "you should definitely know") earn ★.
  Generic emphasis ("this is important") goes into JSON metadata for the
  LLM fusion stage to judge in context — never ★ by regex.
- **Model strategy (proven end-to-end, 2026-06-14):** a hybrid that keeps
  ~95% of the work free and local and uses Claude only for one finishing
  pass. Runs on the owner's desktop (Windows, 32 GB RAM, **AMD RX 5700 XT,
  8 GB VRAM** — Ollama uses the GPU via ROCm):
  - **Board transcription → `qwen2.5vl:7b`** (Ollama). A vision model is
    essential here; `qwen2.5vl:7b` reads handwritten board math *far* better
    than gemma3 (gemma3:4b garbled the proofs — wrong edge-count formulas,
    hallucinated content). 27b never fit the 8 GB card; don't bother.
  - **Fusion (chunk → prose) → `gemma3:12b`** (Ollama). Text reasoning.
  - **Finisher → `claude` CLI** (subscription, headless `-p`). One pass to
    dedup, repair LaTeX, and fix diagrams.
  Backends are swappable via `--backend ollama|claude` / `--model`. Why not
  Claude for the boards too? It's more faithful but ~3× the Claude usage per
  lecture and re-spent on every re-run; the local-vision split keeps a whole
  semester nearly free. See README for the cost trade-off.

## Components (all in this folder)

| Component | Status | How to run |
|---|---|---|
| `chrome-extension/` | ✅ works on SCU Panopto | Load unpacked at chrome://extensions. Downloads video (.mp4, incl. camera-only streams via byte-range HLS) + transcript (.srt, sometimes saved as .txt). Camera 1 = full-frame blackboard for CSEN 19. |
| `board-extractor/` | ✅ tested on real lecture | `.venv/bin/python extract_boards.py <video> -o <outdir> --debug-csv`. Color-detects the green board, tracks 8 vertical strips, saves one PNG per board state before each erase. 33 snapshots from a 65-min lecture. Venv has opencv. |
| `latex-converter/convert_lecture.py` | ✅ runs free on the RX 5700 XT | `python3 convert_lecture.py <snapshot-folder> -o output/name --model qwen2.5vl:7b`. Stage 1 (the keeper): per-image transcribe → `.fragments.json` (resumable, cached per board). Stages 2–3 (merge + compile a boards-only PDF) are a bonus you can ignore — the real pipeline fuses the fragments next. Skips the `board-region.png` debug image. `--limit N` for a quick timing test. |
| `latex-test/` | ✅ proof of concept / benchmark | `boards-24-25.tex/pdf`: manual reconstruction of 2 snapshots, used as the fidelity benchmark when comparing board models (qwen ≈ this; gemma3:4b ≪ this). |
| `transcript-engine/` | ✅ tested on real lecture | `python3 process_transcript.py <captions.srt/.txt> -o output/name --title "..."`. Deterministic: SRT parse → filler strip → paragraph merge → ★ flags. Outputs .md (human) + .json (for fusion; has `stars`, `emphasis`, timestamps). |
| `latex-converter/fuse_lecture.py` | ✅ runs free on the RX 5700 XT | `python3 fuse_lecture.py <transcript.json> <fragments.json> -o output/name --model gemma3:12b`. Interleaves spoken paragraphs + board fragments chronologically (snapshots are at erase time, so time-sorting puts each board after the speech that produced it), splits into chunks of ≤`--boards-per-chunk` boards, and writes one LaTeX **body** per chunk (we own the preamble). Resumable via `<out>.bodies.json`; retries transient Ollama 500s. Produces a **rough draft** (repetition + LaTeX bugs are expected) — the finisher cleans it. `--dry-run` writes the chunk plan, no model calls. |
| `latex-converter/finish_lecture.py` | ✅ produces the final PDF | `python3 finish_lecture.py <out>.bodies.json -o output/name-final --title "..."`. ONE `claude` CLI pass over the assembled draft (fed on stdin): dedup repeated theorems, repair LaTeX, redraw trivial diagrams — keep all math — then tectonic compile (+ up to 2 Claude fix-ups). The only step that uses Claude. |

Compiler: `tectonic` (on the desktop). Claude CLI v2.1.177 installed +
authenticated (Pro) on the desktop. Test data: CSEN 19 Discrete Math lecture
of 2026-06-05 — same topic as the gold-standard PDF, ideal for comparison.
The 2026-06-14 run (`output/csen19-final.pdf`) reached gold-standard quality:
correct dedup'd theorem, three rigorous proofs, real TikZ diagrams, exam
boxes, dominating-set + bipartite material.

## Current state: COMPLETE (2026-06-14)

The full pipeline works end-to-end and hit gold-standard quality on the
CSEN 19 lecture. Setup (Ollama + GPU, models, Claude CLI) is done on the
desktop. To process a new lecture, run the four stages in order:

```
# 1. board snapshots from the video (needs the .venv with opencv)
board-extractor/.venv/bin/python board-extractor/extract_boards.py <video.mp4> -o boards/<name>

# 2. transcript → structured JSON (deterministic, no model)
python3 transcript-engine/process_transcript.py <captions.srt> -o output/<name> --title "<Title>"

# 3a. boards → LaTeX fragments  (FREE, local vision model on the GPU)
python3 latex-converter/convert_lecture.py boards/<name> -o output/<name> --model qwen2.5vl:7b
# 3b. fuse transcript + fragments → rough draft  (FREE, local)
python3 latex-converter/fuse_lecture.py output/<name>.json output/<name>.fragments.json -o output/<name>-fused --model gemma3:12b

# 4. polish into the final study guide  (one Claude CLI pass)
python3 latex-converter/finish_lecture.py output/<name>-fused.bodies.json -o output/<name>-final --title "<Title>"
```

Every stage is resumable (re-running reuses cached `.fragments.json` /
`.bodies.json`). On Windows use `py -3.12` and run from the repo root.

Possible future polish (none required): a single wrapper script for all
four stages; validate on another lecture (`math13-06-03` is in the test
data); have the finisher print exact token usage / use `--bare`.

The repo lives at github.com/kirbb3/latex-pdf-project (imported from the
owner's Drive on 2026-06-13; board snapshot PNGs, videos and .venv
intentionally not committed — regenerate with board-extractor).

## Gotchas learned the hard way

- Panopto serves video from cloudfront.net (host permission needed) and
  its HLS playlists are byte-ranges of ONE file — download in a single
  ranged request, don't stitch 433 segments.
- DeliveryInfo JSON contains bare filenames that look like .mp4 URLs;
  only accept absolute http(s) URLs.
- The combined "podcast" render is mostly projector screensaver with the
  blackboard in a tiny inset — always use the camera-only stream.
- Whole-frame ink scoring fails: people are edge-dense (a group photo beat
  every real board), and professors erase per-panel so global ink never
  drops. Score the color-detected board region only, per strip.
- This folder lives in iCloud-synced Documents: files can be evicted to
  "dataless" stubs that time out on read (fix: delete + rewrite, or
  `brctl download`). Mind disk space — it was at 99%.
