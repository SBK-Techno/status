PLATFORM ?= ../sbk-ai-platform
BRAND ?= ../sbk-brand
STATUS_ROOT ?= $(CURDIR)

.PHONY: brand brand-check render validate

brand:
	cd "$(PLATFORM)" && go run ./cmd/genstatus -root "$(STATUS_ROOT)" -brand "$(BRAND)"
	$(MAKE) --no-print-directory render
	python3 scripts/prune_assets.py --root "$(STATUS_ROOT)" --write

brand-check:
	python3 scripts/check_generated.py --root "$(STATUS_ROOT)" --platform "$(PLATFORM)" --brand "$(BRAND)"
	python3 scripts/build_site.py --root "$(STATUS_ROOT)" --check
	python3 scripts/prune_assets.py --root "$(STATUS_ROOT)" --check

render:
	python3 scripts/build_site.py --root "$(STATUS_ROOT)" --write

validate:
	python3 scripts/validate_status.py --root "$(STATUS_ROOT)"
	python3 -m unittest discover -s scripts -p '*_test.py'
	$(MAKE) --no-print-directory brand-check
