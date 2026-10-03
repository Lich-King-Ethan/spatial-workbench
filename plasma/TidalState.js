.pragma library
// SPDX-License-Identifier: GPL-3.0-or-later

function object(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value) ? value : ({});
}
function decode(text) { return object(JSON.parse(String(text))); }
function items(page, category) {
    const value = page.items || page[category];
    return Array.isArray(value) ? value : [];
}
function loginUrl(value) {
    // Device authorization stays on TIDAL's own site. Never open catalog text.
    return typeof value === "string"
        && /^https:\/\/(link\.tidal\.com|login\.tidal\.com|tidal\.com)(\/|\?|$)/.test(value)
        && !/[\s\\]/.test(value) ? value : "";
}
function playable(item) {
    return !!item && ["track", "album", "playlist"].includes(item.kind)
        && typeof item.reference === "string" && item.reference.startsWith("tidal:");
}
