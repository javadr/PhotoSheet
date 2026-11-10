VER=$(shell grep __version__ src/__init__.py|cut -d= -f2|tr -d '\" ')

HAS_UV := $(shell command -v uv >/dev/null 2>&1 && echo 1)
PKG_TOOL := $(if $(HAS_UV),uv pip,pip)
BUILD_TOOL := $(if $(HAS_UV),uv build,python -m build)

VENV:=$(shell echo "$${VIRTUAL_ENV-}")
BIN:=$(if $(VENV),$(VIRTUAL_ENV)/bin,$(HOME)/.local/bin)
APP_DESKTOP:=$(HOME)/.local/share/applications/photosheet.desktop
APP_ICON:=$(HOME)/.local/share/icons/photosheet.png

.ONESHELL:

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

ver: ## Show the version number
	@echo photosheet, Ver. $(VER)

.PHONY: version
version: ## Show the version with the colourful banner
	@python -m src --version

.PHONY: run
run: ## Run the GUI straight from the source tree (no install needed)
	python -m src

.PHONY: run-cli
run-cli: ## Run the CLI, e.g. make run-cli ARGS="a.jpg b.jpg -o out.pdf --rows 2 --cols 2"
	python -m src.cli $(ARGS)

.PHONY: test
test: ## Run the test suite (Qt tests run offscreen)
	QT_QPA_PLATFORM=offscreen python -m pytest -q

.PHONY: install-desktop-file
install-desktop-file: ## Generate a desktop launcher for the GUI app
	@mkdir -p $(HOME)/.local/share/icons $(HOME)/.local/share/applications
	@cp src/icons/photosheet.png $(APP_ICON)
	@echo "Generating the desktop file..."
	cat <<EOF > $(APP_DESKTOP).tmp
	[Desktop Entry]
	Name=PhotoSheet
	Keywords=PhotoSheet;Photo;Print;Contact sheet;
	Exec=$(BIN)/photosheet
	Icon=photosheet
	StartupWMClass=photosheet
	Version=$(VER)
	Hidden=false
	Terminal=false
	Type=Application
	Categories=Graphics;Photography;
	Comment=Print many photos per sheet, with ICC colour management
	EOF
	mv $(APP_DESKTOP).tmp $(APP_DESKTOP)
	@chmod +x $(APP_DESKTOP)
	@echo "Desktop file generated successfully."

.PHONY: install-dev
install-dev: install-desktop-file ## Install photosheet locally in editable/development mode
	@$(PKG_TOOL) install -e .

.PHONY: uninstall
uninstall: ## Uninstall photosheet and remove its desktop file
	@echo "Uninstalling photosheet ..."
	@$(PKG_TOOL) uninstall photosheet
	rm -fv $(APP_DESKTOP) $(APP_ICON)

setup: ver ## Build source and wheel distribution packages
	$(BUILD_TOOL)

.PHONY: compile-pyinstaller
compile-pyinstaller: ver ## Build a standalone binary with PyInstaller
	pyinstaller --onefile --windowed --clean --noupx -i src/icons/photosheet.ico \
	--add-data src/icons:photosheet/icons src/__main__.py -n photosheet-v$(VER)
	ls -lh dist

clean: ver ## Clean build artifacts
	@rm -rfv photosheet.egg-info build dist .pytest_cache photosheet-v*.spec
	@find . -name __pycache__ -type d -not -path './.venv/*' -prune -exec rm -rfv {} +

# Aliases
.PHONY: lins
lins: install-dev
