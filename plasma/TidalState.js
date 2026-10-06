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

function positive(value) {
    return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : 0;
}
function decodedFormat(audio) {
    // Only observed decoder properties describe the playing stream. Catalog
    // badges and the requested tier do not establish its codec or bitrate.
    if (!audio || !audio.running || !audio.loaded) return "";
    const details = [];
    if (typeof audio.codec === "string" && audio.codec.length <= 32 && audio.codec.length)
        details.push(audio.codec.toUpperCase());
    const parameters = object(audio.audio_parameters);
    const sampleRate = positive(audio.sample_rate) || positive(parameters.samplerate);
    const bitrate = positive(audio.bitrate);
    const channels = positive(audio.channels) || positive(parameters["channel-count"]);
    if (sampleRate) details.push(String(Math.round(sampleRate / 100) / 10) + " kHz");
    if (channels) details.push(String(channels) + " ch");
    if (bitrate) details.push(String(Math.round(bitrate / 1000)) + " kbps");
    return details.join(" · ");
}

function nextOffset(page) {
    const offset = Number.isSafeInteger(page.offset) && page.offset >= 0 ? page.offset : 0;
    // Playlist folders can contain unsupported server rows. Continue at the
    // server's raw position instead of skipping a short filtered page.
    if (Number.isSafeInteger(page.next_offset) && page.next_offset > offset) return page.next_offset;
    const limit = Number.isSafeInteger(page.limit) && page.limit > 0 ? page.limit : 20;
    return offset + limit;
}
