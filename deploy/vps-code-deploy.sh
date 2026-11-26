#!/usr/bin/env bash
set -euo pipefail

expected=''
if (( $# != 0 )); then
    if [[ $# != 1 || ! $1 =~ ^deploy\ ([0-9a-f]{40})$ ]]; then
        exit 64
    fi
    expected=${BASH_REMATCH[1]}
fi

cd /opt/memedatabase
exec 9>/run/lock/memedatabase-deploy.lock
flock -w 1200 9

if [[ $(git branch --show-current) != main || -n $(git status --porcelain --untracked-files=no) ]]; then
    echo 'Deployment checkout must be clean and on main.' >&2
    exit 1
fi

git fetch --no-tags origin main
revision=$(git rev-parse FETCH_HEAD)
if [[ -n $expected && $expected != "$revision" ]]; then
    echo "The workflow revision is no longer the tip of main: $expected" >&2
    exit 1
fi
git merge --ff-only "$revision"
if [[ $(git rev-parse HEAD) != "$revision" ]]; then
    echo 'Local main differs from fetched main.' >&2
    exit 1
fi

state=.git/memedatabase-deployed
if [[ -f $state && $(<"$state") == "$revision" ]]; then
    echo "Already deployed: $revision"
    exit 0
fi

docker compose up -d --build --wait --wait-timeout 120
printf '%s\n' "$revision" > "$state"
echo "Deployed: $revision"
