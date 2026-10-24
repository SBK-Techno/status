PLATFORM ?= ../sbk-ai-platform
BRAND ?= ../sbk-brand

.PHONY: brand brand-check validate

brand:
	cd "$(PLATFORM)" && go run ./cmd/genstatus -root ../sbk-status -brand "$(BRAND)"

brand-check:
	cd "$(PLATFORM)" && go run ./cmd/genstatus -root ../sbk-status -brand "$(BRAND)" -check

validate:
	python3 -m json.tool status.json >/dev/null
	$(MAKE) brand-check
