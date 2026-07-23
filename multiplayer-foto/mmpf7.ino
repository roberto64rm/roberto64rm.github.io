/*
  MultiPlayer_FOTO
  ESP32 - RFID RC522 + TFT 240x320 + Touch PAUSA/MUTE/STOP + LED RGB + Beeper + Seriale Raspberry

  Seriale Raspberry:
  Raspberry GPIO14 TX pin 8  -> ESP32 GPIO16 RX2
  Raspberry GPIO15 RX pin 10 <- ESP32 GPIO17 TX2
  GND Raspberry              -> GND ESP32

  Bus SPI comune:
  SCK  -> GPIO18
  MISO -> GPIO19
  MOSI -> GPIO23

  TFT:
  CS     -> GPIO27
  DC     -> GPIO26
  RESET  -> GPIO25

  TOUCH XPT2046:
  T_CS   -> GPIO14

  RC522:
  SDA/SS -> GPIO5
  RST    -> GPIO22

  LED RGB ANODO COMUNE:
  Rosso  -> GPIO32
  Verde  -> GPIO33
  Blu    -> GPIO13

  Beeper passivo/PWM:
  +      -> GPIO21
  -      -> GND
*/

#include <SPI.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ILI9341.h>
#include <XPT2046_Touchscreen.h>
#include <MFRC522.h>

// ----------------------------------------------------
// PIN
// ----------------------------------------------------

#define RX_RPI 16
#define TX_RPI 17

#define SPI_SCK 18
#define SPI_MISO 19
#define SPI_MOSI 23

#define TFT_CS 27
#define TFT_DC 26
#define TFT_RST 25

#define TOUCH_CS 14

#define RFID_CS 5
#define RFID_RST 22

#define PIN_LED_R 32
#define PIN_LED_G 33
#define PIN_LED_B 13

#define PIN_BEEPER 21

// ----------------------------------------------------
// OGGETTI
// ----------------------------------------------------

Adafruit_ILI9341 tft = Adafruit_ILI9341(TFT_CS, TFT_DC, TFT_RST);
XPT2046_Touchscreen touch(TOUCH_CS);
MFRC522 rfid(RFID_CS, RFID_RST);

// ----------------------------------------------------
// VARIABILI EVENTO
// ----------------------------------------------------

String eventoCorrente = "";
String titoloCorrente = "";
String modoCorrente = "";
String musicaCorrente = "";
String descrizioneCorrente = "";
String coverCorrente = "";
String statoCorrente = "";
String motivoErrore = "";

int numeroImmagini = 0;
bool raspberryPronto = false;
// ----------------------------------------------------
// VARIABILI LED RGB + BEEPER
// ----------------------------------------------------

// LED RGB ad anodo comune:
// HIGH = colore spento
// LOW  = colore acceso
bool erroreUidAttivo = false;
bool statoLampeggioRosso = false;

unsigned long timerLampeggioRosso = 0;
const unsigned long TEMPO_LAMPEGGIO_ERRORE = 300;

// ----------------------------------------------------
// VARIABILI TOUCH BUTTON
// ----------------------------------------------------

// Taratura di base per XPT2046 con rotazione 2.
// Se il tocco non corrisponde perfettamente ai pulsanti,
// questi quattro valori sono gli unici da ritoccare.
const int TOUCH_RAW_MIN_X = 300;
const int TOUCH_RAW_MAX_X = 3900;
const int TOUCH_RAW_MIN_Y = 300;
const int TOUCH_RAW_MAX_Y = 3900;

unsigned long ultimoTouchMs = 0;
const unsigned long TOUCH_DEBOUNCE_MS = 450;

bool pulsantiTouchAttivi = false;

// ----------------------------------------------------
// VARIABILI RFID
// ----------------------------------------------------

String uidAttivo = "";
bool tagPresente = false;

unsigned long ultimoTagSeenMs = 0;
unsigned long ultimoControlloRFID = 0;

const unsigned long PAUSA_CONTROLLO_RFID = 250;
const unsigned long TIMEOUT_RIMOZIONE = 3500;

