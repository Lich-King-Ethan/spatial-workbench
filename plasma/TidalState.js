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

function artworkUrl(value) {
    // Catalogue artwork has one public CDN origin and identifier-only path.
    return typeof value === "string"
        && /^https:\/\/resources\.tidal\.com\/images\/[0-9a-fA-F]{8}\/[0-9a-fA-F]{4}\/[0-9a-fA-F]{4}\/[0-9a-fA-F]{4}\/[0-9a-fA-F]{12}\/320x320\.jpg$/.test(value) ? value : "";
}
