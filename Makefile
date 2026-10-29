# vicolocal tasks. There is no CI: run `make check` before you push.
PY ?= python3

.PHONY: check test run docker docker-build publish-image

check: test
	$(PY) -m py_compile vico_server.py vico_signaling.py vico_sdp.py vico_api.py vico_archive.py vico_pull.py
	bash -n packaging/publish-image.sh
	@echo "check: ok"

# Unit tests (needs requirements.txt installed; nothing talks to the cloud).
test:
	$(PY) -m unittest discover -s tests -t .

# Run the dashboard locally (needs requirements.txt installed and a .vico.env or VICO_* env).
run:
	$(PY) vico_server.py

# Dashboard in a container (needs .env; see .env.example). Pulls the published image.
docker:
	docker compose up -d

# Build the container image locally under the published name.
docker-build:
	docker build -t ghcr.io/averagenative/vicolocal:latest .

# Build for amd64 + arm64 and push to ghcr.io (run from a clean, pushed checkout).
# ARM64_HOST=<ssh host> builds arm64 natively there instead of under qemu.
publish-image:
	packaging/publish-image.sh