int mancateLetture = 0;
const int SOGLIA_MANCATE_LETTURE = 15;

// ----------------------------------------------------
// SETUP
// ----------------------------------------------------

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial2.begin(115200, SERIAL_8N1, RX_RPI, TX_RPI);

  pinMode(TFT_CS, OUTPUT);
  pinMode(TOUCH_CS, OUTPUT);
  pinMode(RFID_CS, OUTPUT);

  digitalWrite(TFT_CS, HIGH);
  digitalWrite(TOUCH_CS, HIGH);
  digitalWrite(RFID_CS, HIGH);

  SPI.begin(SPI_SCK, SPI_MISO, SPI_MOSI);

  avviaLedBeeper();
  ledBlu();

  avviaDisplay();
  avviaTouch();
  avviaRFID();

  schermataSplash();
  delay(2000);
  schermataAvvioRaspberry();

  Serial.println();
  Serial.println("ESP32 pronto - MultiPlayer_FOTO con TFT");
  Serial.println("--------------------------------");

  Serial2.println("ESP32_READY");
}

// ----------------------------------------------------
// LOOP
// ----------------------------------------------------

void loop() {
  leggiSerialeRaspberry();
  gestisciRFID();
  gestisciTouch();
  aggiornaLampeggioErroreUid();
}


// ----------------------------------------------------
// LED RGB ANODO COMUNE + BEEPER
// ----------------------------------------------------

void avviaLedBeeper() {
  pinMode(PIN_LED_R, OUTPUT);
  pinMode(PIN_LED_G, OUTPUT);
  pinMode(PIN_LED_B, OUTPUT);
  pinMode(PIN_BEEPER, OUTPUT);

  ledSpento();
  noTone(PIN_BEEPER);
}

void ledSpento() {
  digitalWrite(PIN_LED_R, HIGH);
  digitalWrite(PIN_LED_G, HIGH);
  digitalWrite(PIN_LED_B, HIGH);
}

void ledRosso() {
  digitalWrite(PIN_LED_R, LOW);
  digitalWrite(PIN_LED_G, HIGH);
  digitalWrite(PIN_LED_B, HIGH);
}

void ledVerde() {
  digitalWrite(PIN_LED_R, HIGH);
  digitalWrite(PIN_LED_G, LOW);
  digitalWrite(PIN_LED_B, HIGH);
}

void ledBlu() {
  digitalWrite(PIN_LED_R, HIGH);
  digitalWrite(PIN_LED_G, HIGH);
  digitalWrite(PIN_LED_B, LOW);
}

void lampeggiaRosso(int numeroLampeggi, int tempoMs) {
  for (int i = 0; i < numeroLampeggi; i++) {
    ledRosso();
    delay(tempoMs);
    ledSpento();
    delay(tempoMs);
  }
}

void beep(int frequenza, int durataMs) {
  tone(PIN_BEEPER, frequenza);
  delay(durataMs);
  noTone(PIN_BEEPER);
}

void beepMultiplo(int numero, int frequenza, int durataMs, int pausaMs) {
  for (int i = 0; i < numero; i++) {
    beep(frequenza, durataMs);
    if (i < numero - 1) delay(pausaMs);
  }
}

void avviaErroreUidSconosciuto() {
  erroreUidAttivo = true;
  statoLampeggioRosso = false;
  timerLampeggioRosso = 0;
  beepMultiplo(3, 300, 140, 120);
}

void fermaErroreUidSconosciuto() {
  erroreUidAttivo = false;
  statoLampeggioRosso = false;
}

void aggiornaLampeggioErroreUid() {
  if (!erroreUidAttivo) return;

  if (millis() - timerLampeggioRosso < TEMPO_LAMPEGGIO_ERRORE) return;
  timerLampeggioRosso = millis();

  statoLampeggioRosso = !statoLampeggioRosso;

  if (statoLampeggioRosso) {
    ledRosso();
  } else {
    ledSpento();
  }
}

