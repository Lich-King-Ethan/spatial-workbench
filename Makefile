.PHONY: test check release package install diagnostics

test:
	python3 -m unittest discover -s tests

check:
	python3 -m compileall -q spatial tools
	bash -n build.sh install.sh
	python3 -m unittest discover -s tests

release:
	python3 tools/make-release.py

package:
	./build.sh

install:
	bash install.sh

diagnostics:
	python3 tools/diagnostics.py
