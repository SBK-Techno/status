PLATFORM ?= ../sbk-ai-platform
BRAND ?= ../sbk-brand
STATUS_ROOT ?= $(CURDIR)

.PHONY: brand brand-check validate

brand:
	cd "$(PLATFORM)" && go run ./cmd/genstatus -root "$(STATUS_ROOT)" -brand "$(BRAND)"

brand-check:
	cd "$(PLATFORM)" && go run ./cmd/genstatus -root "$(STATUS_ROOT)" -brand "$(BRAND)" -check

validate:
	python3 scripts/validate_status.py
	python3 -m unittest discover -s scripts -p '*_test.py'
	$(MAKE) brand-check