// ----------------------------------------------------
// AVVIO HARDWARE
// ----------------------------------------------------

void avviaDisplay() {
  digitalWrite(TOUCH_CS, HIGH);
  digitalWrite(RFID_CS, HIGH);
  digitalWrite(TFT_CS, LOW);

  tft.begin();
  tft.setRotation(2);

  digitalWrite(TFT_CS, HIGH);
}

void avviaTouch() {
  digitalWrite(TFT_CS, HIGH);
  digitalWrite(RFID_CS, HIGH);
  digitalWrite(TOUCH_CS, LOW);

  touch.begin();
  touch.setRotation(2);

  digitalWrite(TOUCH_CS, HIGH);
}

void avviaRFID() {
  digitalWrite(TFT_CS, HIGH);
  digitalWrite(TOUCH_CS, HIGH);
  digitalWrite(RFID_CS, LOW);

  rfid.PCD_Init();

  digitalWrite(RFID_CS, HIGH);
}

// ----------------------------------------------------
// GESTIONE CS SPI
// ----------------------------------------------------

void usaDisplay() {
  digitalWrite(TOUCH_CS, HIGH);
  digitalWrite(RFID_CS, HIGH);
  digitalWrite(TFT_CS, LOW);
}

void usaRFID() {
  digitalWrite(TFT_CS, HIGH);
  digitalWrite(TOUCH_CS, HIGH);
  digitalWrite(RFID_CS, LOW);
}

void usaTouch() {
  digitalWrite(TFT_CS, HIGH);
  digitalWrite(RFID_CS, HIGH);
  digitalWrite(TOUCH_CS, LOW);
}

void liberaSPI() {
  digitalWrite(TFT_CS, HIGH);
  digitalWrite(TOUCH_CS, HIGH);
  digitalWrite(RFID_CS, HIGH);
}

// ----------------------------------------------------
// SERIALE RASPBERRY
// ----------------------------------------------------

void leggiSerialeRaspberry() {
  if (!Serial2.available()) return;

  String msg = Serial2.readStringUntil('\n');
  msg.trim();

  if (msg.length() == 0) return;

  Serial.print("Ricevuto da Raspberry: ");
  Serial.println(msg);

  gestisciMessaggio(msg);
}

void gestisciMessaggio(String msg) {

  if (msg == "PING") {
    Serial2.println("PONG");
  }

  else if (msg == "RPI_READY") {
    raspberryPronto = true;
    statoCorrente = "Raspberry pronto";

    Serial.println("Raspberry pronto - RFID abilitato");

    tagPresente = false;
    uidAttivo = "";
    mancateLetture = 0;


    ledBlu();
    beep(1500, 120);

    schermataAttesaTag();
  }

  else if (msg == "OK:EVENT_FOUND") {
    statoCorrente = "Evento trovato";
    fermaErroreUidSconosciuto();
    ledVerde();
  }

  else if (msg.startsWith("EVENT:")) {
    eventoCorrente = msg.substring(6);
  }

  else if (msg.startsWith("TITLE:")) {
    titoloCorrente = msg.substring(6);
  }

  else if (msg.startsWith("MODE:")) {
    modoCorrente = msg.substring(5);
  }

  else if (msg.startsWith("MUSIC:")) {
    musicaCorrente = msg.substring(6);
  }

  else if (msg.startsWith("DESC:")) {
    descrizioneCorrente = msg.substring(5);
  }

  else if (msg.startsWith("COVER:")) {
    coverCorrente = msg.substring(6);
  }

  else if (msg.startsWith("IMG_COUNT:")) {
    numeroImmagini = msg.substring(10).toInt();
  }

  else if (msg == "OK:EVENT_DATA_SENT") {
    stampaDatiEvento();
    schermataEvento();
    Serial2.println("OK:EVENT_DATA_RECEIVED");
  }

  else if (msg == "STATUS:WAIT_TAG") {
    resetEvento();
    fermaErroreUidSconosciuto();
    ledBlu();
    schermataSchedaRimossa();
  }

  else if (msg.startsWith("ERR:EVENT_INCOMPLETE:")) {
    eventoCorrente = msg.substring(21);
    statoCorrente = "Cartella incompleta";
  }

  else if (msg.startsWith("ERR:REASON:")) {
    motivoErrore = msg.substring(11);
    schermataIncompleta();
  }

  else if (msg == "ERR:FOLDER_NOT_FOUND") {
    statoCorrente = "Cartella mancante";
    motivoErrore = "Cartella mancante";
    schermataIncompleta();
  }

  else if (msg == "ERR:UID_UNKNOWN") {
    statoCorrente = "UID sconosciuto";
    motivoErrore = "UID sconosciuto";
    avviaErroreUidSconosciuto();
    schermataIncompleta();
  }

  else if (msg.startsWith("ERR:")) {
    motivoErrore = msg;
    schermataIncompleta();
  }

  else if (msg.startsWith("OK:")) {
    Serial.print("Conferma: ");
    Serial.println(msg);
  }

  else {
    Serial.print("Messaggio ignorato: ");
    Serial.println(msg);
  }
}

