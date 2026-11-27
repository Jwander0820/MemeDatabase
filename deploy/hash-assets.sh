#!/bin/sh
# 為公開站靜態檔產生帶內容雜湊的副本（name.<12 碼 sha256>.ext），並改寫 index.html 的引用。
# 原檔名保留：私人管理頁與本機 Flask 仍以原檔名引用（no-cache）；帶雜湊的檔名由 Nginx 長期快取（immutable）。
# 用法：sh deploy/hash-assets.sh <gallery 目錄>
set -eu
dir=${1:?需要 gallery 目錄}
index="$dir/index.html"
for path in "$dir"/*.js "$dir"/*.css "$dir"/*.svg; do
    [ -f "$path" ] || continue
    name=${path##*/}
    case "$name" in
        *.[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f].*) continue ;;
    esac
    hash=$(sha256sum "$path" | cut -c1-12)
    hashed="${name%.*}.$hash.${name##*.}"
    cp "$path" "$dir/$hashed"
    # 以結尾引號比對完整檔名，避免 gallery.js 誤中 gallery-init.js。
    sed -i "s#\"/assets/$name\"#\"/assets/$hashed\"#g" "$index"
done
if grep -Eq '"/assets/[A-Za-z0-9_-]+\.(js|css|svg)"' "$index"; then
    echo "index.html 仍有未加雜湊的 /assets/ 引用" >&2
    exit 1
fi
