"""
Status Filme 2.0
Mostra no Discord ("Assistindo ...") o filme ou a série aberta no seu player de vídeo.

- Busca sinopse, pôster, duração, IMDb e trailer no TMDB (opcional, precisa de chave gratuita)
- Barra de progresso estimada pela duração do filme/episódio
- Fica na bandeja do Windows (perto do relógio), sem janela de console
- Opção "Iniciar com o Windows"
- Reconecta sozinho se o Discord for fechado e aberto de novo
- Configuração em %APPDATA%\\StatusFilme\\config.json

Uso para depuração:  python status_filme.py --console
"""

import asyncio
import ctypes
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from logging.handlers import RotatingFileHandler

import psutil
from pypresence import Presence

try:
    from pypresence import ActivityType  # pypresence >= 4.3
except ImportError:
    ActivityType = None

try:
    from guessit import guessit  # mesmo "leitor de nomes" usado por Plex/Kodi/Sonarr
except Exception:
    guessit = None

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    pystray = None

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

APP_NOME = "Status Filme"
APP_VERSAO = "2.1.0"
EXECUTAVEL = getattr(sys, "frozen", False)

PASTA_DADOS = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "StatusFilme")
ARQ_CONFIG = os.path.join(PASTA_DADOS, "config.json")
ARQ_CACHE = os.path.join(PASTA_DADOS, "cache_tmdb.json")
ARQ_LOG = os.path.join(PASTA_DADOS, "status_filme.log")

ICONE_PIPOCA = "https://cdn-icons-png.flaticon.com/256/12608/12608932.png"
CHAVE_REGISTRO_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
NOME_REGISTRO = "StatusFilme"

CONFIG_PADRAO = {
    "_ajuda": {
        "client_id": "ID do aplicativo no Discord Developer Portal (o nome do app aparece no perfil).",
        "tmdb_chave": "Chave da API do TMDB (v3) ou Token de leitura (v4). Grátis em themoviedb.org > Configurações > API. Vazio = sem busca online.",
        "idioma": "Idioma das sinopses do TMDB, ex.: pt-BR, en-US.",
        "players": "Processo do player -> nome exibido. Adicione outros players aqui.",
        "intervalo_segundos": "De quantos em quantos segundos verificar o player.",
        "usar_guessit": "true = usa o guessit para entender o nome do arquivo; false = usa o leitor simples interno.",
        "barra_progresso": "Mostra a barra com tempo restante (estimada pela duração do TMDB; não acompanha pausas).",
        "botoes": "Botões 'IMDb' e 'Trailer' no perfil (você não vê os próprios botões; os outros veem).",
        "sinopses_manuais": "Usadas quando o TMDB não está configurado ou não encontra o título.",
    },
    "client_id": "1533585982711136376",
    "tmdb_chave": "",
    "idioma": "pt-BR",
    "players": {
        "5KPlayer.exe": "5KPlayer",
        "Video.UI.exe": "Filmes e TV",
        "Microsoft.Media.Player.exe": "Media Player",
        "vlc.exe": "VLC",
        "mpc-hc64.exe": "MPC-HC",
        "mpc-be64.exe": "MPC-BE",
        "PotPlayerMini64.exe": "PotPlayer",
    },
    "extensoes": [".mp4", ".mkv", ".avi", ".rmvb", ".wmv", ".mov", ".m4v", ".webm", ".ts"],
    "intervalo_segundos": 5,
    "usar_guessit": True,
    "mostrar_sinopse": True,
    "mostrar_poster": True,
    "barra_progresso": True,
    "botoes": True,
    "texto_padrao": "Sessão de cinema em casa",
    "sinopses_manuais": {
        "Pulp Fiction": "As vidas de dois assassinos, um boxeador e um gangster se cruzam.",
        "Anjos da Noite": "Selene, uma guerreira vampira, descobre uma conspiração.",
        "Anjos De Noite": "Selene, uma guerreira vampira, descobre uma conspiração.",
        "A Casa do Dragao": "A história da disputa de poder dentro da Dinastia Targaryen.",
    },
}

