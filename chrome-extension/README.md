# Lecture Grabber (Phase 1)

Chrome extension that downloads a Panopto lecture's video and caption
transcript from Canvas, for the lecture-to-notes pipeline.

## How it works

1. A content script runs on every Panopto page (including the iframes Canvas
   embeds). It reads the lecture's delivery ID from the URL and calls
   Panopto's own `DeliveryInfo` API — the same call the video player makes —
   to get the direct stream URLs and the session title. Your existing login
   cookies authenticate the request.
2. It also checks the caption endpoint (`GenerateSRT.ashx`) so the popup can
   tell you whether a transcript exists.
3. As a fallback, the background worker watches Panopto network traffic and
   records any `.mp4` / `.m3u8` URLs the player loads, in case the API route
   doesn't work on a particular tenant.
4. Direct `.mp4` links download in one click. HLS streams (`.m3u8`) open a
   downloader page that fetches every segment (decrypting AES-128 if needed)
   and assembles them into a single `.ts` file.

## Install (developer mode)

1. Open `chrome://extensions` in Chrome.
2. Turn on **Developer mode** (top-right toggle).
3. Click **Load unpacked** and select this `chrome-extension/` folder.

## Usage

1. In Canvas, open the lecture so the Panopto player is visible (either the
   embedded player or the full Panopto viewer page). Make sure you're logged
   in.
2. Click the **Lecture Grabber** icon. A green badge on the icon means a
   lecture was detected.
3. Click **Download video** and **Download transcript (.srt)**.

If nothing is detected, press play on the video for a few seconds, then
reopen the popup — the network sniffer will have caught the stream URLs.

## Output

- `<Lecture title>.mp4` — or `<Lecture title>.ts` when the stream is HLS.
  `.ts` files work directly with ffmpeg and the rest of the pipeline; to
  convert losslessly: `ffmpeg -i lecture.ts -c copy lecture.mp4`
- `<Lecture title>.srt` — the caption transcript with timestamps (Phase 2
  input).

## Notes & limitations

- The HLS downloader holds the video in memory while assembling it. Typical
  1-hour lectures (a few hundred MB) are fine; keep the downloader tab open
  until it finishes.
- Some lectures have multiple streams (camera + screen capture). The popup
  lists each one; the "podcast" stream, when present, is the combined view.
- If captions show "not available," the lecture has no transcript in
  Panopto. (A later pipeline phase can fall back to local speech-to-text,
  e.g. Whisper.)
- Only use this on lectures you have legitimate access to through your
  course enrollment.
