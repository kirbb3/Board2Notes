// HLS downloader page. Fetches an .m3u8 playlist and saves the video.
//
// Panopto's playlists usually describe byte ranges of ONE underlying file
// (#EXT-X-BYTERANGE). In that case we download the whole file in a single
// streamed request — fast, and the result is a proper .mp4. Only truly
// segmented streams (separate files per segment, optionally AES-128
// encrypted) take the fetch-and-stitch path.
//
// Runs as an extension page, so host_permissions allow cross-origin fetches
// and credentials:"include" sends the user's session cookies.

const params = new URLSearchParams(location.search);
const SRC = params.get("src");
const TITLE = params.get("title") || "lecture";

const barEl = document.getElementById("bar");
const statusEl = document.getElementById("status");
const titleEl = document.getElementById("title");

function setStatus(text) {
  statusEl.textContent = text;
}
function setProgress(done, total) {
  barEl.style.width = total ? `${(100 * done) / total}%` : "0%";
}
function mb(bytes) {
  return `${(bytes / 1048576).toFixed(1)} MB`;
}
function fail(message) {
  titleEl.textContent = "Download failed";
  statusEl.innerHTML = "";
  const div = document.createElement("div");
  div.className = "error";
  div.textContent = message;
  statusEl.append(div);
}

