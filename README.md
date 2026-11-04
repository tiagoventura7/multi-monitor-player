# Multi-Monitor Player

**English** · [Português](#português)

## English

A media player for Windows built for shows, events and AV installations: one control window and several independent fullscreen outputs on different screens.

The interface is available in Portuguese and English (switch live, remembered between sessions). The detailed documentation is currently in Portuguese: [docs/Documentacao.pdf](docs/Documentacao.pdf).

### Features

- **Several independent outputs**, each fullscreen on its own screen, with its own volume, resolution, FPS, output monitor and transport controls.
- **Playlist (show)** with per-item options: next item, repeat, fades, freeze first/last frame, IN/OUT marks, aspect, scheduling by time of day and weekday, groups and shuffle.
- **Sources**: video, audio, images, folders, YouTube (videos, playlists, lives), Spotify (played through YouTube), RTMP, NDI, capture devices, slideshows and countdowns.
- **Multiview** and **Composite Output**: crop, rotation, corner pin, edge blend and a single merged canvas.
- **Audio routing** in the style of Dante Controller (WASAPI, DirectSound, MME, WDM-KS and ASIO).
- **NDI output** for every output and **NDI input** with live preview.
- **Main/Backup synchronisation** between two computers, with file copy and automatic failover.

### Requirements

- Windows
- Python 3.12
- **libmpv**: put `mpv-2.dll` (or `libmpv-2.dll`) next to `player.py`. Get it from https://mpv.io/installation/ (libmpv builds for Windows).
- **ffmpeg**: `ffmpeg.exe` next to `player.py` or on your `PATH`. Get it from https://ffmpeg.org/download.html
- For NDI: the NDI runtime from NDI/Vizrt (https://ndi.video/tools/) and `ndi-python`.
- For current YouTube support: Deno or Node.js installed.

### Installation

```
git clone https://github.com/tiagoventura7/multi-monitor-player.git
cd multi-monitor-player
pip install -r requirements.txt
python player.py
```

On start-up the app checks for missing Python libraries and offers to install them. The `PLAYER_AUTOINSTALL` environment variable accepts `yes` (install without asking) or `no` (never ask).

### Status

Under active development and tested by the author on Windows. Some features have not yet been verified on every kind of hardware (ASIO devices, edge blend and corner pin rendering, failover under real network failures). The full list is in section 17 of the documentation.

### Logs and issues

The app writes `player_logs/player.log` next to the program. When reporting a problem, please open a GitHub Issue and attach that file (check first that it contains nothing you do not want to share, such as IP addresses or file names).

### Licence and third-party components

The code in this repository is released under the MIT licence (see `LICENSE`). External components are **not** included and keep their own licences: libmpv, ffmpeg, the NDI runtime, yt-dlp and the Python libraries in `requirements.txt`. Spotify audio is never downloaded: the app reads track titles and looks the songs up on YouTube.

Created by Tiago Ventura.

---

## Português

Leitor multimédia para Windows pensado para espetáculos, eventos e instalações audiovisuais: uma janela de controlo e vários outputs independentes em ecrãs inteiros diferentes.

A interface está disponível em português e inglês (muda-se em qualquer momento e fica guardada entre sessões). A documentação detalhada: [docs/Documentacao.pdf](docs/Documentacao.pdf).

### Funcionalidades

- **Vários outputs independentes**, cada um em ecrã inteiro num ecrã próprio, com volume, resolução, FPS, monitor de saída e transporte próprios.
- **Lista de reprodução (show)** com opções por item: próximo, repetição, fades, congelar primeira/última imagem, marcas IN/OUT, aspeto, agendamento por hora e dia da semana, grupos e shuffle.
- **Fontes**: vídeo, áudio, imagem, pastas, YouTube (vídeos, playlists, lives), Spotify (tocado via YouTube), RTMP, NDI, dispositivos de captura, slideshows e contagens decrescentes.
- **Multiview** e **Output Composto**: recorte, rotação, corner pin, edge blend e canvas único.
- **Encaminhamento de áudio** estilo Dante Controller (WASAPI, DirectSound, MME, WDM-KS e ASIO).
- **Saída NDI** de cada output e **entrada NDI** com pré-visualização.
- **Sincronização Main/Backup** entre dois computadores, com cópia de ficheiros e failover automático.

### Requisitos

- Windows
- Python 3.12
- **libmpv**: coloca `mpv-2.dll` (ou `libmpv-2.dll`) na mesma pasta que `player.py`. Descarrega de https://mpv.io/installation/ (builds de libmpv para Windows).
- **ffmpeg**: `ffmpeg.exe` na mesma pasta ou no `PATH`. Descarrega de https://ffmpeg.org/download.html
- Para NDI: o runtime NDI da NDI/Vizrt (https://ndi.video/tools/) e `ndi-python`.
- Para o YouTube atual: Deno ou Node.js instalados.

### Instalação

```
git clone https://github.com/tiagoventura7/multi-monitor-player.git
cd multi-monitor-player
pip install -r requirements.txt
python player.py
```

No arranque, a app verifica as bibliotecas Python em falta e oferece instalá-las. A variável de ambiente `PLAYER_AUTOINSTALL` aceita `yes` (instala sem perguntar) ou `no` (nunca pergunta).

### Estado

Em desenvolvimento ativo e testado pelo autor em Windows. Algumas funcionalidades ainda não foram validadas em todo o tipo de equipamento (dispositivos ASIO, renderização de edge blend e corner pin, failover com falhas de rede reais). A lista completa está na secção 17 da documentação.

### Registos e problemas

A app escreve `player_logs/player.log` junto ao programa. Ao reportar um problema, abre um Issue no GitHub e anexa esse ficheiro (confirma antes que não contém nada que não queiras partilhar, como endereços IP ou nomes de ficheiros).

### Licença e componentes de terceiros

O código deste repositório está sob a licença MIT (ver `LICENSE`). Os componentes externos **não** estão incluídos e têm as suas próprias licenças: libmpv, ffmpeg, o runtime NDI, o yt-dlp e as bibliotecas Python do `requirements.txt`. O áudio do Spotify nunca é descarregado: a app lê os títulos e procura as músicas no YouTube.

Criado por Tiago Ventura.
