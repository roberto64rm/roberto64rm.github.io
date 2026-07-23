# ============================================================
# MAIN.PY - MULTIPLAYER FOTO - RASPBERRY PI
# ============================================================
#
# Progetto:
# MultiPlayer_FOTO
#
# Hardware principale:
# - Raspberry Pi Zero 2 W
# - ESP32 collegato in seriale al Raspberry
# - Lettore RFID gestito da ESP32
# - Display touch gestito da ESP32
# - TV collegata al Raspberry tramite HDMI
# - Disco SSD esterno con cartella ALBUM_FOTO
#
# Scopo del programma:
# Questo script gira sul Raspberry e gestisce:
#
# 1. ricezione UID RFID da ESP32 tramite seriale
# 2. ricerca UID nel file rfid_map.json
# 3. apertura della cartella album sul disco SSD esterno
# 4. lettura config.txt
# 5. visualizzazione cover e informazioni su HDMI
# 6. slideshow foto/GIF/video
# 7. musica MP3 di sottofondo durante foto/GIF
# 8. stop automatico quando la scheda RFID viene rimossa
# 9. comandi touch da ESP32: STOP, PAUSE, MUTE
#
# ------------------------------------------------------------
# NUOVA STRUTTURA SSD
# ------------------------------------------------------------
#
# Gli album NON sono più nella microSD del Raspberry.
# Sono sul disco SSD esterno, nella cartella:
#
#   /media/maker64/SSD480p300/ALBUM_FOTO
#
# Dentro ALBUM_FOTO devono esserci:
#
#   rfid_map.json
#   Nome Album 1/config.txt
#   Nome Album 1/cover.jpg
#   Nome Album 1/foto...
#   Nome Album 1/musica_001.mp3
#
# ------------------------------------------------------------
# NUOVO STANDARD UID RFID
# ------------------------------------------------------------
#
# Le nuove schede hanno UID a 7 byte.
# Esempio letto dal lettore Arduino Nano + RC522:
#
#   UID con due punti : 04:10:78:3D:9E:61:80
#   UID per JSON      : 0410783D9E6180
#
# Nel file rfid_map.json usiamo sempre l'UID pulito:
#
#   0410783D9E6180
#
# Il Raspberry tratta l'UID come stringa, senza imporre una
# lunghezza fissa nel codice principale. In questo modo può
# funzionare sia con UID nuovi da 14 caratteri sia, se necessario,
# con vecchi UID da 8 caratteri.
#
# ------------------------------------------------------------
# NOTE IMPORTANTI DI MANUTENZIONE
# ------------------------------------------------------------
#
# Questo file segue lo standard documentale del progetto:
# - ogni funzione è preceduta da un banner
# - ogni funzione spiega scopo e comportamento
# - il codice è scritto in modo semplice e leggibile
# - evitare trucchi compatti difficili da capire tra un anno
#
# ============================================================


import json
import os
import signal
import subprocess
import textwrap
import time
from pathlib import Path

import serial
from PIL import Image, ImageDraw, ImageFont, ImageOps


# ============================================================
# CONFIGURAZIONE GENERALE
# ============================================================
#
# In questa sezione ci sono tutti i parametri principali.
# Se cambiano disco, cartella, seriale o display, si corregge qui.
# ============================================================

# Seriale Raspberry <-> ESP32.
PORTA_SERIALE = "/dev/serial0"
BAUD = 115200

# Display grafico usato da feh/mpv sul desktop Raspberry.
DISPLAY_HDMI = ":0"

# Cartella interna su microSD usata SOLO per file temporanei PNG
# generati dal Raspberry: schermate errore, stop, intro, ecc.
BASE_DIR = Path.home() / "MultiPlayer_FOTO"

# Nuova cartella principale degli album sul disco SSD esterno.
ALBUM_ROOT = Path("/media/maker64/SSD480p300/ALBUM_FOTO")

# File JSON definitivo generato dal tool crea_rfid_map.py.
MAP_FILE = ALBUM_ROOT / "rfid_map.json"

# Estensioni riconosciute dal player.
ESTENSIONI_FOTO = [".jpg", ".jpeg", ".png", ".bmp", ".webp"]
ESTENSIONI_GIF = [".gif"]
ESTENSIONI_VIDEO = [".mp4", ".mov", ".mkv", ".avi", ".webm"]
ESTENSIONI_MP3 = [".mp3"]
ESTENSIONI_MEDIA = ESTENSIONI_FOTO + ESTENSIONI_GIF + ESTENSIONI_VIDEO + ESTENSIONI_MP3

# Audio HDMI del Raspberry.
# Verificato con speaker-test/mpv sul sistema attuale.
AUDIO_DEVICE_HDMI = "alsa/plughw:0,0"

# Durata schermata intro evento prima dello slideshow.
DURATA_INTRO_EVENTO = 15


# ============================================================
# VARIABILI GLOBALI DI STATO
# ============================================================
#
# Queste variabili tengono traccia dei processi esterni e dello
# stato dei comandi touch.
# ============================================================

processo_feh = None
processo_musica = None
ultimo_evento = ""
uid_attivo = ""
mute_attivo = False
pausa_attiva = False
mp3_correnti = []


