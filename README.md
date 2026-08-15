# YouTube Loader v0.5.0

Standalone FastAPI + yt-dlp + ffmpeg downloader prepared for later LanaAI integration.

## Features
- Video: MP4, MKV, WebM
- Audio: MP3, M4A, AAC, OPUS, FLAC, WAV
- Quality selection and metadata/thumbnail options
- Playlist detection + batch downloads
- Live progress + cancel
- SQLite history + persistent settings
- Context-aware suggestions after analysis
- Filterable download history with compact stats
- Dark Lana-style UI

## Start on the Mac
```bash
python3 -m venv .venv
source .venv/bin/activate
brew install ffmpeg
pip install -r requirements.txt
uvicorn backend.app:app --reload --host 127.0.0.1 --port 8787
```
Open http://127.0.0.1:8787

Use only for media you are permitted to download. No DRM/paywall bypass is implemented.


## v0.5.0

- Playlist-Einträge einzeln an- und abwählbar
- komplette Auswahl, invertierte Auswahl oder einzelne Playlist-Videos startbar
- mehrere Download-Jobs können parallel laufen
- Queue-/Aktiv-Zähler in der Oberfläche
- Playlist-Jobs zeigen Untereinträge und Einzel-Fortschritt
- Safe-Haven-Stand vor der ersten echten Mac-Runtime-Testreihe


## v0.5.0

- Duplikaterkennung vor dem erneuten Download
- Retry für fehlgeschlagene, abgebrochene und teilweise erfolgreiche Jobs
- robuste Playlist-Verarbeitung: einzelne nicht verfügbare Einträge stoppen nicht den ganzen Batch
- Schnell-Presets: MP4 1080p, MP4 Best, MP3 320, M4A 256
- Dockerfile mit FFmpeg für eine einfache Demo-Bereitstellung
- Render Blueprint (`render.yaml`) für einen temporären öffentlichen Testserver

### Render-Demo

Der Free-Webservice eignet sich nur zum Testen der Oberfläche und kleiner eigener/freigegebener Downloads.
Lokale Dateien und SQLite-Daten sind auf einem kostenlosen Render-Webservice nicht persistent.
