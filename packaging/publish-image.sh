#!/usr/bin/env bash
# Build the vicolocal container for linux/amd64 and linux/arm64 and push it to GitHub
# Container Registry as ghcr.io/averagenative/vicolocal:<version>, :<major.minor>, and :latest.
#
# There is no CI on purpose: run this from a clean, pushed checkout after a release. Needs
# podman and `gh` logged in with the write:packages scope
# (`gh auth refresh -s write:packages` adds it).
#
# arm64 is built one of two ways:
#   ARM64_HOST=<ssh host> packaging/publish-image.sh
#       natively on a 64-bit ARM machine with Docker (a Raspberry Pi works). The host clones
#       this exact commit from GitHub, builds, and streams the image back over ssh; the
#       registry token never leaves this machine.
#   packaging/publish-image.sh
#       under qemu on this machine (needs qemu-user-static and no conflicting binfmt handler;
#       Waydroid's libhoudini registers one that breaks this).
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE="${IMAGE:-ghcr.io/averagenative/vicolocal}"
REPO_URL="${REPO_URL:-https://github.com/averagenative/vicolocal.git}"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' vico_server.py)"
MANIFEST="vicolocal-publish"
[ -n "$VERSION" ] || { echo "no __version__ in vico_server.py" >&2; exit 1; }

if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    echo "Working tree has uncommitted changes; publish only from a clean checkout." >&2
    exit 1
fi
REV="$(git rev-parse HEAD)"

gh auth token | podman login ghcr.io -u "$(gh api user -q .login)" --password-stdin
podman manifest rm "$MANIFEST" >/dev/null 2>&1 || true
podman manifest create "$MANIFEST"

# --format docker keeps a HEALTHCHECK if one is added later (the OCI format drops it).
if [ -n "${ARM64_HOST:-}" ]; then
    git fetch -q origin && git branch -r --contains "$REV" | grep -q "origin/" || {
        echo "Push $REV first: $ARM64_HOST builds it from $REPO_URL." >&2; exit 1; }
    podman build --format docker --platform linux/amd64 -t localhost/vicolocal:amd64 .
    echo "== building linux/arm64 on $ARM64_HOST"
    ssh "$ARM64_HOST" "set -e; d=\$(mktemp -d); git clone -q $REPO_URL \$d; cd \$d; git checkout -q $REV
        docker build -q -t localhost/vicolocal:arm64 . >&2
        docker save localhost/vicolocal:arm64; cd /; rm -rf \$d; docker rmi localhost/vicolocal:arm64 >/dev/null 2>&1" \
        | podman load
    podman manifest add "$MANIFEST" containers-storage:localhost/vicolocal:amd64
    podman manifest add "$MANIFEST" containers-storage:localhost/vicolocal:arm64
else
    podman build --format docker --platform linux/amd64,linux/arm64 --manifest "$MANIFEST" .
fi

for tag in "$VERSION" "${VERSION%.*}" latest; do
    podman manifest push --all --format v2s2 "$MANIFEST" "docker://$IMAGE:$tag"
done
echo "Pushed $IMAGE:{$VERSION,${VERSION%.*},latest} for linux/amd64 and linux/arm64"
