PLATFORM ?= .toolchain/platform
BRAND ?= .toolchain/brand
STATUS_ROOT ?= $(CURDIR)
GO ?= go

# publish-check compares against the published commit. The publish workflow
# passes PUBLISH_DECISION_FILE so the script, not the YAML, decides whether a
# heartbeat is worth committing.
PUBLISH_BASE ?= HEAD
PUBLISH_DECISION_FILE ?=

PLATFORM_DIR := $(abspath $(PLATFORM))
BRAND_DIR := $(abspath $(BRAND))

# The single definition of the local gate. CI runs `make validate`; neither
# workflow keeps a second list of checks. scripts/ci_contract_test.py proves
# that the workflow still delegates here and that the pinned toolchain refs
# stay full commit SHAs.
GATE_TARGETS := status-check test brand-check render-check assets-check statuscheck lint

.PHONY: $(GATE_TARGETS) brand prune publish-check toolchain-check validate

# The platform owns the brand assets; this repository owns the rendered
# pages and their publish-time snapshot. `-brand-only` stops genstatus from
# overwriting index.html with the template before the renderer can read the
# previous snapshot binding, which is what keeps an unchanged document
# byte-identical across rebuilds.
brand: toolchain-check
	cd "$(PLATFORM_DIR)" && $(GO) run ./cmd/genstatus -root "$(STATUS_ROOT)" -brand "$(BRAND_DIR)" -brand-only
	$(MAKE) --no-print-directory render
	$(MAKE) --no-print-directory prune

toolchain-check:
	bash scripts/require_checkout.sh platform "$(PLATFORM_DIR)" .toolchain/platform
	bash scripts/require_checkout.sh brand "$(BRAND_DIR)" .toolchain/brand

status-check:
	python3 scripts/validate_status.py --root "$(STATUS_ROOT)"

test:
	cd "$(STATUS_ROOT)" && python3 -m unittest discover -s scripts -p '*_test.py'

brand-check: toolchain-check
	python3 scripts/check_generated.py --root "$(STATUS_ROOT)" --platform "$(PLATFORM_DIR)" --brand "$(BRAND_DIR)"

render:
	python3 scripts/build_site.py --root "$(STATUS_ROOT)" --write

render-check:
	python3 scripts/build_site.py --root "$(STATUS_ROOT)" --check

prune:
	python3 scripts/prune_assets.py --root "$(STATUS_ROOT)" --write

assets-check:
	python3 scripts/prune_assets.py --root "$(STATUS_ROOT)" --check

statuscheck: toolchain-check
	cd "$(PLATFORM_DIR)" && $(GO) run ./cmd/statuscheck -file "$(STATUS_ROOT)/status.json"

lint: toolchain-check
	cd "$(PLATFORM_DIR)" && $(GO) run ./cmd/brandlint -root "$(PLATFORM_DIR)" -brand "$(BRAND_DIR)" -status "$(STATUS_ROOT)"

# Answer "would this publish change anything?" after `make brand`. The script
# owns the exact codes: 0 nothing to publish, 10 only the snapshot instant
# changed, 20 drift that must be committed, 2 the site build or retention
# check fails. GNU make collapses every nonzero recipe exit to 2, so call the
# script directly when you need to distinguish them; the publish workflow
# passes PUBLISH_DECISION_FILE and branches on the `changed` output instead.
publish-check:
	python3 scripts/publish_check.py --root "$(STATUS_ROOT)" --base "$(PUBLISH_BASE)" $(if $(PUBLISH_DECISION_FILE),--decision-file "$(PUBLISH_DECISION_FILE)")

validate: $(GATE_TARGETS)