# ============================================================
# FUNZIONE: normalizza_uid(uid)
# ============================================================
#
# Scopo:
# Riceve un UID proveniente dall'ESP32 e lo porta al formato usato
# nel file rfid_map.json.
#
# Accetta formati tipo:
#
#   04:10:78:3D:9E:61:80
#   04 10 78 3D 9E 61 80
#   04-10-78-3D-9E-61-80
#   0410783d9e6180
#
# Restituisce:
#
#   0410783D9E6180
#
# Nota:
# Non controlliamo qui la lunghezza. Il programma cerca la stringa
# nel JSON. Questo rende il codice compatibile anche con eventuali
# UID da 4 byte o 7 byte.
# ============================================================

def normalizza_uid(uid):
    uid = uid.strip().upper()
    uid = uid.replace(" ", "")
    uid = uid.replace(":", "")
    uid = uid.replace("-", "")
    uid = uid.replace(".", "")
    return uid


# ============================================================
# FUNZIONE: estrai_uid_da_tag_removed(riga)
# ============================================================
#
# Scopo:
# Estrae l'eventuale UID contenuto in un messaggio di rimozione tag.
#
# Formati accettati:
#
#   TAG_REMOVED
#   TAG_REMOVED:0410783D9E6180
#
# Output:
# - stringa UID normalizzata se presente
# - stringa vuota se il messaggio non contiene UID.
#
# Nota di manutenzione:
# Questa funzione serve a evitare che un vecchio TAG_REMOVED arrivato
# in ritardo fermi un album diverso da quello attivo.
# ============================================================

def estrai_uid_da_tag_removed(riga):
    if not riga.startswith("TAG_REMOVED:"):
        return ""

    uid = riga.split(":", 1)[1].strip()
    return normalizza_uid(uid)


# ============================================================
# FUNZIONE: tag_removed_valido(uid_rimosso)
# ============================================================
#
# Scopo:
# Decide se un messaggio TAG_REMOVED deve davvero fermare il player.
#
# Input:
# uid_rimosso = UID estratto da TAG_REMOVED:<UID>.
#
# Output:
# - True  se la rimozione riguarda la scheda attiva.
# - False se il messaggio e' vecchio o non coerente.
#
# Nota:
# Se arriva il vecchio formato TAG_REMOVED senza UID, lo accettiamo
# per compatibilita'.
# ============================================================

def tag_removed_valido(uid_rimosso):
    if uid_rimosso == "":
        return True

    if uid_attivo == "":
        print("TAG_REMOVED ignorato: nessun UID attivo. UID ricevuto:", uid_rimosso)
        return False

    if uid_rimosso != uid_attivo:
        print("TAG_REMOVED ignorato: UID rimosso diverso da UID attivo")
        print("UID rimosso:", uid_rimosso)
        print("UID attivo :", uid_attivo)
        return False

    return True


# ============================================================
# FUNZIONE: carica_mappa()
# ============================================================
#
# Scopo:
# Carica il file rfid_map.json dal disco SSD esterno.
#
# Input:
# Nessuno. Usa la costante globale MAP_FILE.
#
# Output:
# Dizionario Python con struttura:
#
#   {
#     "0410783D9E6180": "Nome Album",
#     "00000000000001": "Album non ancora codificato"
#   }
#
# Note:
# Se il file non esiste o non è leggibile, il programma deve fermarsi
# con errore chiaro, perché senza mappa RFID non può funzionare.
# ============================================================

