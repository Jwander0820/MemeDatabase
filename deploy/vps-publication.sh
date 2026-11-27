#!/usr/bin/env bash
set -euo pipefail
[[ $# == 1 ]] || exit 64

# SSH 傳入的是單一字串，只辨識下列操作，不交給 shell 展開。
command=$1
version_pattern='[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}'
version=''
if [[ $command =~ ^(check-version|activate)\ ($version_pattern)$ ]]; then
    action=${BASH_REMATCH[1]}
    version=${BASH_REMATCH[2]}
else
    case "$command" in
        media-inventory|install|install-and-activate) action=$command ;;
        *) exit 64 ;;
    esac
fi

root=/srv/memedatabase/releases
umask 077
exec 8>/run/lock/memedatabase-deploy.lock
flock -s -w 120 8
exec 9>/run/lock/memedatabase-publication.lock
flock -n 9
image=$(/usr/bin/docker inspect --type container --format '{{.Image}}' memedatabase-public-api-1)
[[ $image =~ ^sha256:[0-9a-f]{64}$ ]] || exit 69
container=(/usr/bin/docker run --rm --network none --read-only --tmpfs /tmp
    --cap-drop ALL --security-opt no-new-privileges --user 10001:10001
    --pids-limit 64 --memory 256m --cpus 1)
runtime=(python -m src.anime_memes.runtime)

# 啟用成功後清理舊版本：保留最新三版與啟用版，服務不需停止。
# 輸出導到 stderr，stdout 只留給呼叫端解析的 JSON；清理失敗不影響已完成的啟用。
prune_old() {
    "${container[@]}" -v "$root:/releases" "$image" "${runtime[@]}" prune --root /releases --keep-recent 3 >&2 \
        || echo "warning: prune failed; release is active, old versions kept" >&2
}

case "$action" in
    check-version)
        "${container[@]}" -v "$root:/releases:ro" "$image" python -c '
import json, sys
from pathlib import Path
from src.anime_memes.releases import validate
root = Path("/releases")
version = sys.argv[1]
path = root / "versions" / version
installed = path.exists() or path.is_symlink()
print(json.dumps({"installed": installed, "manifest": validate(root, version) if installed else None}))
' "$version"
        ;;
    media-inventory)
        "${container[@]}" -v "$root:/releases:ro" "$image" "${runtime[@]}" media-inventory --root /releases
        ;;
    activate)
        "${container[@]}" -v "$root:/releases" "$image" "${runtime[@]}" activate --root /releases "$version"
        prune_old
        ;;
    install|install-and-activate)
        bundle=$(mktemp /var/tmp/memedatabase-publication.XXXXXXXX)
        trap 'rm -f -- "$bundle"' EXIT
        limit=$((4 * 1024 * 1024 * 1024))
        timeout 20m head -c "$((limit + 1))" > "$bundle"
        size=$(stat -c %s "$bundle")
        (( size > 0 && size <= limit )) || exit 65
        chmod 0644 "$bundle"
        flags=()
        if [[ $action == install-and-activate ]]; then flags+=(--activate); fi
        "${container[@]}" -v "$root:/releases" -v "$bundle:/bundle.tar.gz:ro" "$image" \
            "${runtime[@]}" install-bundle --root /releases "${flags[@]}" /bundle.tar.gz
        if [[ $action == install-and-activate ]]; then prune_old; fi
        ;;
esac
