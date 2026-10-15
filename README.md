# ekovoltis-it

Marketplace pluginów Claude Code dla działu IT Ekovoltis. Repozytorium jest prywatne, odczyt ma tylko zespół IT, i celowo nie jest synchronizowane do claude.ai: ogólnofirmowy marketplace `ekovoltis` widzą wszyscy pracownicy, a pluginy stąd działają z uprawnieniami deweloperskimi i mają trafiać wyłącznie do IT. Dostęp kontroluje GitHub, nie ustawienia organizacji w claude.ai (na planie Team nie da się tam przypisać pluginu grupie).

## Instalacja na stanowisku dyżurnego

Wymagany dostęp do tego repozytorium na GitHubie i zapisane poświadczenia git (np. `gh auth login`, potem `gh auth setup-git`). Claude Code pobiera marketplace w tle, bez okna logowania.

```bash
claude plugin marketplace add ekovoltis/ekovoltis-claude-marketplace-it
claude plugin install pp-support@ekovoltis-it
```

Pluginy z hookami działającymi w całej sesji mają `defaultEnabled: false`. Włącz je świadomie w sesji, w której są potrzebne, a ustawienia uzupełnij w `/plugin`.

Aktualizacja po zmianach w repozytorium:

```bash
claude plugin marketplace update ekovoltis-it
claude plugin update pp-support@ekovoltis-it
```

Claude Code serwuje plugin z kopii w `~/.claude/plugins/cache/` i odświeża ją dopiero przy zmianie numeru wersji w `plugin.json`. Zmiana treści bez podbicia wersji do nikogo nie dotrze.
