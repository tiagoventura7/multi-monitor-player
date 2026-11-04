# Multi-Monitor Player

Leitor multimédia para Windows pensado para espetáculos, eventos e instalações audiovisuais: uma janela de controlo e vários outputs independentes em ecrãs inteiros diferentes.

*A multi-monitor media player for Windows: one control window, several independent fullscreen outputs. The interface is available in Portuguese and English (switch live). Documentation is currently in Portuguese.*

## Funcionalidades

- **Vários outputs** em ecrã inteiro, cada um com volume, resolução, FPS, monitor de saída e transporte próprios.
- **Lista de reprodução (show)** com próximo, repetição, fades, congelar frames, marcas IN/OUT, aspeto, agendamento por hora e dia da semana, grupos e shuffle.
- **Fontes**: vídeo, áudio, imagem, pastas, YouTube (vídeos, playlists, lives), Spotify (via YouTube), RTMP, NDI, dispositivos de captura, slideshows e contagens decrescentes.
- **Multiview** e **Output Composto** (recorte, rotação, corner pin, edge blend, canvas único).
- **Encaminhamento de áudio** estilo Dante Controller (WASAPI, DirectSound, MME, WDM-KS e ASIO).
- **Saída NDI** de cada output e **entrada NDI** com pré-visualização.
- **Sincronização Main/Backup** entre dois computadores, com cópia de ficheiros e failover automático.

Documentação completa: [docs/Documentacao.pdf](docs/Documentacao.pdf)

## Requisitos

- Windows
- Python 3.12
- **libmpv**: coloca `mpv-2.dll` (ou `libmpv-2.dll`) na mesma pasta que `player.py`. Descarrega de https://mpv.io/installation/ (builds de libmpv para Windows).
- **ffmpeg**: `ffmpeg.exe` na mesma pasta ou no PATH. Descarrega de https://ffmpeg.org/download.html
- Para NDI: o runtime NDI da NDI/Vizrt (https://ndi.video/tools/) e `ndi-python`.
- Para o YouTube atual: Deno ou Node.js instalados.

## Instalação

```
git clone https://github.com/tiagoventura7/multi-monitor-player.git
cd multi-monitor-player
pip install -r requirements.txt
python player.py
```

No arranque, a app verifica as bibliotecas em falta e oferece instalá-las. A variável `PLAYER_AUTOINSTALL` aceita `yes` (instala sem perguntar) ou `no` (nunca pergunta).

## Estado

Projeto em desenvolvimento ativo, testado pelo autor em Windows. Algumas funcionalidades ainda não foram validadas em todo o tipo de equipamento (ASIO, edge blend e corner pin, failover com falhas de rede reais). A lista completa está na secção 17 da documentação.

## Registos e problemas

A app escreve `player_logs/player.log` junto ao programa. Ao reportar um problema, anexa o ficheiro (confirma antes que não tem dados que não queiras partilhar). Usa os *Issues* do GitHub.

## Licença e componentes de terceiros

O código desta app está sob licença MIT (ver `LICENSE`). Os componentes externos **não** estão incluídos e têm as suas próprias licenças: libmpv, ffmpeg, o runtime NDI, yt-dlp e as bibliotecas Python do `requirements.txt`. O áudio do Spotify nunca é descarregado: a app lê os títulos e procura as músicas no YouTube.

Criado por Tiago Ventura.