def carica_mappa():
    print("Carico mappa RFID:", MAP_FILE)

    with open(MAP_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================
# FUNZIONE: leggi_config(percorso_evento)
# ============================================================
#
# Scopo:
# Legge il file config.txt presente nella cartella album.
#
# Formato atteso:
#
#   titolo=Nome visualizzato
#   modo=random
#   musica=on
#   tempo_foto=7
#   descrizione=Testo descrittivo
#
# Input:
# percorso_evento = Path della cartella album.
#
# Output:
# - dizionario config se il file esiste
# - None se config.txt manca
#
# Note:
# Le righe vuote e le righe che iniziano con # vengono ignorate.
# ============================================================

def leggi_config(percorso_evento):
    config = {}
    file_config = percorso_evento / "config.txt"

    if not file_config.exists():
        return None

    with open(file_config, "r", encoding="utf-8") as f:
        for riga in f:
            riga = riga.strip()

            if not riga:
                continue

            if riga.startswith("#"):
                continue

            if "=" in riga:
                chiave, valore = riga.split("=", 1)
                config[chiave.strip()] = valore.strip()

    return config


# ============================================================
# FUNZIONE: trova_media(percorso_evento)
# ============================================================
#
# Scopo:
# Cerca dentro la cartella album tutti i file gestiti dal player:
# foto, GIF, video e MP3.
#
# Input:
# percorso_evento = Path della cartella album.
#
# Output:
# Lista ordinata di Path.
#
# Note:
# - cover.jpg viene esclusa dallo slideshow.
# - gli MP3 vengono inclusi nella lista perché poi avvia_slideshow()
#   li separa e li usa come base musicale.
# ============================================================

def trova_media(percorso_evento):
    media = []

    for file in percorso_evento.iterdir():
        if not file.is_file():
            continue

        if file.name.lower() == "cover.jpg":
            continue

        if file.suffix.lower() in ESTENSIONI_MEDIA:
            media.append(file)

    media.sort()
    return media


# ============================================================
# FUNZIONE: invia(ser, msg)
# ============================================================
#
# Scopo:
# Invia un messaggio testuale all'ESP32 sulla seriale.
#
# Input:
# ser = oggetto serial.Serial già aperto.
# msg = stringa da inviare senza newline finale.
#
# Output:
# Nessuno.
#
# Note:
# Aggiunge automaticamente \n.
# Stampa anche a console per debug.
# ============================================================

def invia(ser, msg):
    ser.write((msg + "\n").encode("utf-8"))
    print("Raspberry -> ESP32:", msg)
    time.sleep(0.05)


# ============================================================
# FUNZIONE: ambiente_grafico()
# ============================================================
#
# Scopo:
# Prepara le variabili d'ambiente per lanciare feh e mpv sul
# display HDMI del Raspberry.
#
# Output:
# Dizionario env da passare a subprocess.Popen().
# ============================================================

def ambiente_grafico():
    env = os.environ.copy()
    env["DISPLAY"] = DISPLAY_HDMI
    env["HOME"] = str(Path.home())
    return env


# ============================================================
# FUNZIONE: uccidi_processo_gruppo(processo, nome)
# ============================================================
#
# Scopo:
# Chiude in modo robusto un processo esterno avviato con os.setsid.
#
# Perché serve:
# feh e mpv possono creare processi figli. Chiudere solo il processo
# principale non sempre basta. Per questo chiudiamo tutto il gruppo.
#
# Input:
# processo = oggetto subprocess.Popen oppure None.
# nome = testo usato solo nei messaggi di debug.
#
# Output:
# Nessuno.
# ============================================================

def uccidi_processo_gruppo(processo, nome):
    if processo is None:
        return

    try:
        os.killpg(os.getpgid(processo.pid), signal.SIGTERM)
        time.sleep(0.4)

        if processo.poll() is None:
            os.killpg(os.getpgid(processo.pid), signal.SIGKILL)
            time.sleep(0.2)

    except Exception as e:
        print("Errore chiusura", nome + ":", e)


# ============================================================
# FUNZIONE: metti_in_pausa_processi()
# ============================================================
#
# Scopo:
# Mette in pausa i processi video/foto e musica usando SIGSTOP.
#
# Note:
# La pausa delle foto richiede anche che il ciclo Python non continui
# a contare il tempo. Questo è gestito in mostra_blocco_foto_con_feh().
# ============================================================

def metti_in_pausa_processi():
    print("Metto in pausa processi media")

    for processo, nome in [(processo_feh, "media"), (processo_musica, "musica")]:
        if processo is not None:
            try:
                os.killpg(os.getpgid(processo.pid), signal.SIGSTOP)
            except Exception as e:
                print("Errore pausa", nome + ":", e)


# ============================================================
# FUNZIONE: riprendi_processi()
# ============================================================
#
# Scopo:
# Riprende i processi messi in pausa usando SIGCONT.
# ============================================================

def riprendi_processi():
    print("Riprendo processi media")

    for processo, nome in [(processo_feh, "media"), (processo_musica, "musica")]:
        if processo is not None:
            try:
                os.killpg(os.getpgid(processo.pid), signal.SIGCONT)
            except Exception as e:
                print("Errore ripresa", nome + ":", e)


# ============================================================
# FUNZIONE: ferma_musica()
# ============================================================
#
# Scopo:
# Ferma la base musicale MP3 se è attiva.
# ============================================================

def ferma_musica():
    global processo_musica

    uccidi_processo_gruppo(processo_musica, "musica")
    processo_musica = None


# ============================================================
# FUNZIONE: avvia_musica(lista_mp3)
# ============================================================
#
# Scopo:
# Avvia mpv in modalità audio per riprodurre gli MP3 dell'album
# come base musicale di sottofondo.
#
# Input:
# lista_mp3 = lista di Path dei file MP3.
#
# Note:
# - Se mute_attivo è True, non parte nulla.
# - La playlist viene ripetuta all'infinito.
# - mpv sceglie ordine casuale con --shuffle.
# ============================================================

def avvia_musica(lista_mp3):
    global processo_musica

    if mute_attivo:
        print("MUTE attivo: non avvio base musicale")
        return

    ferma_musica()

    if not lista_mp3:
        print("Nessun MP3 per base musicale")
        return

    comando = [
        "mpv",
        "--no-video",
        "--loop-playlist=inf",
        "--shuffle",
        "--really-quiet",
        "--audio-device=" + AUDIO_DEVICE_HDMI,
    ]

    for mp3 in lista_mp3:
        comando.append(str(mp3))

    print("Avvio base musicale MP3:")
    print(" ".join(comando))

    processo_musica = subprocess.Popen(
        comando,
        env=ambiente_grafico(),
        preexec_fn=os.setsid,
    )


# ============================================================
# FUNZIONE: chiudi_media()
# ============================================================
#
# Scopo:
# Chiude il processo attualmente usato per foto/GIF/video.
# Storicamente si chiamava processo_feh, ma oggi può essere feh o mpv.
# ============================================================

def chiudi_media():
    global processo_feh

    uccidi_processo_gruppo(processo_feh, "media")
    processo_feh = None


# ============================================================
# FUNZIONE: ferma_visualizzazione()
# ============================================================
#
# Scopo:
# Ferma tutto: media visivo e musica.
# Viene usata quando si rimuove il tag o si preme STOP.
# ============================================================

def ferma_visualizzazione():
    chiudi_media()
    ferma_musica()


# ============================================================
# FUNZIONE: carica_font(percorso, dimensione)
# ============================================================
#
# Scopo:
# Carica un font TrueType. Se non riesce, usa il font di default.
# Serve per creare le schermate HDMI con Pillow.
# ============================================================

def carica_font(percorso, dimensione):
    try:
        return ImageFont.truetype(percorso, dimensione)
    except Exception:
        return ImageFont.load_default()


# ============================================================
# FUNZIONE: mostra_png_con_feh(file_png)
# ============================================================
#
# Scopo:
# Mostra una singola immagine PNG/JPG su HDMI usando feh.
# Usata per intro, errori, stop e scheda rimossa.
# ============================================================

def mostra_png_con_feh(file_png):
    global processo_feh

    chiudi_media()

    processo_feh = subprocess.Popen(
        [
            "feh",
            "-F",
            "-Y",
            "-x",
            "-q",
            "--auto-zoom",
            str(file_png),
        ],
        env=ambiente_grafico(),
        preexec_fn=os.setsid,
    )


# ============================================================
# FUNZIONE: disegna_testo_centrato(draw, testo, y, font, fill)
# ============================================================
#
# Scopo:
# Disegna una riga di testo centrata orizzontalmente su immagine 1920x1080.
# ============================================================

def disegna_testo_centrato(draw, testo, y, font, fill=(255, 255, 255)):
    larghezza = 1920
    bbox = draw.textbbox((0, 0), testo, font=font)
    larghezza_testo = bbox[2] - bbox[0]
    x = (larghezza - larghezza_testo) // 2
    draw.text((x, y), testo, font=font, fill=fill)


# ============================================================
# FUNZIONE: mostra_intro_evento_hdmi(...)
# ============================================================
#
# Scopo:
# Crea e mostra su HDMI la schermata iniziale dell'album:
# cover sullo sfondo, pannello scuro, titolo, descrizione e numero contenuti.
# ============================================================

def mostra_intro_evento_hdmi(percorso_cover, titolo, descrizione, numero_contenuti):
    larghezza = 1920
    altezza = 1080

    base = Image.new("RGB", (larghezza, altezza), color=(0, 0, 0))

    sorgente = Image.open(percorso_cover).convert("RGB")
    sorgente = ImageOps.contain(sorgente, (larghezza, altezza))

    x_img = (larghezza - sorgente.width) // 2
    y_img = (altezza - sorgente.height) // 2
    base.paste(sorgente, (x_img, y_img))

    overlay = Image.new("RGBA", (larghezza, altezza), (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)
    draw_overlay.rounded_rectangle((120, 140, 1800, 940), radius=35, fill=(0, 0, 0, 155))

    base = Image.alpha_composite(base.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(base)

    font_titolo = carica_font("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 88)
    font_desc = carica_font("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 46)
    font_info = carica_font("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 54)

    if descrizione is None:
        descrizione = ""

    righe_desc = textwrap.wrap(descrizione, width=42)

    y = 230
    disegna_testo_centrato(draw, titolo, y, font_titolo)
    y += 150

    for riga in righe_desc[:4]:
        disegna_testo_centrato(draw, riga, y, font_desc)
        y += 68

    y += 45
    disegna_testo_centrato(draw, f"Contenuti: {numero_contenuti}", y, font_info)

    file_intro = BASE_DIR / "intro_evento_hdmi.png"
    base.convert("RGB").save(file_intro)

    print("Mostro intro evento su HDMI:", file_intro)
    mostra_png_con_feh(file_intro)


# ============================================================
# FUNZIONE: crea_schermata_messaggio(nome_file, righe)
# ============================================================
#
# Scopo:
# Crea una schermata HDMI nera con righe di testo centrate.
# Usata per errore, STOP e TAG rimosso.
#
# Input:
# nome_file = nome PNG da salvare dentro BASE_DIR.
# righe = lista di tuple (testo, tipo), dove tipo può essere:
#        titolo, grande, testo, vuoto
#
# Output:
# Path del file PNG creato.
# ============================================================

def crea_schermata_messaggio(nome_file, righe):
    larghezza = 1920
    altezza = 1080

    img = Image.new("RGB", (larghezza, altezza), color=(0, 0, 0))
    draw = ImageDraw.Draw(img)

    font_titolo = carica_font("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 90)
    font_grande = carica_font("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 78)
    font_testo = carica_font("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 52)

    y = 150

    for testo, tipo in righe:
        if tipo == "titolo":
            font = font_titolo
            passo = 110
        elif tipo == "grande":
            font = font_grande
            passo = 90
        elif tipo == "vuoto":
            font = font_testo
            passo = 55
        else:
            font = font_testo
            passo = 75

        if testo != "":
            disegna_testo_centrato(draw, testo, y, font)

        y += passo

    file_msg = BASE_DIR / nome_file
    img.save(file_msg)
    return file_msg


# ============================================================
# FUNZIONE: mostra_pronto_hdmi()
# ============================================================
#
# Scopo:
# Mostra sulla TV la schermata di sistema pronto.
#
# Quando viene chiamata:
# - dopo il caricamento di rfid_map.json
# - dopo l'apertura della seriale
# - prima di inviare RPI_READY all'ESP32
#
# Output:
# - schermata HDMI con messaggio per l'utente.
# ============================================================

def mostra_pronto_hdmi():
    righe = [
        ("MULTI PLAYER FOTO", "titolo"),
        ("", "vuoto"),
        ("SISTEMA PRONTO", "grande"),
        ("", "vuoto"),
        ("Ora puoi inserire", "testo"),
        ("la scheda RFID", "testo"),
        ("", "vuoto"),
        ("Scegli un viaggio o un evento", "testo"),
    ]

    file_msg = crea_schermata_messaggio("sistema_pronto_hdmi.png", righe)
    print("Mostro schermata sistema pronto su HDMI:", file_msg)
    mostra_png_con_feh(file_msg)


# ============================================================
# FUNZIONE: mostra_errore_hdmi(nome_cartella, motivo)
# ============================================================
#
# Scopo:
# Mostra un errore su HDMI quando l'evento non può partire.
# ============================================================

def mostra_errore_hdmi(nome_cartella, motivo):
    righe = [
        (nome_cartella, "titolo"),
        ("", "vuoto"),
        ("CARTELLA INCOMPLETA", "grande"),
        ("", "vuoto"),
        ("Motivo:", "testo"),
        (motivo, "testo"),
        ("", "vuoto"),
        ("Cambiare scheda TAG", "testo"),
    ]

    file_msg = crea_schermata_messaggio("errore_hdmi.png", righe)
    print("Mostro errore su HDMI:", file_msg)
    mostra_png_con_feh(file_msg)


# ============================================================
# FUNZIONE: mostra_stop_hdmi(nome_evento)
# ============================================================
#
# Scopo:
# Mostra la schermata di STOP manuale.
# ============================================================

def mostra_stop_hdmi(nome_evento):
    righe = [
        (nome_evento, "titolo"),
        ("", "vuoto"),
        ("PRESENTAZIONE", "grande"),
        ("FERMATA", "grande"),
        ("", "vuoto"),
        ("Stop manuale da display touch", "testo"),
        ("", "vuoto"),
        ("Inserire una nuova scheda", "testo"),
    ]

    file_msg = crea_schermata_messaggio("stop_hdmi.png", righe)
    print("Mostro schermata STOP su HDMI:", file_msg)
    mostra_png_con_feh(file_msg)


# ============================================================
# FUNZIONE: mostra_tag_rimosso_hdmi(nome_evento)
# ============================================================
#
# Scopo:
# Mostra la schermata quando il tag RFID viene rimosso.
# ============================================================

def mostra_tag_rimosso_hdmi(nome_evento):
    righe = [
        (nome_evento, "titolo"),
        ("", "vuoto"),
        ("SCHEDA RIMOSSA", "grande"),
        ("", "vuoto"),
        ("Inserire una nuova scheda", "testo"),
    ]

    file_msg = crea_schermata_messaggio("tag_removed_hdmi.png", righe)
    print("Mostro schermata tag rimosso su HDMI:", file_msg)
    mostra_png_con_feh(file_msg)


# ============================================================
# FUNZIONE: gestisci_comando_durante_media(ser, riga)
# ============================================================
#
# Scopo:
# Gestisce i comandi ricevuti da ESP32 mentre foto/video/musica
# sono in riproduzione.
#
# Output:
# - True  se il media corrente deve interrompersi
# - False se si può continuare
# ============================================================

def gestisci_comando_durante_media(ser, riga):
    global pausa_attiva
    global mute_attivo
    global uid_attivo

    if riga == "TAG_REMOVED" or riga.startswith("TAG_REMOVED:"):
        uid_rimosso = estrai_uid_da_tag_removed(riga)

        if not tag_removed_valido(uid_rimosso):
            return False

        print("Tag rimosso durante slideshow")
        ferma_visualizzazione()

        if ultimo_evento != "":
            mostra_tag_rimosso_hdmi(ultimo_evento)
        else:
            mostra_tag_rimosso_hdmi("Evento")

        uid_attivo = ""
        invia(ser, "STATUS:WAIT_TAG")
        return True

    if riga == "CMD:STOP":
        print("STOP touch durante slideshow")
        ferma_visualizzazione()

        if ultimo_evento != "":
            mostra_stop_hdmi(ultimo_evento)
        else:
            mostra_stop_hdmi("Evento")

        uid_attivo = ""
        invia(ser, "STATUS:WAIT_TAG")
        return True

    if riga == "CMD:PAUSE":
        pausa_attiva = not pausa_attiva

        if pausa_attiva:
            print("PAUSA ON durante slideshow")
            metti_in_pausa_processi()
            invia(ser, "STATUS:PAUSED")
        else:
            print("PAUSA OFF durante slideshow")
            riprendi_processi()
            invia(ser, "STATUS:PLAYING")

        return False

    if riga == "CMD:MUTE":
        mute_attivo = not mute_attivo

        if mute_attivo:
            print("MUTE ON durante slideshow")
            ferma_musica()
            invia(ser, "STATUS:MUTED")
        else:
            print("MUTE OFF durante slideshow")
            invia(ser, "STATUS:UNMUTED")
            avvia_musica(mp3_correnti)

        return False

    if riga.startswith("ERR:"):
        print("Errore ricevuto da ESP32:", riga)
        return False

    if riga.startswith("OK:"):
        print("Conferma ricevuta da ESP32:", riga)
        return False

    print("Messaggio ignorato durante slideshow:", riga)
    return False


# ============================================================
# FUNZIONE: controlla_seriale_durante_media(ser)
# ============================================================
#
# Scopo:
# Controlla se ESP32 ha inviato comandi durante lo slideshow.
#
# Output:
# - True se bisogna interrompere il media/slideshow
# - False se si può continuare
# ============================================================

def controlla_seriale_durante_media(ser):
    riga = ser.readline().decode("utf-8", errors="ignore").strip()

    if not riga:
        return False

    print("ESP32 -> Raspberry:", riga)
    return gestisci_comando_durante_media(ser, riga)


# ============================================================
# FUNZIONE: mostra_blocco_foto_con_feh(...)
# ============================================================
#
# Scopo:
# Mostra tutte le foto dell'album con feh.
#
# Note:
# - feh gestisce il cambio immagine automatico.
# - Python tiene un timer totale per sapere quando passare a GIF/video.
# - Se pausa_attiva è True, il timer NON avanza.
# ============================================================

def mostra_blocco_foto_con_feh(ser, lista_foto, modo, tempo_foto):
    global processo_feh

    if not lista_foto:
        return False

    chiudi_media()

    comando = [
        "feh",
        "-F",
        "-Y",
        "-x",
        "-q",
        "--auto-zoom",
        "--slideshow-delay",
        str(tempo_foto),
    ]

    if modo.lower() == "random":
        comando.append("-z")

    for img in lista_foto:
        comando.append(str(img))

    print("Avvio blocco foto con feh:")
    print(" ".join(comando))

    processo_feh = subprocess.Popen(
        comando,
        env=ambiente_grafico(),
        preexec_fn=os.setsid,
    )

    durata_blocco = len(lista_foto) * tempo_foto
    tempo_passato = 0
    ultimo_tick = time.time()

    while tempo_passato < durata_blocco:
        if controlla_seriale_durante_media(ser):
            return True

        adesso = time.time()

        if pausa_attiva:
            ultimo_tick = adesso
            time.sleep(0.2)
            continue

        tempo_passato += adesso - ultimo_tick
        ultimo_tick = adesso
        time.sleep(0.2)

    chiudi_media()
    return False


# ============================================================
# FUNZIONE: mostra_video_o_gif(ser, file_media, durata)
# ============================================================
#
# Scopo:
# Riproduce un video o una GIF usando mpv.
#
# Input:
# durata = 0 significa riproduci fino alla fine.
# durata > 0 significa limita la riproduzione a quel numero di secondi.
#
# Note:
# L'audio dei video esce da HDMI usando AUDIO_DEVICE_HDMI.
# ============================================================

def mostra_video_o_gif(ser, file_media, durata):
    global processo_feh

    chiudi_media()

    comando = [
        "mpv",
        "--fs",
        "--osc=no",
        "--no-osd-bar",
        "--osd-level=0",
        "--really-quiet",
        "--no-input-default-bindings",
        "--audio-device=" + AUDIO_DEVICE_HDMI,
    ]

    if durata > 0:
        comando.append("--length=" + str(durata))

    comando.append(str(file_media))

    print("Riproduco media:")
    print(" ".join(comando))

    processo_feh = subprocess.Popen(
        comando,
        env=ambiente_grafico(),
        preexec_fn=os.setsid,
    )

    while processo_feh.poll() is None:
        if controlla_seriale_durante_media(ser):
            return True

        time.sleep(0.2)

    processo_feh = None
    return False


# ============================================================
# FUNZIONE: dividi_media_per_tipo(lista_file)
# ============================================================
#
# Scopo:
# Divide la lista media dell'album in quattro liste:
# foto, gif, video, mp3.
# ============================================================

def dividi_media_per_tipo(lista_file):
    foto = []
    gif = []
    video = []
    mp3 = []

    for file in lista_file:
        estensione = file.suffix.lower()

        if estensione in ESTENSIONI_FOTO:
            foto.append(file)
        elif estensione in ESTENSIONI_GIF:
            gif.append(file)
        elif estensione in ESTENSIONI_VIDEO:
            video.append(file)
        elif estensione in ESTENSIONI_MP3:
            mp3.append(file)
        else:
            print("File ignorato:", file.name)

    return foto, gif, video, mp3


# ============================================================
# FUNZIONE: mescola_se_random(modo, *liste)
# ============================================================
#
# Scopo:
# Se modo=random, mescola le liste passate.
#
# Note:
# Importiamo random qui per mantenere le dipendenze semplici.
# ============================================================

def mescola_se_random(modo, *liste):
    if modo.lower() != "random":
        return

    import random

    for lista in liste:
        random.shuffle(lista)


# ============================================================
# FUNZIONE: avvia_slideshow(ser, lista_file, modo, tempo_foto, musica)
# ============================================================
#
# Scopo:
# Gestisce il ciclo multimediale completo dell'album.
#
# Sequenza:
# 1. separa foto/GIF/video/MP3
# 2. avvia musica se richiesta
# 3. mostra foto
# 4. mostra GIF
# 5. mostra video, fermando la musica di sottofondo
# 6. ripete il ciclo finché il tag resta appoggiato
# ============================================================

def avvia_slideshow(ser, lista_file, modo, tempo_foto, musica):
    global mp3_correnti

    chiudi_media()

    if not lista_file:
        print("Nessun file da visualizzare")
        return

    foto, gif, video, mp3 = dividi_media_per_tipo(lista_file)
    mp3_correnti = mp3

    mescola_se_random(modo, foto, gif, video, mp3)

    if musica.lower() == "on":
        avvia_musica(mp3)
    else:
        ferma_musica()

    while True:
        if foto:
            if mostra_blocco_foto_con_feh(ser, foto, modo, tempo_foto):
                return

        for file_gif in gif:
            if controlla_seriale_durante_media(ser):
                return

            if mostra_video_o_gif(ser, file_gif, 10):
                return

        for file_video in video:
            if controlla_seriale_durante_media(ser):
                ferma_musica()
                return

            ferma_musica()

            if mostra_video_o_gif(ser, file_video, 0):
                ferma_musica()
                return

            if musica.lower() == "on":
                avvia_musica(mp3)

        if controlla_seriale_durante_media(ser):
            return


# ============================================================
# FUNZIONE: attesa_intro_interrompibile(ser, durata)
# ============================================================
#
# Scopo:
# Attende durante la schermata intro, ma controlla comunque se la
# scheda viene rimossa o se l'utente preme STOP.
# ============================================================

def attesa_intro_interrompibile(ser, durata):
    inizio = time.time()

    while time.time() - inizio < durata:
        if controlla_seriale_durante_media(ser):
            return True

        time.sleep(0.2)

    return False


# ============================================================
# FUNZIONE: gestisci_uid(ser, uid, mappa_rfid)
# ============================================================
#
# Scopo:
# Gestisce un UID ricevuto dall'ESP32.
#
# Passi:
# 1. normalizza UID
# 2. cerca UID in rfid_map.json
# 3. apre la cartella album su SSD
# 4. legge config.txt
# 5. controlla cover.jpg e contenuti
# 6. invia informazioni a ESP32
# 7. mostra intro HDMI
# 8. avvia slideshow
# ============================================================

def gestisci_uid(ser, uid, mappa_rfid):
    global ultimo_evento
    global pausa_attiva
    global uid_attivo

    uid = normalizza_uid(uid)

    print("--------------------------------")
    print("UID ricevuto normalizzato:", uid)

    pausa_attiva = False

    if uid not in mappa_rfid:
        print("UID non registrato:", uid)
        invia(ser, "ERR:UID_UNKNOWN")
        mostra_errore_hdmi("UID SCONOSCIUTO", uid)
        return

    nome_cartella = mappa_rfid[uid]
    uid_attivo = uid
    ultimo_evento = nome_cartella
    percorso_evento = ALBUM_ROOT / nome_cartella

    if not percorso_evento.exists():
        print("Cartella evento mancante:", percorso_evento)
        invia(ser, "ERR:FOLDER_NOT_FOUND")
        mostra_errore_hdmi(nome_cartella, "cartella evento mancante")
        return

    config = leggi_config(percorso_evento)

    if config is None:
        print("Evento incompleto: config.txt mancante")
        invia(ser, "ERR:EVENT_INCOMPLETE:" + nome_cartella)
        invia(ser, "ERR:REASON:CONFIG_MISSING")
        mostra_errore_hdmi(nome_cartella, "config.txt mancante")
        return

    titolo = config.get("titolo", nome_cartella)
    modo = config.get("modo", "sequenziale")
    musica = config.get("musica", "off")
    descrizione = config.get("descrizione", "")

    try:
        tempo_foto = int(config.get("tempo_foto", "5"))
    except ValueError:
        tempo_foto = 5

    ultimo_evento = titolo if titolo != "" else nome_cartella

    cover = percorso_evento / "cover.jpg"
    media = trova_media(percorso_evento)

    if not cover.exists():
        print("Evento incompleto: cover.jpg mancante")
        invia(ser, "ERR:EVENT_INCOMPLETE:" + nome_cartella)
        invia(ser, "ERR:REASON:COVER_MISSING")
        mostra_errore_hdmi(nome_cartella, "cover.jpg mancante")
        return

    if len(media) == 0:
        print("Evento incompleto: nessun contenuto")
        invia(ser, "ERR:EVENT_INCOMPLETE:" + nome_cartella)
        invia(ser, "ERR:REASON:NO_MEDIA")
        mostra_errore_hdmi(nome_cartella, "nessun contenuto trovato")
        return

    print("Evento trovato:", nome_cartella)
    print("Percorso:", percorso_evento)
    print("Titolo:", titolo)
    print("Modo:", modo)
    print("Musica:", musica)
    print("Tempo foto:", tempo_foto)
    print("Descrizione:", descrizione)
    print("Cover presente:", cover.exists())
    print("Numero contenuti:", len(media))

    invia(ser, "OK:EVENT_FOUND")
    invia(ser, "EVENT:" + nome_cartella)
    invia(ser, "TITLE:" + titolo)
    invia(ser, "MODE:" + modo)
    invia(ser, "MUSIC:" + musica)
    invia(ser, "DESC:" + descrizione)
    invia(ser, "COVER:OK")
    invia(ser, "IMG_COUNT:" + str(len(media)))
    invia(ser, "OK:EVENT_DATA_SENT")

    mostra_intro_evento_hdmi(cover, titolo, descrizione, len(media))

    if attesa_intro_interrompibile(ser, DURATA_INTRO_EVENTO):
        return

    avvia_slideshow(ser, media, modo, tempo_foto, musica)

    print("--------------------------------")


# ============================================================
# FUNZIONE: gestisci_comando_idle(ser, riga)
# ============================================================
#
# Scopo:
# Gestisce i comandi ESP32 quando non siamo dentro una funzione media.
#
# Output:
# Nessuno.
# ============================================================

def gestisci_comando_idle(ser, riga):
    global pausa_attiva
    global mute_attivo
    global uid_attivo

    if riga == "PING":
        invia(ser, "PONG")
        return

    if riga == "ESP32_READY":
        print("ESP32 pronto")
        return

    if riga == "CMD:PAUSE":
        pausa_attiva = not pausa_attiva

        if pausa_attiva:
            print("Comando touch: PAUSE ON")
            metti_in_pausa_processi()
            invia(ser, "STATUS:PAUSED")
        else:
            print("Comando touch: PAUSE OFF")
            riprendi_processi()
            invia(ser, "STATUS:PLAYING")

        return

    if riga == "CMD:MUTE":
        mute_attivo = not mute_attivo

        if mute_attivo:
            print("Comando touch: MUTE ON")
            ferma_musica()
            invia(ser, "STATUS:MUTED")
        else:
            print("Comando touch: MUTE OFF")
            invia(ser, "STATUS:UNMUTED")
            avvia_musica(mp3_correnti)

        return

    if riga == "CMD:STOP":
        print("Comando touch: STOP")
        ferma_visualizzazione()

        if ultimo_evento != "":
            mostra_stop_hdmi(ultimo_evento)
        else:
            mostra_stop_hdmi("Evento")

        uid_attivo = ""
        invia(ser, "STATUS:WAIT_TAG")
        return

    if riga == "TAG_REMOVED" or riga.startswith("TAG_REMOVED:"):
        uid_rimosso = estrai_uid_da_tag_removed(riga)

        if not tag_removed_valido(uid_rimosso):
            return

        print("Tag rimosso")
        ferma_visualizzazione()

        if ultimo_evento != "":
            mostra_tag_rimosso_hdmi(ultimo_evento)
        else:
            mostra_tag_rimosso_hdmi("Evento")

        uid_attivo = ""
        invia(ser, "STATUS:WAIT_TAG")
        return

    if riga == "TEST_RFID":
        invia(ser, "ERR:TEST_RFID_IS_ESP32_COMMAND")
        return

    if riga.startswith("ERR:"):
        print("Errore ricevuto da ESP32:", riga)
        return

    if riga.startswith("OK:"):
        print("Conferma ricevuta da ESP32:", riga)
        return

    print("Comando sconosciuto ignorato:", riga)


# ============================================================
# FUNZIONE: main()
# ============================================================
#
# Scopo:
# Punto di ingresso del programma Raspberry.
#
# Passi:
# 1. stampa configurazione
# 2. carica rfid_map.json da SSD
# 3. apre seriale con ESP32
# 4. aspetta messaggi
# 5. se arriva RFID:<uid>, avvia gestisci_uid()
# ============================================================

def main():
    print("MultiPlayer_FOTO - Raspberry main.py")
    print("Cartella temporanea:", BASE_DIR)
    print("Album SSD:", ALBUM_ROOT)
    print("Mappa RFID:", MAP_FILE)
    print("Seriale:", PORTA_SERIALE)
    print("--------------------------------")

    mappa_rfid = carica_mappa()
    print("UID caricati:", len(mappa_rfid))

    ser = serial.Serial(PORTA_SERIALE, BAUD, timeout=1)
    time.sleep(2)

    ser.reset_input_buffer()
    ser.reset_output_buffer()

    mostra_pronto_hdmi()

    invia(ser, "RPI_READY")

    print("In attesa di messaggi da ESP32...")
    print("--------------------------------")

    while True:
        riga = ser.readline().decode("utf-8", errors="ignore").strip()

        if not riga:
            continue

        print("ESP32 -> Raspberry:", riga)

        if riga.startswith("RFID:"):
            uid = riga.replace("RFID:", "", 1).strip()
            gestisci_uid(ser, uid, mappa_rfid)
        else:
            gestisci_comando_idle(ser, riga)


# ============================================================
# AVVIO DEL PROGRAMMA
# ============================================================
#
# Permette di eseguire il programma con:
#
#   python3 main.py
#
# In caso di CTRL+C ferma media e musica prima di uscire.
# ============================================================

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        print("Chiusura programma...")
        ferma_visualizzazione()
