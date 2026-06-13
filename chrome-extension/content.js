// Runs inside Panopto pages — both standalone viewer tabs and the iframes
// Canvas embeds. Asks Panopto's own DeliveryInfo API (the same call the video
// player makes) for the stream URLs and caption availability, then reports
// everything to the background service worker.

const DELIVERY_ID_RE =
  /[?&]id=([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/i;

function getDeliveryId() {
  const m = location.href.match(DELIVERY_ID_RE);
  return m ? m[1] : null;
}

// Walk the DeliveryInfo JSON and collect every video URL found anywhere in
// it. Tenants differ in where they put streams (Streams, PodcastStreams,
// StreamHttpUrl, ...), so a recursive sweep is more robust than fixed paths.
function collectUrls(node, out) {
  if (typeof node === "string") {
    // Only absolute URLs — DeliveryInfo also contains bare filenames like
    // "rec0_p0_0_....mp4" that are not downloadable.
    if (!/^https?:\/\//i.test(node)) return;
    const path = node.split("?")[0].toLowerCase();
    if (path.endsWith(".mp4")) out.mp4.add(node);
    else if (path.endsWith(".m3u8")) out.hls.add(node);
  } else if (Array.isArray(node)) {
    for (const v of node) collectUrls(v, out);
  } else if (node && typeof node === "object") {
    for (const v of Object.values(node)) collectUrls(v, out);
  }
}

async function fetchDeliveryInfo(deliveryId) {
  const body = new URLSearchParams({
    deliveryId,
    invocationId: "",
    isLiveNotes: "false",
    refreshAuthCookie: "true",
    isActiveBroadcast: "false",
    isEditing: "false",
    isKollectiveAgentInstalled: "false",
    isEmbed: "false",
    responseType: "json",
  });
  const res = await fetch(
    `${location.origin}/Panopto/Pages/Viewer/DeliveryInfo.aspx`,
    {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      credentials: "include",
      body,
    }
  );
  if (!res.ok) throw new Error(`DeliveryInfo returned HTTP ${res.status}`);
  return res.json();
}

async function checkCaptions(deliveryId) {
  const url = `${location.origin}/Panopto/Pages/Transcription/GenerateSRT.ashx?id=${deliveryId}&language=0`;
  try {
    const res = await fetch(url, { credentials: "include" });
    const text = await res.text();
    return { url, available: res.ok && text.trim().length > 0 };
  } catch {
    return { url, available: false };
  }
}

async function main() {
  const deliveryId = getDeliveryId();
  if (!deliveryId) return;

  const info = {
    deliveryId,
    origin: location.origin,
    pageUrl: location.href,
    title: document.title || "lecture",
    streams: [],
    mp4: [],
    hls: [],
    captions: null,
    error: null,
  };

  try {
    const data = await fetchDeliveryInfo(deliveryId);

    // Structured per-stream info (multi-camera lectures: blackboard cam,
    // screen capture, etc.). Mirrors the order of the player's own
    // stream-switcher buttons.
    const seen = new Set();
    const rawStreams = data?.Delivery?.Streams;
    if (Array.isArray(rawStreams)) {
      for (const s of rawStreams) {
        const urls = { mp4: new Set(), hls: new Set() };
        collectUrls(s, urls);
        for (const u of [...urls.mp4, ...urls.hls]) seen.add(u);
        info.streams.push({
          name: s.Name || s.Tag || null,
          tag: s.Tag || null,
          mp4: [...urls.mp4],
          hls: [...urls.hls],
        });
      }
    }

    // Anything not attached to a named stream (e.g. combined "podcast"
    // renders) goes in the flat lists.
    const urls = { mp4: new Set(), hls: new Set() };
    collectUrls(data, urls);
    info.mp4 = [...urls.mp4].filter((u) => !seen.has(u));
    info.hls = [...urls.hls].filter((u) => !seen.has(u));

    const sessionName =
      data?.Delivery?.SessionName || data?.Delivery?.PublicID || null;
    if (sessionName) info.title = sessionName;
  } catch (e) {
    info.error = String(e);
  }

  info.captions = await checkCaptions(deliveryId);
  chrome.runtime.sendMessage({ type: "lectureFound", info });
}

main();
