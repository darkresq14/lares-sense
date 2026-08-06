#!/usr/bin/env bash
#
# fetch_vendor.sh — populate the gitignored docs_and_tools/ with TI + vendor files.
#
#   - Idempotent: skips any item already present (non-empty file / existing dir).
#   - Direct items are downloaded via curl.
#   - Gated items (TI login required) are NOT fetched automatically; the script
#     prints the human-download page and the exact target filename to drop in.
#
# Vendor is intentionally not version-controlled (~1.8 GB).
# Run only on dev machines. Never invoked by anything automatically.
#
set -euo pipefail

# Resolve repo root relative to this script (works from any CWD).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDOR_DIR="${VENDOR_DIR:-$SCRIPT_DIR/docs_and_tools}"

mkdir -p "$VENDOR_DIR"

# --- helpers ---------------------------------------------------------------

# fetch_file <relative_path> <url>
# Downloads only if file missing/empty. URL-encodes nothing — filenames kept literal.
fetch_file() {
    local rel="$1" url="$2"
    local dest="$VENDOR_DIR/$rel"
    if [[ -s "$dest" ]]; then
        printf '  [skip] %s (exists)\n' "$rel"
        return 0
    fi
    printf '  [get ] %s\n' "$rel"
    local tmp="$dest.part"
    curl -fL --retry 3 --connect-timeout 30 -o "$tmp" "$url"
    mv "$tmp" "$dest"
}

# manual <relative_path> <download_page_url> <human_note>
# Gated / login-only items. Verifies presence, prints page if missing.
manual() {
    local rel="$1" page="$2" note="$3"
    local dest="$VENDOR_DIR/$rel"
    if [[ -e "$dest" ]]; then
        printf '  [skip] %s (exists)\n' "$rel"
        return 0
    fi
    printf '  [MANUAL] %s\n          %s\n          -> place at %s\n' "$rel" "$page" "$dest"
    printf '          note: %s\n' "$note"
}

# --- cleanup ---------------------------------------------------------------

# Windows redirect artifact (0-byte 'nul') that slips into the folder.
if [[ -f "$VENDOR_DIR/nul" && ! -s "$VENDOR_DIR/nul" ]]; then
    rm -f "$VENDOR_DIR/nul"
    printf '  [clean] removed junk file: nul\n'
fi

# --- direct downloads (no login) -------------------------------------------

echo "Direct downloads:"

fetch_file "iwr6843_data_sheet.pdf" \
    "https://www.ti.com/lit/ds/symlink/iwr6843.pdf"

fetch_file "an197-serial-communications-guide-cp210x.pdf" \
    "https://www.silabs.com/documents/public/application-notes/an197-serial-communications-guide-cp210x.pdf"

fetch_file "Linux_CP210x_VCP_2.6.x_Release_Notes.txt" \
    "https://www.silabs.com/documents/public/release-notes/Linux_CP210x_VCP_2.6.x_Release_Notes.txt"

fetch_file "uRAD - AWR IWR PCB adapter for RaspberryPi - EN.pdf" \
    "https://urad.es/wp-content/descargables/uRAD%20-%20AWR%20IWR%20PCB%20adapter%20for%20RaspberryPi%20-%20EN.pdf"

fetch_file "uRAD - User Manual - Industrial - EN.pdf" \
    "https://urad.es/wp-content/descargables/uRAD%20-%20User%20Manual%20-%20Industrial%20-%20EN.pdf"

# --- manual items (TI login required) --------------------------------------

echo
echo "Manual items (TI account / login required, not auto-fetched):"

manual "radar_toolbox_4_00_00_05/" \
    "https://www.ti.com/tool/download/RADAR-TOOLBOX" \
    "Radar Toolbox 4.00.00.05 (~1.5 GB). Extract so the folder is docs_and_tools/radar_toolbox_4_00_00_05/."

manual "uniflash_sl.9.5.0.5651.run" \
    "https://www.ti.com/tool/download/UNIFLASH/9.5.0" \
    "UniFlash 9.5.0.5651 Linux installer."

echo
echo "Done. Vendor dir: $VENDOR_DIR"