function safeName(s) {
  return (
    s
      .replace(/[\\/:*?"<>|]+/g, "_")
      .replace(/[^\x20-\x7E]/g, "_")
      .replace(/_{2,}/g, "_")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 100)
      .replace(/[. _-]+$/, "") || "lecture"
  );
}

async function fetchText(url) {
  const res = await fetch(url, { credentials: "include" });
  if (!res.ok) throw new Error(`HTTP ${res.status} fetching ${url}`);
  return res.text();
}

// Returns [{bandwidth, url}] if `text` is a master playlist, else null.
function parseMaster(text, baseUrl) {
  if (!text.includes("#EXT-X-STREAM-INF")) return null;
  const lines = text.split(/\r?\n/);
  const variants = [];
  for (let i = 0; i < lines.length; i++) {
    if (!lines[i].startsWith("#EXT-X-STREAM-INF")) continue;
    const bwMatch = lines[i].match(/BANDWIDTH=(\d+)/);
    const bandwidth = bwMatch ? parseInt(bwMatch[1], 10) : 0;
    for (let j = i + 1; j < lines.length; j++) {
      const line = lines[j].trim();
      if (line && !line.startsWith("#")) {
        variants.push({ bandwidth, url: new URL(line, baseUrl).href });
        break;
      }
    }
  }
  return variants;
}

// Returns {segments: [{url, byterange}], key, mediaSequence, map}.
// byterange = {offset, length} or null. Implicit offsets (a BYTERANGE tag
// with no "@offset") continue where the previous segment ended.
function parseMedia(text, baseUrl) {
  const lines = text.split(/\r?\n/);
  const segments = [];
  let key = null;
  let mediaSequence = 0;
  let map = null;
  let lastEnd = 0;
  let pendingRange = null;

  for (const raw of lines) {
    const line = raw.trim();
    if (line.startsWith("#EXT-X-MEDIA-SEQUENCE:")) {
      mediaSequence = parseInt(line.split(":")[1], 10) || 0;
    } else if (line.startsWith("#EXT-X-KEY:")) {
      const method = (line.match(/METHOD=([A-Z0-9-]+)/) || [])[1] || "NONE";
      const uri = (line.match(/URI="([^"]+)"/) || [])[1] || null;
      const iv = (line.match(/IV=0[xX]([0-9a-fA-F]+)/) || [])[1] || null;
      key =
        method === "NONE"
          ? null
          : { method, uri: uri ? new URL(uri, baseUrl).href : null, iv };
    } else if (line.startsWith("#EXT-X-MAP:")) {
      const uri = (line.match(/URI="([^"]+)"/) || [])[1] || null;
      const br = line.match(/BYTERANGE="(\d+)(?:@(\d+))?"/);
      map = {
        url: uri ? new URL(uri, baseUrl).href : null,
        byterange: br
          ? { length: +br[1], offset: br[2] != null ? +br[2] : 0 }
          : null,
      };
    } else if (line.startsWith("#EXT-X-BYTERANGE:")) {
      const m = line.slice("#EXT-X-BYTERANGE:".length).match(/(\d+)(?:@(\d+))?/);
      if (m) {
        const length = +m[1];
        const offset = m[2] != null ? +m[2] : lastEnd;
        pendingRange = { length, offset };
      }
    } else if (line && !line.startsWith("#")) {
      const url = new URL(line, baseUrl).href;
      segments.push({ url, byterange: pendingRange });
      if (pendingRange) lastEnd = pendingRange.offset + pendingRange.length;
      pendingRange = null;
    }
  }
  return { segments, key, mediaSequence, map };
}

function extensionFor(url) {
  const path = url.split("?")[0].toLowerCase();
  if (path.endsWith(".mp4") || path.endsWith(".m4s") || path.endsWith(".m4v")) {
    return "mp4";
  }
  return "ts";
}

function saveBlob(parts, ext) {
  const blob = new Blob(parts, {
    type: ext === "mp4" ? "video/mp4" : "video/mp2t",
  });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${safeName(TITLE)}.${ext}`;
  document.body.append(a);
  a.click();
  return blob.size;
}

// One streamed request for byte range [start, end) of `url`.
async function downloadRange(url, start, end) {
  const headers = {};
  const total = end != null ? end - start : null;
  if (start > 0 || end != null) {
    headers["Range"] = `bytes=${start}-${end != null ? end - 1 : ""}`;
  }
  const res = await fetch(url, { credentials: "include", headers });
  if (!res.ok) throw new Error(`HTTP ${res.status} fetching video file`);

  const reader = res.body.getReader();
  const parts = [];
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    parts.push(value);
    received += value.length;
    setProgress(received, total);
    setStatus(
      total ? `${mb(received)} of ${mb(total)}` : `${mb(received)} downloaded…`
    );
  }
  return parts;
}

function hexToBytes(hex) {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(hex.substr(i * 2, 2), 16);
  }
  return out;
}

function ivForSequence(seq) {
  const iv = new Uint8Array(16);
  new DataView(iv.buffer).setUint32(12, seq);
  return iv;
}

async function importAesKey(uri) {
  const res = await fetch(uri, { credentials: "include" });
  if (!res.ok) throw new Error(`HTTP ${res.status} fetching AES key`);
  const raw = await res.arrayBuffer();
  return crypto.subtle.importKey("raw", raw, "AES-CBC", false, ["decrypt"]);
}

async function run() {
  if (!SRC) {
    fail("No playlist URL was passed to this page.");
    return;
  }
  titleEl.textContent = `Downloading: ${TITLE}`;

  let playlistUrl = SRC;
  let text = await fetchText(playlistUrl);

  const variants = parseMaster(text, playlistUrl);
  if (variants && variants.length > 0) {
    variants.sort((a, b) => b.bandwidth - a.bandwidth);
    playlistUrl = variants[0].url;
    setStatus(
      `Picked highest quality of ${variants.length} variant(s). Fetching segment list…`
    );
    text = await fetchText(playlistUrl);
  }

  const { segments, key, mediaSequence, map } = parseMedia(text, playlistUrl);
  if (segments.length === 0) {
    throw new Error("Playlist contains no media segments.");
  }

  // Fast path: every segment is a byte range of the same file, covering it
  // contiguously. Download the file once instead of stitching ranges.
  const oneFile =
    !key &&
    segments.every((s) => s.byterange && s.url === segments[0].url);
  if (oneFile) {
    let start = segments[0].byterange.offset;
    if (map && map.url === segments[0].url && map.byterange) {
      start = Math.min(start, map.byterange.offset);
    }
    let contiguous = true;
    let expect = segments[0].byterange.offset;
    for (const s of segments) {
      if (s.byterange.offset !== expect) {
        contiguous = false;
        break;
      }
      expect += s.byterange.length;
    }
    if (contiguous) {
      setStatus("Single-file stream detected — downloading in one request…");
      const parts = await downloadRange(segments[0].url, start, expect);
      const ext = extensionFor(segments[0].url);
      const size = saveBlob(parts, ext);
      titleEl.textContent = "Done";
      setStatus(
        `Saved ${safeName(TITLE)}.${ext} (${mb(size)}). ` +
          "You can close this tab once the file appears in your downloads."
      );
      return;
    }
  }

  // Segmented path: separate files (or non-contiguous ranges), fetched in
  // order, optionally AES-128 decrypted, stitched into one blob.
  let aesKey = null;
  if (key) {
    if (key.method !== "AES-128" || !key.uri) {
      throw new Error(`Unsupported encryption method: ${key.method}`);
    }
    aesKey = await importAesKey(key.uri);
  }

  const parts = [];
  let bytes = 0;

  if (map && map.url) {
    const r = map.byterange;
    const initParts = await downloadRange(
      map.url,
      r ? r.offset : 0,
      r ? r.offset + r.length : null
    );
    parts.push(...initParts);
  }

  for (let i = 0; i < segments.length; i++) {
    const s = segments[i];
    const headers = {};
    if (s.byterange) {
      headers["Range"] = `bytes=${s.byterange.offset}-${
        s.byterange.offset + s.byterange.length - 1
      }`;
    }
    const res = await fetch(s.url, { credentials: "include", headers });
    if (!res.ok) {
      throw new Error(
        `HTTP ${res.status} on segment ${i + 1}/${segments.length}`
      );
    }
    let buf = await res.arrayBuffer();
    if (aesKey) {
      const iv = key.iv
        ? hexToBytes(key.iv.padStart(32, "0"))
        : ivForSequence(mediaSequence + i);
      buf = await crypto.subtle.decrypt({ name: "AES-CBC", iv }, aesKey, buf);
    }
    parts.push(buf);
    bytes += buf.byteLength;
    setProgress(i + 1, segments.length);
    setStatus(`Segment ${i + 1} of ${segments.length} — ${mb(bytes)}`);
  }

  setStatus("Assembling file…");
  const ext = extensionFor(segments[0].url);
  const size = saveBlob(parts, ext);
  titleEl.textContent = "Done";
  setStatus(
    `Saved ${safeName(TITLE)}.${ext} (${mb(size)}). ` +
      "You can close this tab once the file appears in your downloads."
  );
}

run().catch((e) => fail(String(e)));
