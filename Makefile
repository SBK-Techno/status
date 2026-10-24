BRAND ?= ../sbk-brand
.PHONY: brand brand-check validate

brand:
	@test -d "$(BRAND)" || { echo "brand: $(BRAND) is missing" >&2; exit 1; }
	cp "$(BRAND)/assets/web/brand.css" brand.css
	cp "$(BRAND)/assets/svg/logo-color.svg" logo-color.svg
	cp "$(BRAND)/assets/svg/icon-color.svg" icon-color.svg
	cp "$(BRAND)/assets/web/favicon.ico" favicon.ico
	sha256sum brand.css favicon.ico icon-color.svg logo-color.svg > brand.lock

brand-check:
	sha256sum -c brand.lock

validate:
	python3 -m json.tool status.json >/dev/null
	sha256sum -c brand.lock
