.pragma library
// SPDX-License-Identifier: GPL-3.0-or-later

// Presentation derived solely from the decoder's observed state. A file name,
// TIDAL badge, configured decoder, or capability flag is not Atmos evidence.
function sourceKind(audio) {
    if (!audio || !audio.running || !audio.loaded) return "none";
    const objects = Number(audio.object_count);
    if (audio.renderer_ready && audio.source_mode === "spatial"
            && Number.isFinite(objects) && objects > 0) {
        if (audio.content_format === "Dolby Atmos") return "atmos";
        if (audio.content_format === "DTS:X") return "dtsx";
        return "objects";
    }
    if (audio.renderer_ready) return "binaural";
    return "stereo";
}

function seconds(value) {
    return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : 0;
}

function durationText(value) {
    const whole = Math.floor(seconds(value));
    const hours = Math.floor(whole / 3600);
    const minutes = Math.floor((whole % 3600) / 60);
    const remaining = String(whole % 60).padStart(2, "0");
    return hours ? hours + ":" + String(minutes).padStart(2, "0") + ":" + remaining
                 : minutes + ":" + remaining;
}

function progress(audio) {
    const duration = seconds(audio && audio.duration);
    return duration > 0 ? Math.min(1, seconds(audio && audio.position) / duration) : 0;
}
