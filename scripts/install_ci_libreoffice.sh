#!/bin/bash
# CI only: pin the converter and verify the official archive before mounting it.
set -euo pipefail
[[ "${GITHUB_ACTIONS:-}" == "true" && "${RUNNER_OS:-}" == "macOS" ]] || {
  echo 'This installer is for disposable GitHub macOS runners only.' >&2
  exit 1
}
case "$(uname -m)" in
  arm64)
    arch=aarch64
    file_arch=aarch64
    sha=8858d8058da4f862f47559486814e65efc27294da67c5e4bb56b006b1ee59f89
    ;;
  x86_64)
    arch=x86_64
    file_arch=x86-64
    sha=2dcbce4894e01bc1ecd594658e2cbda70ff7bfcd0b310f35d38887797172d09e
    ;;
  *) echo 'Unsupported CI architecture' >&2; exit 1 ;;
esac
# Hashes published in the official .dmg.mirrorlist and Homebrew cask metadata.
# 26.8.0.3 is the full build number of the previously tested 26.8.0 release.
version=26.8.0.3
filename="LibreOffice_${version}_MacOS_${file_arch}.dmg"
url="https://downloadarchive.documentfoundation.org/libreoffice/old/${version}/mac/${arch}/${filename}"
temporary=$(mktemp -d "${RUNNER_TEMP}/libreoffice.XXXXXX")
dmg="${temporary}/LibreOffice.dmg"
volume="${temporary}/volume"
curl --fail --location --retry 3 --retry-all-errors --connect-timeout 20 --max-time 180 \
  --proto '=https' --proto-redir '=https' --output "$dmg" "$url"
printf '%s  %s\n' "$sha" "$dmg" | shasum -a 256 -c -
[[ ! -e /Applications/LibreOffice.app ]] || { echo 'LibreOffice already exists on the runner' >&2; exit 1; }
mkdir "$volume"
hdiutil attach -quiet -nobrowse -readonly -mountpoint "$volume" "$dmg"
trap 'hdiutil detach -quiet "$volume"' EXIT
ditto "$volume/LibreOffice.app" /Applications/LibreOffice.app
/Applications/LibreOffice.app/Contents/MacOS/soffice --version