// ----------------------------------------------------
// RFID
// ----------------------------------------------------

void gestisciRFID() {

  if (!raspberryPronto) {
    return;
  }
  if (millis() - ultimoControlloRFID < PAUSA_CONTROLLO_RFID) return;
  ultimoControlloRFID = millis();

  String uidLetto = "";

  if (leggiUIDdaRC522(uidLetto)) {
    ultimoTagSeenMs = millis();
    mancateLetture = 0;

    if (!tagPresente) {
      tagPresente = true;
      uidAttivo = uidLetto;

      Serial.print("Nuova scheda UID: ");
      Serial.println(uidAttivo);

      fermaErroreUidSconosciuto();
      beep(1000, 120);

      schermataLetturaTag(uidAttivo);
      inviaUIDaRaspberry(uidAttivo);
      return;
    }

    if (uidLetto != uidAttivo) {
      uidAttivo = uidLetto;

      Serial.print("Scheda cambiata UID: ");
      Serial.println(uidAttivo);

      fermaErroreUidSconosciuto();
      beep(1000, 120);

      schermataLetturaTag(uidAttivo);
      inviaUIDaRaspberry(uidAttivo);
      return;
    }

    return;
  }

  if (tagPresente) {
    if (tagNelCampo()) {
      ultimoTagSeenMs = millis();
      mancateLetture = 0;
      return;
    }

    mancateLetture++;

    if (mancateLetture >= SOGLIA_MANCATE_LETTURE && millis() - ultimoTagSeenMs > TIMEOUT_RIMOZIONE) {

      Serial.println("Scheda rimossa confermata");
      Serial2.print("TAG_REMOVED:");
      Serial2.println(uidAttivo);

      fermaErroreUidSconosciuto();
      beepMultiplo(2, 440, 120, 120);
      lampeggiaRosso(3, 180);
      ledBlu();

      tagPresente = false;
      uidAttivo = "";
      mancateLetture = 0;

      resetEvento();
      schermataSchedaRimossa();
    }
  }
}

bool leggiUIDdaRC522(String &uid) {
  usaRFID();

  if (!rfid.PICC_IsNewCardPresent()) {
    liberaSPI();
    return false;
  }

  if (!rfid.PICC_ReadCardSerial()) {
    liberaSPI();
    return false;
  }

  uid = "";

  for (byte i = 0; i < rfid.uid.size; i++) {
    if (rfid.uid.uidByte[i] < 0x10) uid += "0";
    uid += String(rfid.uid.uidByte[i], HEX);
  }

  uid.toUpperCase();

  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();

  liberaSPI();
  return true;
}

bool tagNelCampo() {
  usaRFID();

  byte atqa[2];
  byte atqaSize = sizeof(atqa);

  MFRC522::StatusCode status = rfid.PICC_WakeupA(atqa, &atqaSize);

  if (status == MFRC522::STATUS_OK || status == MFRC522::STATUS_COLLISION) {
    rfid.PICC_HaltA();
    liberaSPI();
    return true;
  }

  liberaSPI();
  return false;
}

