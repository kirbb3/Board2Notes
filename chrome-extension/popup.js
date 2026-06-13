// Popup UI: shows what was found in the current tab and triggers downloads.
// Downloads run directly from the popup so every failure surfaces as a
// status message under the button instead of dying silently.

function safeName(s) {
  return (
    s
      .replace(/[\\/:*?"<>|]+/g, "_")
      // The downloads API rejects some non-ASCII characters; keep it plain.
      .replace(/[^\x20-\x7E]/g, "_")
      .replace(/_{2,}/g, "_")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 100)
      .replace(/[. _-]+$/, "") || "lecture"
  );
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "disabled") node.disabled = v;
    else node.setAttribute(k, v);
  }
  node.append(...children);
  return node;
}

// A download button with its own status line. `kind` is "file" for direct
// downloads or "hls" for streams that go through the downloader page.
function downloadButton(label, { url, filename, kind }) {
  const status = el("div", { class: "status" });
  const btn = el("button", {}, label);

  btn.addEventListener("click", async () => {
    status.className = "status";
    status.textContent = "Starting…";

    if (kind === "hls") {
      try {
        const u = new URL(chrome.runtime.getURL("downloader.html"));
        u.searchParams.set("src", url);
        u.searchParams.set("title", filename.replace(/\.[a-z0-9]+$/i, ""));
        await chrome.tabs.create({ url: u.toString() });
      } catch (e) {
        status.className = "status error";
        status.textContent = `Error: ${e.message || e}`;
      }
      return;
    }

    try {
      await chrome.downloads.download({ url, filename, saveAs: false });
      status.textContent = "✓ Download started";
    } catch (e1) {
      // Filename rejected? Let Chrome pick one.
      try {
        await chrome.downloads.download({ url, saveAs: false });
        status.textContent = "✓ Download started (Chrome chose the filename)";
      } catch (e2) {
        status.className = "status error";
        status.textContent = `Failed: ${
          e2.message || e2
        } — opening in a tab instead`;
        chrome.tabs.create({ url });
      }
    }
  });

  const wrap = el("div", {});
  wrap.append(btn, status);
  return wrap;
}

// Pick the single best video URL for the lecture. The flat mp4 list holds
// the combined "podcast" render (the one confirmed working); per-camera
// streams and HLS are fallbacks only.
function pickVideo(info) {
  if (info.mp4.length > 0) return { url: info.mp4[0], kind: "file" };
  for (const s of info.streams || []) {
    if (s.mp4.length > 0) return { url: s.mp4[0], kind: "file" };
  }
  if (info.hls.length > 0) return { url: info.hls[0], kind: "hls" };
  for (const s of info.streams || []) {
    if (s.hls.length > 0) return { url: s.hls[0], kind: "hls" };
  }
  return null;
}

function renderLecture(info) {
  const box = el("div", { class: "lecture" });
  box.append(el("div", { class: "title" }, info.title));
  const name = safeName(info.title);

  const video = pickVideo(info);
  if (video) {
    box.append(
      downloadButton(
        video.kind === "hls"
          ? "⬇ Download video (HLS stream)"
          : "⬇ Download video (.mp4)",
        {
          url: video.url,
          filename: `${name}.${video.kind === "hls" ? "ts" : "mp4"}`,
          kind: video.kind,
        }
      )
    );
  } else {
    box.append(
      el("div", { class: "empty" }, "No video URL found via the API.")
    );
  }

  // Individual camera streams (full-resolution blackboard camera lives
  // here; the combined render shrinks it to an inset). Each button reports
  // its own error below it if the download fails.
  (info.streams || []).forEach((s, i) => {
    const label = s.name || s.tag || `stream ${i + 1}`;
    const suffix = ` - camera${i + 1}`;
    if (s.mp4.length > 0) {
      box.append(
        downloadButton(`⬇ Camera ${i + 1} only: ${label} (.mp4)`, {
          url: s.mp4[0],
          filename: `${name}${suffix}.mp4`,
          kind: "file",
        })
      );
    } else if (s.hls.length > 0) {
      box.append(
        downloadButton(`⬇ Camera ${i + 1} only: ${label} (HLS)`, {
          url: s.hls[0],
          filename: `${name}${suffix}.ts`,
          kind: "hls",
        })
      );
    }
  });

  const cap = info.captions;
  if (cap && cap.available) {
    box.append(
      downloadButton("⬇ Download transcript (.srt)", {
        url: cap.url,
        filename: `${name}.srt`,
        kind: "file",
      })
    );
  } else {
    box.append(
      el(
        "button",
        { disabled: true },
        "Transcript: not available for this lecture"
      )
    );
  }

  if (info.error) {
    box.append(el("div", { class: "error" }, `API error: ${info.error}`));
  }
  return box;
}

function renderSniffed(urls) {
  const frag = document.createDocumentFragment();
  frag.append(el("h2", {}, "Streams seen on the network (fallback)"));
  for (const url of urls) {
    const isHls = url.split("?")[0].toLowerCase().endsWith(".m3u8");
    const btn = downloadButton(isHls ? "⬇ HLS playlist" : "⬇ MP4", {
      url,
      filename: isHls ? "lecture.ts" : "lecture.mp4",
      kind: isHls ? "hls" : "file",
    });
    btn.append(el("div", { class: "url" }, url));
    frag.append(btn);
  }
  return frag;
}

async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const content = document.getElementById("content");
  if (!tab) {
    content.textContent = "No active tab.";
    return;
  }

  const state = await chrome.runtime.sendMessage({
    type: "getState",
    tabId: tab.id,
  });
  content.replaceChildren();

  const lectures = Object.values(state.lectures);
  if (lectures.length > 0) {
    for (const info of lectures) content.append(renderLecture(info));
  } else {
    content.append(
      el(
        "div",
        { class: "empty" },
        "No Panopto lecture detected in this tab. Open the lecture page " +
          "(or click play on the embedded video), then reopen this popup."
      )
    );
  }

  if (lectures.length === 0 && state.sniffed.length > 0) {
    content.append(renderSniffed(state.sniffed));
  }
}

init();
