PREFIXBIN=/usr/bin
SHAREDIR=/usr/share
NAME=altbooster

install: install-data install-bin

install-data:
	install -d $(SHAREDIR)/$(NAME)
	install -d $(SHAREDIR)/applications
	install -d $(SHAREDIR)/icons/hicolor/512x512/apps
	install -d $(PREFIXBIN)
	cp -a $(NAME).desktop $(SHAREDIR)/applications
	cp -a icons/$(NAME).svg $(SHAREDIR)/icons/hicolor/512x512/apps
	cp -a src/altbooster.py $(SHAREDIR)/$(NAME)
	cp -p -r src/core src/tabs src/ui src/modules $(SHAREDIR)/$(NAME)

install-bin:
	install -Dm755 $(NAME) $(PREFIXBIN)/$(NAME)