void inviaUIDaRaspberry(String uid) {
  Serial2.print("RFID:");
  Serial2.println(uid);

  Serial.print("Inviato a Raspberry: RFID:");
  Serial.println(uid);
}

// ----------------------------------------------------
// SCHERMATE TFT
// ----------------------------------------------------

void schermataSplash() {
  pulsantiTouchAttivi = false;
  usaDisplay();

  tft.fillScreen(ILI9341_BLACK);
  tft.drawRect(0, 0, 240, 320, ILI9341_WHITE);

  tft.setTextColor(ILI9341_YELLOW);
  tft.setTextSize(3);
  tft.setCursor(25, 55);
  tft.println("MULTI");

  tft.setCursor(25, 90);
  tft.println("PLAYER");

  tft.setTextColor(ILI9341_CYAN);
  tft.setTextSize(2);
  tft.setCursor(35, 145);
  tft.println("FOTO RFID");

  tft.setTextColor(ILI9341_WHITE);
  tft.setTextSize(1);
  tft.setCursor(55, 250);
  tft.println("MMPF 2026");

  liberaSPI();
}

void schermataAttesaTag() {
  pulsantiTouchAttivi = false;
  ledBlu();
  usaDisplay();

  tft.fillScreen(ILI9341_BLACK);
  tft.drawRect(0, 0, 240, 320, ILI9341_WHITE);

  tft.setTextColor(ILI9341_YELLOW);
  tft.setTextSize(2);
  tft.setCursor(25, 30);
  tft.println("MULTI PLAYER");

  tft.setCursor(45, 55);
  tft.println("FOTO RFID");

  tft.drawLine(10, 90, 230, 90, ILI9341_WHITE);

  tft.setTextColor(ILI9341_GREEN);
  tft.setTextSize(2);
  tft.setCursor(20, 130);
  tft.println("Scegliere");

  tft.setCursor(20, 160);
  tft.println("scheda TAG");

  liberaSPI();
}

void schermataLetturaTag(String uid) {
  pulsantiTouchAttivi = false;
  usaDisplay();

  tft.fillScreen(ILI9341_BLACK);
  tft.drawRect(0, 0, 240, 320, ILI9341_WHITE);

  tft.setTextColor(ILI9341_CYAN);
  tft.setTextSize(2);
  tft.setCursor(20, 35);
  tft.println("TAG LETTO");

  tft.setTextColor(ILI9341_WHITE);
  tft.setTextSize(2);
  tft.setCursor(20, 90);
  tft.println(uid);

  tft.setTextSize(1);
  tft.setCursor(20, 150);
  tft.println("Attendo dati");
  tft.setCursor(20, 170);
  tft.println("dal Raspberry...");

  liberaSPI();
}

void schermataEvento() {
  usaDisplay();

  tft.fillScreen(ILI9341_BLACK);
  tft.drawRect(0, 0, 240, 320, ILI9341_GREEN);

  tft.setTextColor(ILI9341_YELLOW);
  tft.setTextSize(2);
  stampaTestoCentrato(titoloCorrente, 20, 2, ILI9341_YELLOW);

  tft.drawLine(10, 60, 230, 60, ILI9341_WHITE);

  tft.setTextColor(ILI9341_WHITE);
  tft.setTextSize(2);

  tft.setCursor(15, 80);
  tft.print("Foto: ");
  tft.println(numeroImmagini);

  tft.setCursor(15, 110);
  tft.print("Modo: ");
  tft.println(modoCorrente);

  tft.setCursor(15, 140);
  tft.print("Musica: ");
  tft.println(musicaCorrente);

  tft.setTextColor(ILI9341_CYAN);
  tft.setTextSize(1);
  tft.setCursor(15, 185);
  stampaDescrizione(descrizioneCorrente, 15, 185);

  disegnaPulsantiTouch();
  pulsantiTouchAttivi = true;

  liberaSPI();
}