log = logging.getLogger("status_filme")


# ---------------------------------------------------------------------------
# Configuração, log e utilidades
# ---------------------------------------------------------------------------

def configurar_log(console=False):
    os.makedirs(PASTA_DADOS, exist_ok=True)
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%d/%m %H:%M:%S")
    arq = RotatingFileHandler(ARQ_LOG, maxBytes=512_000, backupCount=1, encoding="utf-8")
    arq.setFormatter(fmt)
    log.addHandler(arq)
    if console and sys.stdout:
        tela = logging.StreamHandler(sys.stdout)
        tela.setFormatter(fmt)
        log.addHandler(tela)


def carregar_config():
    """Lê o config.json, completa chaves que faltarem e devolve (config, primeira_vez)."""
    os.makedirs(PASTA_DADOS, exist_ok=True)
    primeira_vez = not os.path.exists(ARQ_CONFIG)
    cfg = json.loads(json.dumps(CONFIG_PADRAO))  # cópia profunda
    if not primeira_vez:
        try:
            with open(ARQ_CONFIG, encoding="utf-8") as f:
                usuario = json.load(f)
            cfg.update({k: v for k, v in usuario.items() if k != "_ajuda"})
        except Exception as e:
            log.error("config.json inválido (%s). Usando padrões.", e)
            return cfg, False
    try:
        with open(ARQ_CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError as e:
        log.warning("Não foi possível salvar o config.json: %s", e)
    return cfg, primeira_vez


def normalizar(texto):
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", texto).strip()


def cortar(texto, limite=128):
    """Discord aceita de 2 a 128 caracteres em details/state/large_text."""
    if not texto:
        return None
    texto = " ".join(str(texto).split())
    if len(texto) > limite:
        texto = texto[: limite - 1].rstrip() + "…"
    return texto if len(texto) >= 2 else texto + " "


# ---------------------------------------------------------------------------
# Interpretação do nome do arquivo
# ---------------------------------------------------------------------------

TERMOS_SUJEIRA = {
    "2160p", "1080p", "720p", "480p", "4k", "uhd", "bluray", "blu-ray", "brrip", "bdrip",
    "web", "web-dl", "webdl", "webrip", "hdrip", "dvdrip", "hdtv", "hmax", "amzn", "nf",
    "dsnp", "atvp", "hdr", "hdr10", "dv", "dual", "dublado", "legendado", "nacional",
    "x264", "x265", "h264", "h265", "hevc", "avc", "aac", "ac3", "eac3", "ddp", "ddp5",
    "dts", "atmos", "10bit", "remux", "proper", "repack", "extended", "unrated", "imax",
    "www", "multi",
}
RE_EPISODIO = re.compile(r"\bS(\d{1,2})\s*[E_xX](\d{1,3})\b", re.IGNORECASE)
RE_EPISODIO_ALT = re.compile(r"\b(\d{1,2})x(\d{2,3})\b", re.IGNORECASE)
RE_ANO = re.compile(r"(19\d{2}|20\d{2})")


def interpretar_nome(caminho, usar_guessit=True):
    """Entende o nome do arquivo com o guessit; se não der, usa o leitor simples."""
    if usar_guessit and guessit is not None:
        try:
            info = _interpretar_guessit(caminho)
            if info:
                return info
        except Exception as e:
            log.warning("guessit falhou em '%s': %s", os.path.basename(caminho), e)
    return _interpretar_simples(caminho)


def _primeiro(valor):
    """guessit devolve lista em casos como S01E01E02; ficamos com o primeiro."""
    if isinstance(valor, (list, tuple)):
        valor = valor[0] if valor else None
    try:
        return int(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None


def _interpretar_guessit(caminho):
    resultado = guessit(os.path.basename(caminho))
    if not resultado.get("title"):
        # nome de arquivo inútil (ex.: "video.mp4"): deixa o guessit olhar as pastas também
        resultado = guessit(caminho)
    titulo = resultado.get("title")
    if isinstance(titulo, (list, tuple)):
        titulo = titulo[0] if titulo else None
    if not titulo:
        return None
    temporada = _primeiro(resultado.get("season"))
    episodio = _primeiro(resultado.get("episode"))
    serie = resultado.get("type") == "episode" or temporada is not None or episodio is not None
    return {
        "titulo": str(titulo).strip(),
        "ano": _primeiro(resultado.get("year")),
        "temporada": temporada,
        "episodio": episodio,
        "serie": serie,
        "episodio_titulo": resultado.get("episode_title"),
        "leitor": "guessit",
    }


def _interpretar_simples(caminho):
    """'A.Casa.do.Dragao.S02E03.1080p.WEB-DL.mkv' -> {'titulo': 'A Casa do Dragao', 'temporada': 2, ...}"""
    base = os.path.splitext(os.path.basename(caminho))[0]
    # remove etiqueta de grupo no início, ex.: "[site.com] Filme..."
    texto = re.sub(r"^\s*[\[\{][^\]\}]*[\]\}]\s*", "", base)
    texto = re.sub(r"[\[\]\(\)\{\}._]+", " ", texto)

    temporada = episodio = None
    m = RE_EPISODIO.search(texto) or RE_EPISODIO_ALT.search(texto)
    if m:
        temporada, episodio = int(m.group(1)), int(m.group(2))
        texto = texto[: m.start()]

    tokens = texto.split()
    titulo, ano = [], None
    for i, tok in enumerate(tokens):
        limpo = tok.strip("-–·,").lower()
        if not limpo:
            continue
        if RE_ANO.fullmatch(limpo) and titulo:
            proximo = tokens[i + 1].strip("-–").lower() if i + 1 < len(tokens) else ""
            if not RE_ANO.fullmatch(proximo):  # "Blade Runner 2049 2017"
                ano = int(limpo)
                break
        if limpo in TERMOS_SUJEIRA or re.fullmatch(r"\d{3,4}p", limpo):
            if titulo:
                break
            continue
        titulo.append(tok.strip("-–"))

    nome = " ".join(titulo).strip(" -") or base
    return {"titulo": nome, "ano": ano, "temporada": temporada, "episodio": episodio,
            "serie": temporada is not None, "episodio_titulo": None, "leitor": "simples"}


def sinopse_manual(cfg, titulo):
    alvo = normalizar(titulo)
    for chave, texto in (cfg.get("sinopses_manuais") or {}).items():
        k = normalizar(chave)
        if k and (k in alvo or alvo in k):
            return texto
    return None


# ---------------------------------------------------------------------------
# TMDB
# ---------------------------------------------------------------------------

class TMDB:
    BASE = "https://api.themoviedb.org/3"
    IMG = "https://image.tmdb.org/t/p/w500"

    def __init__(self, chave, idioma):
        self.chave = chave.strip()
        self.idioma = idioma or "pt-BR"
        self.cache = {}
        try:
            with open(ARQ_CACHE, encoding="utf-8") as f:
                self.cache = json.load(f)
        except Exception:
            pass

    def _get(self, caminho, **params):
        params["language"] = self.idioma
        headers = {"Accept": "application/json", "User-Agent": f"StatusFilme/{APP_VERSAO}"}
        if len(self.chave) > 40:  # token v4 (Bearer)
            headers["Authorization"] = "Bearer " + self.chave
        else:
            params["api_key"] = self.chave
        url = f"{self.BASE}{caminho}?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=8) as r:
            return json.load(r)

    def _salvar_cache(self):
        try:
            with open(ARQ_CACHE, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, ensure_ascii=False)
        except OSError:
            pass

    @staticmethod
    def _trailer(videos):
        for v in (videos or {}).get("results", []):
            if v.get("site") == "YouTube" and v.get("type") == "Trailer" and v.get("key"):
                return "https://www.youtube.com/watch?v=" + v["key"]
        return None

    def buscar(self, info):
        chave = f"{normalizar(info['titulo'])}|{info['ano']}|{info['temporada']}|{info['episodio']}|{self.idioma}"
        if chave in self.cache:
            return self.cache[chave]
        try:
            if info.get("serie"):
                resultado = self._serie(info)
            else:
                resultado = self._filme(info)
        except Exception as e:
            log.warning("TMDB falhou para '%s': %s", info["titulo"], e)
            return None  # não guarda falhas de rede no cache
        self.cache[chave] = resultado
        self._salvar_cache()
        return resultado

    def _filme(self, info):
        achados = []
        if info["ano"]:
            achados = self._get("/search/movie", query=info["titulo"], year=info["ano"]).get("results", [])
        if not achados:
            achados = self._get("/search/movie", query=info["titulo"]).get("results", [])
        if not achados:
            return None
        d = self._get(f"/movie/{achados[0]['id']}", append_to_response="videos,external_ids",
                      include_video_language=f"{self.idioma.split('-')[0]},en,null")
        return {
            "tipo": "filme",
            "titulo": d.get("title") or info["titulo"],
            "ano": (d.get("release_date") or "")[:4] or None,
            "sinopse": d.get("overview") or None,
            "poster": self.IMG + d["poster_path"] if d.get("poster_path") else None,
            "duracao_min": d.get("runtime") or None,
            "imdb": d.get("imdb_id") or (d.get("external_ids") or {}).get("imdb_id"),
            "trailer": self._trailer(d.get("videos")),
            "tmdb_url": f"https://www.themoviedb.org/movie/{d['id']}",
        }

    def _serie(self, info):
        achados = self._get("/search/tv", query=info["titulo"]).get("results", [])
        if not achados:
            return None
        d = self._get(f"/tv/{achados[0]['id']}", append_to_response="videos,external_ids",
                      include_video_language=f"{self.idioma.split('-')[0]},en,null")
        ep = {}
        if info.get("temporada") is not None and info.get("episodio") is not None:
            try:
                ep = self._get(f"/tv/{d['id']}/season/{info['temporada']}/episode/{info['episodio']}")
            except Exception:
                pass
        duracoes = d.get("episode_run_time") or []
        return {
            "tipo": "serie",
            "titulo": d.get("name") or info["titulo"],
            "ano": (d.get("first_air_date") or "")[:4] or None,
            "sinopse": ep.get("overview") or d.get("overview") or None,
            "episodio_nome": ep.get("name") or None,
            "poster": self.IMG + d["poster_path"] if d.get("poster_path") else None,
            "duracao_min": ep.get("runtime") or (duracoes[0] if duracoes else None),
            "imdb": (d.get("external_ids") or {}).get("imdb_id"),
            "trailer": self._trailer(d.get("videos")),
            "tmdb_url": f"https://www.themoviedb.org/tv/{d['id']}",
        }


# ---------------------------------------------------------------------------
# Montagem do status do Discord
# ---------------------------------------------------------------------------

def montar_atividade(cfg, info, meta, player_nome, inicio):
    meta = meta or {}
    serie = info.get("serie")
    titulo = meta.get("titulo") or info["titulo"]
    ano = meta.get("ano") or info["ano"]
    sinopse = meta.get("sinopse") or sinopse_manual(cfg, titulo) or sinopse_manual(cfg, info["titulo"])

    if serie:
        details = titulo
        partes = []
        if info.get("temporada") is not None:
            partes.append(f"T{info['temporada']}")
        if info.get("episodio") is not None:
            partes.append(f"E{info['episodio']}")
        state = ":".join(partes)
        nome_ep = meta.get("episodio_nome") or info.get("episodio_titulo")
        if nome_ep:
            state = f"{state} · {nome_ep}" if state else nome_ep
        state = state or cfg.get("texto_padrao")
    else:
        details = f"{titulo} ({ano})" if ano else titulo
        state = sinopse if cfg.get("mostrar_sinopse", True) and sinopse else cfg.get("texto_padrao")

    atividade = {
        "details": cortar(details),
        "state": cortar(state),
        "start": inicio,
        "large_image": ICONE_PIPOCA,
        "large_text": cortar(sinopse or titulo),
    }

    if cfg.get("mostrar_poster", True) and meta.get("poster"):
        atividade["large_image"] = meta["poster"]
        atividade["small_image"] = ICONE_PIPOCA
        atividade["small_text"] = cortar(f"Assistindo no {player_nome}")
    else:
        atividade["large_text"] = cortar(f"{sinopse or titulo} — {player_nome}")

    if cfg.get("barra_progresso", True) and meta.get("duracao_min"):
        atividade["end"] = inicio + int(meta["duracao_min"]) * 60

    if cfg.get("botoes", True):
        botoes = []
        if meta.get("imdb"):
            botoes.append({"label": "Ver no IMDb", "url": f"https://www.imdb.com/title/{meta['imdb']}/"})
        if meta.get("trailer"):
            botoes.append({"label": "Assistir trailer", "url": meta["trailer"]})
        if not botoes and meta.get("tmdb_url"):
            botoes.append({"label": "Ver no TMDB", "url": meta["tmdb_url"]})
        if botoes:
            atividade["buttons"] = botoes[:2]

    return {k: v for k, v in atividade.items() if v is not None}


# ---------------------------------------------------------------------------
# Monitor (roda numa thread separada)
# ---------------------------------------------------------------------------

class Monitor:
    REENVIO_SEGUNDOS = 60  # reenvia o status de tempos em tempos para detectar queda do Discord

    def __init__(self, cfg, ao_mudar_status=None):
        self.cfg = cfg
        self.ao_mudar_status = ao_mudar_status
        self.parar = threading.Event()
        self.pausado = False
        self.pedido_recarregar = False
        self.status = "Iniciando..."
        self.rpc = None
        self.conectado = False
        self.loop = None
        self.tmdb = None
        self.arquivo_atual = None
        self.atividade_atual = None
        self.ultimo_envio = 0
        self._aplicar_config()

    # -- utilidades --
    def _aplicar_config(self):
        chave = (self.cfg.get("tmdb_chave") or "").strip()
        self.tmdb = TMDB(chave, self.cfg.get("idioma")) if chave else None
        self.players = {k.lower(): v for k, v in (self.cfg.get("players") or {}).items()}
        self.extensoes = tuple(e.lower() for e in self.cfg.get("extensoes") or [])

    def _definir_status(self, texto):
        if texto != self.status:
            self.status = texto
            log.info(texto)
            if self.ao_mudar_status:
                try:
                    self.ao_mudar_status(texto)
                except Exception:
                    pass

    # -- Discord --
    def _conectar(self):
        try:
            self.rpc = Presence(str(self.cfg["client_id"]), loop=self.loop)
            self.rpc.connect()
            self.conectado = True
            self.arquivo_atual = None  # força reenviar o que estiver tocando
            self._definir_status("Conectado ao Discord. Aguardando um player...")
        except Exception as e:
            self.conectado = False
            self._definir_status("Discord não encontrado. Tentando de novo...")
            log.debug("Falha ao conectar: %s", e)

    def _desconectar(self):
        if self.rpc:
            try:
                self.rpc.clear()
            except Exception:
                pass
            try:
                self.rpc.close()
            except Exception:
                pass
        self.rpc = None
        self.conectado = False

    def _enviar(self, atividade):
        dados = dict(atividade)
        if ActivityType is not None:
            dados["activity_type"] = ActivityType.WATCHING
        try:
            try:
                self.rpc.update(**dados)
            except TypeError:  # pypresence antigo sem activity_type
                dados.pop("activity_type", None)
                self.rpc.update(**dados)
            self.ultimo_envio = time.time()
            return True
        except Exception as e:
            log.warning("Conexão com o Discord perdida: %s", e)
            self._desconectar()
            self._definir_status("Discord desconectado. Tentando reconectar...")
            return False

    def _limpar(self):
        self.arquivo_atual = None
        self.atividade_atual = None
        if self.conectado:
            try:
                self.rpc.clear()
            except Exception:
                self._desconectar()

    # -- detecção do player --
    def _detectar(self):
        for proc in psutil.process_iter(["name"]):
            nome = (proc.info.get("name") or "").lower()
            if nome not in self.players:
                continue
            try:
                for arq in proc.open_files():
                    if arq.path.lower().endswith(self.extensoes):
                        return arq.path, self.players[nome]
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                continue
        return None, None

    # -- laço principal --
    def rodar(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        log.info("%s %s iniciado.", APP_NOME, APP_VERSAO)

        while not self.parar.is_set():
            try:
                self._passo()
            except Exception:
                log.exception("Erro inesperado no monitor")
            espera = self.cfg.get("intervalo_segundos", 5) if self.conectado else 15
            self.parar.wait(max(1, float(espera)))

        self._desconectar()
        try:
            self.loop.close()
        except Exception:
            pass
        log.info("Encerrado.")

    def _passo(self):
        if self.pedido_recarregar:
            self.pedido_recarregar = False
            self._desconectar()
            self._aplicar_config()
            log.info("Configuração recarregada.")

        if not self.conectado:
            self._conectar()
            if not self.conectado:
                return

        if self.pausado:
            if self.arquivo_atual:
                self._limpar()
            self._definir_status("Status pausado.")
            return

        caminho, player = self._detectar()

        if caminho and caminho != self.arquivo_atual:
            info = interpretar_nome(caminho, self.cfg.get("usar_guessit", True))
            log.info("Detectado: %s -> %s", os.path.basename(caminho), info)
            meta = self.tmdb.buscar(info) if self.tmdb else None
            inicio = int(time.time())
            self.atividade_atual = montar_atividade(self.cfg, info, meta, player, inicio)
            self.arquivo_atual = caminho
            if self._enviar(self.atividade_atual):
                self._definir_status(f"Assistindo: {self.atividade_atual.get('details', info['titulo'])}")

        elif caminho and time.time() - self.ultimo_envio > self.REENVIO_SEGUNDOS:
            self._enviar(self.atividade_atual)  # mantém vivo e detecta queda

        elif not caminho and self.arquivo_atual:
            self._limpar()
            self._definir_status("Player fechado. Aguardando um player...")

        elif not caminho:
            self._definir_status("Conectado ao Discord. Aguardando um player...")


# ---------------------------------------------------------------------------
# Windows: instância única e iniciar com o Windows
# ---------------------------------------------------------------------------

_mutex = None


def ja_esta_rodando():
    global _mutex
    if os.name != "nt":
        return False
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _mutex = k32.CreateMutexW(None, False, "Local\\StatusFilme_instancia_unica")
    return ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS


def comando_inicializacao():
    if EXECUTAVEL:
        return f'"{sys.executable}"'
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    return f'"{pythonw}" "{os.path.abspath(__file__)}"'


def autostart_valor():
    if os.name != "nt":
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CHAVE_REGISTRO_RUN) as k:
            return winreg.QueryValueEx(k, NOME_REGISTRO)[0]
    except OSError:
        return None


def definir_autostart(ativar):
    if os.name != "nt":
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CHAVE_REGISTRO_RUN, 0, winreg.KEY_SET_VALUE) as k:
        if ativar:
            winreg.SetValueEx(k, NOME_REGISTRO, 0, winreg.REG_SZ, comando_inicializacao())
        else:
            try:
                winreg.DeleteValue(k, NOME_REGISTRO)
            except OSError:
                pass


def corrigir_autostart():
    """Se o .exe mudou de lugar, atualiza o caminho salvo no registro."""
    valor = autostart_valor()
    if valor and valor != comando_inicializacao():
        definir_autostart(True)
        log.info("Caminho do 'Iniciar com o Windows' atualizado.")


# ---------------------------------------------------------------------------
# Ícone da bandeja
# ---------------------------------------------------------------------------

def imagem_icone():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    caminho = os.path.join(base, "icone.ico")
    if os.path.exists(caminho):
        try:
            return Image.open(caminho)
        except Exception:
            pass
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 62, 62), radius=14, fill=(88, 101, 242, 255))
    d.polygon([(24, 16), (24, 48), (48, 32)], fill=(255, 255, 255, 255))
    return img


