# Status Filme

Mostra no Discord ("Assistindo ...") o filme ou a série que você está vendo no seu player de vídeo, com sinopse, pôster, barra de progresso e botões para o IMDb e o trailer.

Players suportados: 5KPlayer, Filmes e TV, Media Player, VLC, MPC-HC, MPC-BE e PotPlayer. Outros podem ser adicionados no `config.json`.

## Como usar

1. Instale o [Python](https://www.python.org/) (marque "Add to PATH").
2. Rode `build.bat`. O programa é gerado em `dist\StatusFilme.exe`.
3. Abra o programa. Ele fica na bandeja do Windows, perto do relógio.
4. (Opcional) Para sinopses, pôsteres e botões, crie uma chave gratuita no [TMDB](https://www.themoviedb.org/settings/api) e cole em `tmdb_chave` pelo menu **Abrir configuração**.

A configuração fica em `%APPDATA%\StatusFilme\config.json`, fora do projeto. **Não publique sua chave do TMDB.**

## Créditos

- Dados de filmes e séries: [TMDB](https://www.themoviedb.org/).
  *This product uses the TMDB API but is not endorsed or certified by TMDB.*
- Ícone de pipoca: [Flaticon](https://www.flaticon.com/).
- Bibliotecas: [pypresence](https://github.com/qwertyquerty/pypresence), [psutil](https://github.com/giampaolo/psutil), [pystray](https://github.com/moses-palmer/pystray), [Pillow](https://python-pillow.org/), [guessit](https://github.com/guessit-io/guessit), [PyInstaller](https://pyinstaller.org/).
