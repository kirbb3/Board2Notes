// Service worker. Three jobs:
//   1. Remember lecture info reported by content scripts, keyed per tab.
//   2. Sniff .mp4/.m3u8 URLs from Panopto network traffic as a fallback for
//      tenants where the DeliveryInfo API route fails.
//   3. Perform downloads on behalf of the popup (chrome.downloads uses the
//      browser cookie jar, so authenticated URLs work).
//
// State lives in chrome.storage.session, not module globals, because MV3
// service workers are killed and restarted between events.

function lecturesKey(tabId) {
  return `lectures:${tabId}`;
}
function sniffedKey(tabId) {
  return `sniffed:${tabId}`;
}

async function addLecture(tabId, info) {
  const key = lecturesKey(tabId);
  const cur = (await chrome.storage.session.get(key))[key] || {};
  cur[info.deliveryId] = info;
  await chrome.storage.session.set({ [key]: cur });
  chrome.action.setBadgeText({ tabId, text: String(Object.keys(cur).length) });
  chrome.action.setBadgeBackgroundColor({ tabId, color: "#2e7d32" });
}

async function addSniffed(tabId, url) {
  const key = sniffedKey(tabId);
  const cur = (await chrome.storage.session.get(key))[key] || [];
  if (cur.includes(url)) return;
  cur.push(url);
  // Keep the list bounded; HLS players request many playlist variants.
  await chrome.storage.session.set({ [key]: cur.slice(-30) });
}

async function clearTab(tabId) {
  await chrome.storage.session.remove([lecturesKey(tabId), sniffedKey(tabId)]);
  try {
    chrome.action.setBadgeText({ tabId, text: "" });
  } catch {
    // Tab may already be gone.
  }
}

chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    if (details.tabId < 0) return;
    const path = details.url.split("?")[0].toLowerCase();
    if (path.endsWith(".mp4") || path.endsWith(".m3u8")) {
      addSniffed(details.tabId, details.url);
    }
  },
  { urls: ["*://*.panopto.com/*", "*://*.panopto.eu/*"] }
);

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  // Full navigation to a new URL invalidates anything we knew about the tab.
  if (changeInfo.url) clearTab(tabId);
});

chrome.tabs.onRemoved.addListener((tabId) => clearTab(tabId));

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "lectureFound" && sender.tab) {
    addLecture(sender.tab.id, msg.info);
  } else if (msg.type === "getState") {
    (async () => {
      const data = await chrome.storage.session.get([
        lecturesKey(msg.tabId),
        sniffedKey(msg.tabId),
      ]);
      sendResponse({
        lectures: data[lecturesKey(msg.tabId)] || {},
        sniffed: data[sniffedKey(msg.tabId)] || [],
      });
    })();
    return true; // async sendResponse
  } else if (msg.type === "download") {
    chrome.downloads.download({
      url: msg.url,
      filename: msg.filename,
      saveAs: false,
    });
  }
});