void schermataIncompleta() {
  pulsantiTouchAttivi = false;
  usaDisplay();

  tft.fillScreen(ILI9341_BLACK);
  tft.drawRect(0, 0, 240, 320, ILI9341_RED);

  tft.setTextColor(ILI9341_RED);
  tft.setTextSize(2);
  stampaTestoCentrato("INCOMPLETA", 35, 2, ILI9341_RED);

  tft.setTextColor(ILI9341_WHITE);
  tft.setTextSize(1);
  stampaTestoCentrato(eventoCorrente, 90, 1, ILI9341_WHITE);

  tft.setTextColor(ILI9341_YELLOW);
  tft.setCursor(20, 140);
  tft.println("Motivo:");

  tft.setTextColor(ILI9341_WHITE);
  tft.setCursor(20, 160);
  tft.println(motivoErrore);

  tft.setTextColor(ILI9341_CYAN);
  tft.setTextSize(2);
  tft.setCursor(20, 230);
  tft.println("Cambiare");
  tft.setCursor(20, 260);
  tft.println("scheda TAG");

  liberaSPI();
}

void schermataSchedaRimossa() {
  pulsantiTouchAttivi = false;
  usaDisplay();

  tft.fillScreen(ILI9341_BLACK);
  tft.drawRect(0, 0, 240, 320, ILI9341_ORANGE);

  tft.setTextColor(ILI9341_ORANGE);
  tft.setTextSize(2);
  stampaTestoCentrato("SCHEDA", 55, 2, ILI9341_ORANGE);
  stampaTestoCentrato("RIMOSSA", 85, 2, ILI9341_ORANGE);

  tft.setTextColor(ILI9341_WHITE);
  tft.setTextSize(2);
  tft.setCursor(20, 160);
  tft.println("Inserire");
  tft.setCursor(20, 190);
  tft.println("nuova");
  tft.setCursor(20, 220);
  tft.println("scheda TAG");

  liberaSPI();
}

void schermataAvvioRaspberry() {
  usaDisplay();

  tft.fillScreen(ILI9341_BLACK);
  tft.drawRect(0, 0, 240, 320, ILI9341_ORANGE);

  tft.setTextColor(ILI9341_YELLOW);
  tft.setTextSize(2);
  tft.setCursor(25, 35);
  tft.println("MULTI PLAYER");

  tft.setCursor(45, 60);
  tft.println("FOTO RFID");

  tft.drawLine(10, 95, 230, 95, ILI9341_WHITE);

  tft.setTextColor(ILI9341_CYAN);
  tft.setTextSize(2);
  tft.setCursor(20, 125);
  tft.println("Avvio");

  tft.setCursor(20, 155);
  tft.println("Raspberry...");

  tft.setTextColor(ILI9341_WHITE);
  tft.setTextSize(1);
  tft.setCursor(20, 215);
  tft.println("Attendere il messaggio:");

  tft.setTextColor(ILI9341_GREEN);
  tft.setTextSize(1);
  tft.setCursor(20, 240);
  tft.println("Sistema pronto");

  tft.setTextColor(ILI9341_RED);
  tft.setCursor(20, 275);
  tft.println("Non inserire ancora la TAG");

  liberaSPI();
}
// ----------------------------------------------------
// PULSANTI TOUCH PAUSA / MUTE / STOP
// ----------------------------------------------------

void disegnaPulsantiTouch() {
  // Tre pulsanti nella parte bassa del display.
  // Coordinate display: 240 x 320.

  tft.fillRect(5, 278, 72, 34, ILI9341_DARKGREY);
  tft.drawRect(5, 278, 72, 34, ILI9341_WHITE);
  tft.setTextColor(ILI9341_WHITE);
  tft.setTextSize(1);
  tft.setCursor(22, 291);
  tft.print("PAUSA");

  tft.fillRect(84, 278, 72, 34, ILI9341_DARKGREY);
  tft.drawRect(84, 278, 72, 34, ILI9341_WHITE);
  tft.setCursor(105, 291);
  tft.print("MUTE");

  tft.fillRect(163, 278, 72, 34, ILI9341_RED);
  tft.drawRect(163, 278, 72, 34, ILI9341_WHITE);
  tft.setCursor(187, 291);
  tft.print("STOP");
}

