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
- **Model strategy:** local Ollama on the owner's desktop PC (32 GB RAM,
  target model `gemma3:27b`) as the free default backend; the `claude` CLI
  (installed, v2.1.173, authenticated) as the swappable alternative.
  The MacBook (M2, 16 GB) is too small for 27b; 12b barely fits, 4b is the
  local fallback.

## Components (all in this folder)

| Component | Status | How to run |
|---|---|---|
| `chrome-extension/` | ✅ works on SCU Panopto | Load unpacked at chrome://extensions. Downloads video (.mp4, incl. camera-only streams via byte-range HLS) + transcript (.srt, sometimes saved as .txt). Camera 1 = full-frame blackboard for CSEN 19. |
| `board-extractor/` | ✅ tested on real lecture | `.venv/bin/python extract_boards.py <video> -o <outdir> --debug-csv`. Color-detects the green board, tracks 8 vertical strips, saves one PNG per board state before each erase. 33 snapshots from a 65-min lecture. Venv has opencv. |
| `latex-converter/` | ✅ code done; awaiting desktop Ollama | `python3 convert_lecture.py <snapshot-folder> -o output/name --backend ollama\|claude --model gemma3:27b --ollama-host http://<desktop-ip>:11434`. 3 stages: per-image transcribe → merge → tectonic compile with model fix loop. Fragments cached in `.fragments.json` (resumable). |
| `latex-test/` | ✅ proof of concept | `boards-24-25.tex/pdf`: manual reconstruction of 2 snapshots (done by Claude in-session) proving chalk→LaTeX quality is achievable, incl. occlusion recovery and TikZ diagrams. |
| `transcript-engine/` | ✅ tested on real lecture | `python3 process_transcript.py <captions.srt/.txt> -o output/name --title "..."`. Deterministic: SRT parse → filler strip → paragraph merge → ★ flags. Outputs .md (human) + .json (for fusion; has `stars`, `emphasis`, timestamps). |
| `latex-converter/fuse_lecture.py` | ✅ code done; awaiting desktop Ollama | `python3 fuse_lecture.py <transcript.json> <fragments.json> -o output/name --backend ollama\|claude --model gemma3:27b`. Fusion stage: interleaves spoken paragraphs + board fragments chronologically (snapshots are taken at erase time, so time-sorting puts each board after the speech that produced it), then one LLM pass writes the textbook chapter — board math spine, prose from speech, ★ exambox callouts, ASR fixes — then tectonic compile with fix loop. `--dry-run` writes the timeline without model calls (verified against the real CSEN 19 transcript + `test-data/csen19-sample.fragments.json`). |

Compiler: `tectonic` (installed via brew on the Mac). Test data: CSEN 19
Discrete Math lecture of 2026-06-05 (camera1 video + transcript in
~/Downloads) — same topic as the gold-standard PDF, ideal for comparison.

## Current state / next steps

1. **Desktop Ollama setup (blocker for full pipeline):** on the 32 GB PC
   install Ollama, set `OLLAMA_HOST=0.0.0.0`, `ollama pull gemma3:27b`.
   Then run latex-converter against it from any machine on the LAN.
   (Ollama 0.30.7 is installed on the Mac but no model pulled — disk was
   99% full; ~8 GB freed by deleting caches/duplicates, more cleanup
   planned by the owner.)
2. **First full pipeline run:** extension → board-extractor →
   convert_lecture (stages 1–2, producing `.fragments.json`) →
   fuse_lecture (transcript JSON + fragments → final PDF) on the
   2026-06-05 CSEN 19 lecture.
3. Quality pass comparing generated PDF vs `Lecture 26` gold standard.

The repo now lives at github.com/kirbb3/latex-pdf-project (imported from
the owner's Drive on 2026-06-13; board snapshot PNGs, videos and .venv
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
