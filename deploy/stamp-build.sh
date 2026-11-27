#!/bin/sh
# 把部署版號（git commit 短碼與建置時間）寫進公開站 index.html：
# <meta name="build-version"> 與頁尾的 <span class="build-version">。index.html 由 Nginx 以 no-cache 提供，
# 重新整理即可看到目前上線的版本。未提供版號（本機建置）時保持空白，頁尾不顯示。
# 用法：sh deploy/stamp-build.sh <index.html> <git commit> <建置時間 UTC，如 2026-11-27T09:30:00Z>
set -eu
index=${1:?需要 index.html 路徑}
revision=${2:-}
built=${3:-}
[ -n "$revision" ] || exit 0
printf '%s' "$revision" | grep -Eqx '[0-9a-f]{7,40}' || { echo "版號格式錯誤：$revision" >&2; exit 1; }
printf '%s' "$built" | grep -Eqx '[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z' || { echo "建置時間格式錯誤：$built" >&2; exit 1; }
short=$(printf '%s' "$revision" | cut -c1-7)
sed -i \
    -e "s#<meta name=\"build-version\" content=\"\">#<meta name=\"build-version\" content=\"$revision $built\">#" \
    -e "s#<span class=\"build-version\"></span>#<span class=\"build-version\" title=\"建置於 $built\">$short</span>#" \
    "$index"
grep -q "content=\"$revision $built\"" "$index" && grep -q ">$short</span>" "$index" || { echo "index.html 找不到版號欄位" >&2; exit 1; }
