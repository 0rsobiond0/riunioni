# Registratore AI per riunioni

Implementazione del progetto descritto in `~/Scaricati/progetto-ai-recorder-riunioni.md`.
Portatile in sede collegato alla TV via HDMI, LLM opzionale, documenti preparati
in locale e inviati a mano via WhatsApp (human in the loop).

## Hardware rilevato

- Intel **i3-4030U** @ 1,9 GHz, 2 core / 4 thread, **AVX2 presente**
- Intel Haswell-ULT integrata (nessuna GPU per inferenza)
- 8 GB RAM, Fedora 44 KDE
- Microfono analogico interno + presa per mic USB (da collegare al momento dell'uso)

## Installazione (Fedora, senza sudo)

```bash
cd ~/riunioni
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

`av` è fissato a 18.1.1: la 19.x ha rimosso un argomento ancora usato da
`faster-whisper` 1.2.1. `mermaid.min.js` è già in `static/vendor/`, quindi la
mappa live funziona anche senza internet.

Sono disponibili due modelli Vosk italiani, già scaricati ed estratti in `models/`:

- `vosk-model-it-0.22` — **default**, 2 GB su disco, ~2,1 GB di RAM, 7-9 s di
  carico prima di aprire il microfono, 0,35x in elaborazione;
- `vosk-model-small-it-0.22` — 48 MB, ~1 s di carico, 0,10x, ma meno accurato.

Per passare a quello leggero basta decommentare la riga `vosk_model` piccola in
`config.yaml`. Il modello viene caricato sempre prima di avviare la cattura,
quindi quei secondi non finiscono nella trascrizione.

## Avvio

```bash
cd ~/riunioni
.venv/bin/python server.py
```

Sul PC apri <http://localhost:8000/display>, trascina la finestra sul display
HDMI e mettila a schermo intero (`F11`).

- **Barra in alto**: pulsante `☀ Chiaro` / `☾ Scuro` per passare dal tema scuro
  a quello bianco tenue (scelta ricordata dal browser), poi `Avvia` e
  `Ferma e salva`. Quest'ultima chiude soltanto la registrazione: salva
  `audio.wav`, `riunione.json` e `trascrizione.txt` e basta.
- **Tre menu a tendina** sotto la barra in alto, per tenere tutto separato:
  - `Riunioni base` — solo audio e trascrizione live (Vosk);
  - `Accurate` — riunioni con trascrizione Whisper, con la stima del tempo;
  - `Documenti` — riunioni con file generati (PDF/DOCX/Markdown/diagrammi).

  Una riunione può comparire in più menu: per esempio una con trascrizione
  accurata e documenti si trova sia in `Accurate` sia in `Documenti`.
  Cliccando una voce si seleziona la riunione e si apre subito il pannello
  corrispondente (trascrizione per `base`/`Accurate`, documenti per `Documenti`).
- **Barra azioni** (sempre sopra i pannelli, quindi i pulsanti restano cliccabili
  anche mentre si legge una trascrizione o si sfogliano i documenti): la
  riunione selezionata, `Mostra trascrizione`, `Mostra documenti`,
  `Genera documenti` e `Ritrascrizione accurata`. Quest'ultima funziona anche su
  riunioni passate che hanno già i documenti: Whisper riscrive
  `trascrizione.txt`/`.json` e rigenera i file. Nel viewer documenti c'è anche
  un pulsante `Ritrascrivi accurata` diretto. La stima del tempo è calcolata
  sulla durata dell'audio, per esempio `circa 1 min 31 s` (non un generico
  «qualche minuto»), e appare sia nel confermare l'operazione sia nello stato
  del lavoro. La trascrizione si può leggere anche a terminale con
  `cat output/<id>/trascrizione.txt`.

Gli id delle riunioni mostrano l'orario leggibile: `AAAAMMGG-HH:MM:SS`
(per esempio `20261121-19:00:27`). Le cartelle create prima di questa
modifica sono state rinominate nello stesso formato.

Entrambe le generazioni funzionano **dopo il riavvio del PC**, anche senza che
sia in corso una riunione, e non richiedono internet (tranne il primo download
di `npx`/Chromium per i diagrammi). È il punto in cui chiudi il pc in sede e
prepari i file a casa. Se spegni il PC mentre un lavoro è in corso, lo stato
del lavoro si perde: rilancia semplicemente il pulsante (i file già salvati
restano intatti).

Api utili:

| Metodo | Endpoint | Azione |
|---|---|---|
| GET | `/health` | controllo rapido che il server risponda |
| GET | `/api/status` | stato corrente + lavori in background |
| GET | `/api/meetings` | elenco riunioni salvate e documenti presenti |
| GET | `/api/jobs` | stato dei lavori di finalizzazione |
| GET | `/api/transcript?meeting_id=…` | testo della trascrizione salvata |
| GET | `/api/document?meeting_id=…&file=…` | apre/scarica un documento generato |
| GET | `/api/devices` | elenco microfoni (per scegliere il dispositivo USB) |
| POST | `/api/start` | inizia cattura + STT live + LLM |
| POST | `/api/stop` | ferma tutto e salva audio + JSON + trascrizione (senza documenti) |
| POST | `/api/finalize` | genera i documenti di `meeting_id` (o dell'ultima riunione) |
| POST | `/api/finalize-accurate` | ritrascrizione Whisper + documenti finali |

Gli ultimi due accettano un corpo JSON opzionale, per esempio:

```bash
curl -X POST -H 'Content-Type: application/json' \
  -d '{"meeting_id":"20261121-17:27:52"}' \
  http://localhost:8000/api/finalize

curl -X POST -H 'Content-Type: application/json' \
  -d '{"meeting_id":"20261121-17:27:52","model":"medium","accurate_render_diagrams":true}' \
  http://localhost:8000/api/finalize-accurate
```

Equivalente da terminale per elencare i microfoni: `.venv/bin/python list_mics.py`.

## Configurazione

Tutto in `config.yaml`. Override rapidi via ambiente:

```bash
export RIUNIONI_LLM_BASE_URL="http://192.168.1.4:8080/v1"
export RIUNIONI_LLM_API_KEY="chiave"
export RIUNIONI_LLM_MODELS="modello-1,modello-2"   # retry in ordine
export RIUNIONI_AUDIO_DEVICE="hw:2,0"              # mic USB
.venv/bin/python server.py
```

In alternativa copia `.env.example` in `.env` e compilalo: le variabili
vengono lette automaticamente all'avvio (precedenza: ambiente > `.env` > yaml).

Se `base_url` o `models` sono vuoti l'LLM resta disattivato: **i sottotitoli
continuano a funzionare**, e a fine riunione i documenti contengono la
trascrizione con un riassunto segnaposto. Il sistema è pensato per funzionare
senza internet in sede.

## Dopo la riunione

`output/<AAAAMMGG-HH:MM:SS>` contiene subito dopo `Ferma e salva`:

- `audio.wav` — grezzo, sempre salvato
- `riunione.json` — verbale strutturato (rigenerabile senza rifare l'IA)
- `trascrizione.txt` — live

Dopo `Genera documenti` / `Ritrascrizione accurata` compaiono anche:

- `trascrizione.json` — segmenti Whisper con timestamp, solo dopo la ritrascrizione
- `trascrizione.txt` — sostituita da quella accurata
- `documenti/verbale.pdf`, `verbale.docx`, `verbale.md`, `mappa-mentale.*`, `flusso.*`

Se riavvii il PC, il selettore nella barra «Riunione salvata» ritrova tutte le
riunioni con `audio.wav`: non serve tenere aperta la riunione live.

Per la qualità alta, anche da riga di comando:

```bash
.venv/bin/python stt_final.py --meeting output/20261121-163000 --model medium
```

I diagrammi Mermaid vengono renderizzati in SVG/PNG tramite `npx` (serve
internet la prima volta); senza `npx` restano i `.mmd`, apribili in qualsiasi
editor Mermaid. I file finiti vengono presi e inviati manualmente via WhatsApp
da una persona: nessuna automazione di invio o pagina pubblica.

## Test e benchmark

```bash
.venv/bin/python tests/benchmark_stt.py tests/test_5min_16k.wav
```

Il campione è italiano sintetico generato con `espeak-ng`: serve a confrontare
i modelli, **non** a sostituire una prova con audio di microfono reale.

Risultati su 330 s di audio (i3-4030U, 4 thread, int8):

| Modello | Tempo | Fattore | Uso |
|---|---|---|---|
| tiny beam5 | 85,8 s | 0,26x | confronto |
| base beam5 | 90,9 s | 0,28x | confronto |
| base beam1 | 64,7 s | 0,20x | confronto |
| small beam5 | 220,2 s | 0,67x | confronto |

### Sottotitoli live: perché Vosk

Quei numeri sembrano compatibili con il live, ma su chunk da 3 s il costo di
ogni chiamata a Whisper è alto e irregular: 2,8-5 s per 3 s di audio, con il
lag che cresceva da 14,6 s a 22,2 s in 45 s di prova. Su questa CPU (i3-4030U,
spesso caricata da WhatsApp/plasma-discover) faster-whisper a chunk **non sta
in sincronizzazione**.

Il motore live predefinito è quindi **Vosk streaming**, che emette partial in
~0,3 s e frasi confermate poco dopo la fine della frase. Con il modello small e
microfono interno: 15 frasi in 120 s, lag 2,0-3,2 s, zero chunk scartati.

Confronto sui tuoi 120 s di voce reale (offline, stesso audio):

| Modello | Carico | Elaborazione | Esempi |
|---|---|---|---|
| Vosk small | 1,1 s | 11,8 s (0,10x) | «più accurata», «parlare violento», frase ripetuta 1 volta |
| Vosk grande | 8,4 s | 41,9 s (0,35x) | «più accurato», «più lento io» ripetuta 2 volte |
| Whisper medium | 7,6 s | ~118 s (1,0x) | testo migliore in assoluto |

Il modello grande resta comoda in tempo reale (nessun accumulo, lag ~0 s
nell'iniezione a tempo reale) ed è quello impostato come default. Ha costi di
RAM e avvio superiori. Qualunque sia il modello live, il documento finale usa
sempre la ritrascrizione Whisper `medium`, non i sottotitoli live.

Nota: il lag mostrato in `/display` usa un orologio monotonico, quindi non salta
se il PC va in sospensione o l'orologio di sistema viene corretto.

### Trascrizione accurata (a riunione finita)

Misurata su 60 s di audio, modello già in cache:

| Modello | Caricamento | Trascrizione | Per 1 h di riunione |
|---|---|---|---|
| `small` | ~pochi s | 0,67x | ~40 min |
| `medium` (default) | 7,6 s | 1,78x | ~1 h 47 min |

Il primo download di `medium` è ~1,5 GB (nella nostra rete ha richiesto ~24 min),
poi resta in `~/.cache/huggingface`. Se il turnaround conta, usa
`--model small`; se vuoi la massima qualità puoi lanciare `medium` e lasciarlo
lavorare, oppure eseguirlo sul server di casa.

La stima mostrata prima di avviare la ritrascrizione è
`durata_audio × accurate_speed_factor + accurate_overhead_seconds`, con i due
parametri in `config.yaml` sotto `finalization` (valori predefiniti `1.3` e
`10`). Essendo un rate medio, va presa come indicazione: il carico del PC e la
dimensione del modello possono farla variare. `/api/meetings` restituisce per
ogni riunione `duration_seconds`, `transcript_chars` e
`accurate_estimate_seconds`.

Il rendering Mermaid in SVG/PNG tramite `npx` è stato verificato: la prima
volta scarica Chromium, poi riusa la cache.

## Privacy

In Italia è lecito registrare una conversazione a cui si partecipa, ma
diffonderla richiede il consenso dei partecipanti: avvisa sempre all'inizio
della riunione che stai registrando e che il verbale verrà condiviso. Poiché
l'invio è manuale, il controllo finale resta in capo a chi invia il file.

## Stato dei task

- [x] Test CPU/AVX2 e benchmark modelli
- [x] Cattura audio + WAV grezzo
- [x] STT live streaming via Vosk + WebSocket
- [x] Server FastAPI + pagina `/display`
- [x] Finalizzazione differita (documenti generabili dopo un riavvio, anche offline)
- [x] LLM loop opzionale con retry e degradazione elegante
- [x] Display con mappa, decisioni e azioni
- [x] Finalizzazione PDF/DOCX/Markdown/Mermaid
- [x] Ritrascrizione accurata
- [x] Visualizzatore documenti cliccabile + stima tempo ritrascrizione
- [ ] Configurazione mic USB fisico e prova in sala con microfono reale
- [ ] Prova end-to-end con riunione vera