bool leggiPuntoTouch(int &x, int &y) {
  usaTouch();

  if (!touch.touched()) {
    liberaSPI();
    return false;
  }

  TS_Point p = touch.getPoint();
  liberaSPI();

  x = map(p.x, TOUCH_RAW_MIN_X, TOUCH_RAW_MAX_X, 239, 0);
  y = map(p.y, TOUCH_RAW_MIN_Y, TOUCH_RAW_MAX_Y, 319, 0);

  x = constrain(x, 0, 239);
  y = constrain(y, 0, 319);

  return true;
}

void gestisciTouch() {
  if (!pulsantiTouchAttivi) return;
  if (millis() - ultimoTouchMs < TOUCH_DEBOUNCE_MS) return;

  int x = 0;
  int y = 0;

  if (!leggiPuntoTouch(x, y)) return;

  // I pulsanti sono validi solo nella fascia bassa.
  if (y < 278 || y > 319) return;

  ultimoTouchMs = millis();

  if (x >= 5 && x <= 77) {
    Serial.println("Touch: PAUSA");
    Serial2.println("CMD:PAUSE");
    beep(900, 80);
  }

  else if (x >= 84 && x <= 156) {
    Serial.println("Touch: MUTE");
    Serial2.println("CMD:MUTE");
    beep(700, 80);
  }

  else if (x >= 163 && x <= 235) {
    Serial.println("Touch: STOP");
    Serial2.println("CMD:STOP");
    beep(500, 120);
    pulsantiTouchAttivi = false;
  }
}

// ----------------------------------------------------
// UTILITY DISPLAY
// ----------------------------------------------------

void stampaTestoCentrato(String testo, int y, int size, uint16_t colore) {
  tft.setTextSize(size);
  tft.setTextColor(colore);

  int16_t x1, y1;
  uint16_t w, h;

  tft.getTextBounds(testo, 0, y, &x1, &y1, &w, &h);

  int x = (240 - w) / 2;
  if (x < 0) x = 0;

  tft.setCursor(x, y);
  tft.print(testo);
}

void stampaDescrizione(String testo, int x, int y) {
  int maxCar = 28;
  int riga = 0;

  while (testo.length() > 0 && riga < 6) {
    String pezzo = testo.substring(0, maxCar);

    if (testo.length() > maxCar) {
      int spazio = pezzo.lastIndexOf(' ');
      if (spazio > 0) {
        pezzo = pezzo.substring(0, spazio);
      }
    }

    tft.setCursor(x, y + (riga * 15));
    tft.println(pezzo);

    testo = testo.substring(pezzo.length());
    testo.trim();

    riga++;
  }
}

// ----------------------------------------------------
// DEBUG
// ----------------------------------------------------

void stampaDatiEvento() {
  Serial.println("--------------------------------");
  Serial.println("DATI EVENTO COMPLETI");
  Serial.print("Evento: ");
  Serial.println(eventoCorrente);
  Serial.print("Titolo: ");
  Serial.println(titoloCorrente);
  Serial.print("Modo: ");
  Serial.println(modoCorrente);
  Serial.print("Musica: ");
  Serial.println(musicaCorrente);
  Serial.print("Descrizione: ");
  Serial.println(descrizioneCorrente);
  Serial.print("Cover: ");
  Serial.println(coverCorrente);
  Serial.print("Numero immagini: ");
  Serial.println(numeroImmagini);
  Serial.println("--------------------------------");
}

void resetEvento() {
  eventoCorrente = "";
  titoloCorrente = "";
  modoCorrente = "";
  musicaCorrente = "";
  descrizioneCorrente = "";
  coverCorrente = "";
  statoCorrente = "";
  motivoErrore = "";
  numeroImmagini = 0;
}