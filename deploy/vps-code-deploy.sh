#!/usr/bin/env bash
set -euo pipefail

deployment='^deploy ([0-9a-f]{40}) (sha256:[0-9a-f]{64}) (sha256:[0-9a-f]{64})$'
[[ $# == 1 && $1 =~ $deployment ]] || exit 64
revision=${BASH_REMATCH[1]}
api="ghcr.io/jwander0820/memedatabase-public-api@${BASH_REMATCH[2]}"
nginx="ghcr.io/jwander0820/memedatabase-public-nginx@${BASH_REMATCH[3]}"

cd /opt/memedatabase
exec 9>/run/lock/memedatabase-deploy.lock
flock -w 1200 9
if systemctl is-active --quiet memedatabase-deploy.timer; then
    echo 'Stop the source deployment timer before switching to GHCR.' >&2
    exit 1
fi
main=$(git ls-remote --exit-code https://github.com/Jwander0820/MemeDatabase.git refs/heads/main)
if [[ ${main%%$'\t'*} != "$revision" ]]; then
    echo 'The workflow revision is no longer the tip of main.' >&2
    exit 1
fi
umask 077
pending=$(mktemp /opt/memedatabase/.ghcr-pending.XXXXXXXX)
trap 'rm -f -- "$pending"' EXIT
state=/opt/memedatabase/.ghcr-deployed.env
printf 'MEMES_PUBLIC_API_IMAGE=%s\nMEMES_PUBLIC_NGINX_IMAGE=%s\nMEMES_DEPLOYED_REVISION=%s\n' "$api" "$nginx" "$revision" > "$pending"
compose=(/usr/bin/docker compose --project-directory /opt/memedatabase
    --env-file /opt/memedatabase/.env --profile public
    -f /etc/memedatabase/compose.yaml)
images=(-f /etc/memedatabase/compose.ghcr.yaml)
services=(public-api public-nginx)

# 兩份映像都拉取並確認版本後才更新容器。
"${compose[@]}" "${images[@]}" --env-file "$pending" pull "${services[@]}"
for image in "$api" "$nginx"; do
    [[ $(/usr/bin/docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image") == "$revision" ]]
done
if ! "${compose[@]}" "${images[@]}" --env-file "$pending" up -d --pull never --no-build --wait --wait-timeout 120 "${services[@]}"; then
    echo 'New containers did not become healthy; restoring the previous deployment.' >&2
    if [[ -f $state ]]; then
        "${compose[@]}" "${images[@]}" --env-file "$state" up -d --pull never --no-build --wait "${services[@]}"
    else
        /usr/bin/docker compose --profile public up -d --pull never --no-build --wait "${services[@]}"
    fi
    exit 1
fi
mv -f -- "$pending" "$state"
echo "Deployed: $revision"
