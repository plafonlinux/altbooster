DESTDIR   ?=
PREFIX    ?= /usr
BINDIR    ?= $(PREFIX)/bin
SHAREDIR  ?= $(PREFIX)/share
NAME      = altbooster

install: install-data install-icons install-bin install-locale

install-data:
	install -d $(DESTDIR)$(SHAREDIR)/$(NAME)
	install -d $(DESTDIR)$(SHAREDIR)/applications
	cp -a src/altbooster.py $(DESTDIR)$(SHAREDIR)/$(NAME)
	cp -p -r src/core src/tabs src/ui src/modules $(DESTDIR)$(SHAREDIR)/$(NAME)
	install -Dm644 $(NAME).desktop $(DESTDIR)$(SHAREDIR)/applications/$(NAME).desktop

install-icons:
	install -d $(DESTDIR)$(SHAREDIR)/icons/hicolor/scalable/apps
	install -d $(DESTDIR)$(SHAREDIR)/icons/hicolor/scalable/devices
	install -d $(DESTDIR)$(SHAREDIR)/icons/hicolor/512x512/apps
	install -m644 icons/$(NAME).svg $(DESTDIR)$(SHAREDIR)/icons/hicolor/scalable/apps/$(NAME).svg
	install -m644 icons/$(NAME).svg $(DESTDIR)$(SHAREDIR)/icons/hicolor/512x512/apps/$(NAME).svg
	for f in icons/hicolor/scalable/apps/*.svg; do \
		install -m644 "$$f" $(DESTDIR)$(SHAREDIR)/icons/hicolor/scalable/apps/; \
	done
	for f in icons/hicolor/scalable/devices/*.svg; do \
		install -m644 "$$f" $(DESTDIR)$(SHAREDIR)/icons/hicolor/scalable/devices/; \
	done

install-bin:
	install -d $(DESTDIR)$(BINDIR)
	install -Dm755 $(NAME) $(DESTDIR)$(BINDIR)/$(NAME)

install-locale:
	@for po in po/*.po; do \
		lang=$$(basename "$$po" .po); \
		install -d $(DESTDIR)$(SHAREDIR)/locale/$$lang/LC_MESSAGES; \
		msgfmt "$$po" -o $(DESTDIR)$(SHAREDIR)/locale/$$lang/LC_MESSAGES/$(NAME).mo; \
	done

pot:
	find src -name "*.py" | sort | xargs xgettext \
		--language=Python --keyword=_ \
		--package-name="ALT Booster" \
		--copyright-holder="ALT Booster contributors" \
		--msgid-bugs-address="https://github.com/plafonlinux/altbooster/issues" \
		-o po/$(NAME).pot

uninstall:
	rm -rf $(DESTDIR)$(SHAREDIR)/$(NAME)
	rm -f $(DESTDIR)$(SHAREDIR)/applications/$(NAME).desktop
	rm -f $(DESTDIR)$(SHAREDIR)/icons/hicolor/scalable/apps/$(NAME).svg
	rm -f $(DESTDIR)$(SHAREDIR)/icons/hicolor/512x512/apps/$(NAME).svg
	for f in icons/hicolor/scalable/apps/*.svg; do \
		rm -f $(DESTDIR)$(SHAREDIR)/icons/hicolor/scalable/apps/$$(basename "$$f"); \
	done
	for f in icons/hicolor/scalable/devices/*.svg; do \
		rm -f $(DESTDIR)$(SHAREDIR)/icons/hicolor/scalable/devices/$$(basename "$$f"); \
	done
	rm -f $(DESTDIR)$(BINDIR)/$(NAME)
	for po in po/*.po; do \
		lang=$$(basename "$$po" .po); \
		rm -f $(DESTDIR)$(SHAREDIR)/locale/$$lang/LC_MESSAGES/$(NAME).mo; \
	done