def abrir_no_bloco_de_notas(caminho):
    try:
        subprocess.Popen(["notepad.exe", caminho])
    except Exception:
        os.startfile(caminho)  # type: ignore[attr-defined]


def rodar_bandeja(cfg, primeira_vez):
    icone = pystray.Icon("StatusFilme", imagem_icone(), APP_NOME)

    def ao_mudar_status(texto):
        icone.title = cortar(f"{APP_NOME} — {texto}", 120)
        try:
            icone.update_menu()
        except Exception:
            pass

    monitor = Monitor(cfg, ao_mudar_status)
    thread = threading.Thread(target=monitor.rodar, name="monitor", daemon=True)

    def alternar_pausa(_icone, _item):
        monitor.pausado = not monitor.pausado

    def alternar_autostart(_icone, _item):
        try:
            definir_autostart(not autostart_valor())
        except OSError as e:
            log.error("Não foi possível alterar a inicialização automática: %s", e)
        icone.update_menu()

    def abrir_config(_icone, _item):
        abrir_no_bloco_de_notas(ARQ_CONFIG)

    def recarregar(_icone, _item):
        novo, _ = carregar_config()
        monitor.cfg.clear()
        monitor.cfg.update(novo)
        monitor.pedido_recarregar = True

    def abrir_log(_icone, _item):
        abrir_no_bloco_de_notas(ARQ_LOG)

    def sair(_icone, _item):
        monitor.parar.set()
        thread.join(timeout=5)
        icone.stop()

    icone.menu = pystray.Menu(
        pystray.MenuItem(lambda _i: cortar(monitor.status, 60), None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Pausar status", alternar_pausa, checked=lambda _i: monitor.pausado),
        pystray.MenuItem("Iniciar com o Windows", alternar_autostart,
                         checked=lambda _i: bool(autostart_valor())),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Abrir configuração", abrir_config),
        pystray.MenuItem("Recarregar configuração", recarregar),
        pystray.MenuItem("Ver log", abrir_log),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Sair", sair),
    )

    def ao_iniciar(ic):
        ic.visible = True
        thread.start()
        if primeira_vez:
            try:
                ic.notify("Estou na bandeja, perto do relógio. Clique com o botão direito para "
                          "configurar a chave do TMDB e ativar 'Iniciar com o Windows'.", APP_NOME)
            except Exception:
                pass

    icone.run(setup=ao_iniciar)


# ---------------------------------------------------------------------------
# Início
# ---------------------------------------------------------------------------

def main():
    modo_console = "--console" in sys.argv or pystray is None
    configurar_log(console=modo_console)

    if ja_esta_rodando():
        log.info("Já existe uma instância rodando. Saindo.")
        return

    cfg, primeira_vez = carregar_config()
    corrigir_autostart()

    if modo_console:
        print(f"{APP_NOME} {APP_VERSAO} — modo console (Ctrl+C para sair)")
        print(f"Configuração: {ARQ_CONFIG}")
        monitor = Monitor(cfg)
        try:
            monitor.rodar()
        except KeyboardInterrupt:
            monitor.parar.set()
            monitor._desconectar()
    else:
        rodar_bandeja(cfg, primeira_vez)


if __name__ == "__main__":
    main()
