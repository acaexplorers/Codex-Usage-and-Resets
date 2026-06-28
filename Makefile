.PHONY: test typecheck build safety package

test:
	/usr/bin/python3 test_codex_reset_expiry.py

typecheck:
	swiftc -typecheck CodexUsageMenuBar.swift

build:
	./build-codex-menubar-app.command

safety:
	@if command -v rg >/dev/null 2>&1; then \
		! rg -n "rate-limit-reset-credits/consume|redeem_request_id|\\.post\\(" codex-reset-expiry.py codex-reset-widget.5m.py CodexUsageMenuBar.swift *.command; \
	else \
		! grep -R -n -E "rate-limit-reset-credits/consume|redeem_request_id|\\.post\\(" codex-reset-expiry.py codex-reset-widget.5m.py CodexUsageMenuBar.swift *.command; \
	fi

package:
	rm -f ../codex-usage-reset-source.zip
	zip -r ../codex-usage-reset-source.zip . -x "./.git/*" "./.build/*" "./Codex Usage.app/*" "./__pycache__/*" "./*.pyc" "./.DS_Store"
