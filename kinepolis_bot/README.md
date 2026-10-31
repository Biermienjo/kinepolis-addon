# Kinepolis (Home Assistant OS-add-on)

Scanners die films in de gaten houden, ook films die nog niet op Kinepolis staan.
Zodra er een voorstelling is, kiest de app de beste plaatsen en vraagt je op
**Telegram** (en in de app): *Ja → in je mandje*, *Volgende* of *Stop voorstellen*.
Betalen doe je zelf, in de live browser in de app (ook op je gsm via de HA-app).

## Installeren

1. **Repository toevoegen.** Instellingen → Add-ons → Add-on Store → ⋮ (rechtsboven) →
   *Repositories* → plak `https://github.com/Biermienjo/kinepolis-addon` → *Toevoegen*.
2. Herlaad de pagina. Onderaan staat **Kinepolis** → *Installeren*. De Pi bouwt de add-on zelf:
   reken de eerste keer op 10–20 minuten.
3. **Configuratie:**
   - `telegram_token`, `telegram_chat_id`: voor de voorstellen met Ja/Nee-knoppen.
     Stuur je bot eerst zelf `/start`. Gebruik de token niet tegelijk in een ander script
     dat berichten *ontvangt*.
   - `locaties`: namen voor cinemacodes, bv. `kortrijk=KKOR, brussel=KBRU, leuven=SL`.
     Alleen benoemde cinema's kun je kiezen voor een film die nog geen voorstellingen heeft.
   - `scan_interval_seconds`: hoe vaak een scanner kijkt (standaard 30 s).
   - `ha_notify_service` (optioneel): bv. `notify.mobile_app_iphone_van_nathan` voor een
     push via de Home Assistant-app.
   - `film_paginas`: Kinepolis-pagina's waarop gezocht wordt naar films op naam (zie onder).
4. Zet **Weergeven in zijbalk** aan en start de add-on. **Kinepolis** staat in je zijbalk.
5. Kijk in **Log**: daar staat of Telegram verbonden is.

**Updates:** verschijnen vanzelf in Home Assistant (Instellingen → Updates) → *Update*.

Geen poort doorsturen, geen certificaat: de app draait binnen Home Assistant (Ingress).

## Gebruik

1. **Film**: typ de naam (of plak de kinepolis.be-link).
   - Staat hij op Kinepolis → kies hem uit de lijst.
   - Staat hij er (nog) niet → *wacht op "…"*: de scanner kijkt elke 5 minuten of de film
     verschijnt, meldt het en scant vanaf dan naar voorstellingen.
2. **Instellen**: personen, locaties (de volgorde waarin je ze aantikt = je voorkeur),
   periode (standaard *Alles*), vanaf welk uur, formaat → **🔔 Start scanner**.
3. **Scanners**: al je scanners met hun status; pauzeren, hervatten, stoppen, ＋ nieuwe.
   Ze lopen oneindig door tot je ze stopt (ook vanuit Telegram na een geslaagd mandje).
4. **Voorstel** (Telegram + tab *Boeking*): beeld van de zaal met jullie plaatsen.
   - *Ja* → stoelen aanklikken, *Volwassene* kiezen, tot aan je gegevens.
   - *Volgende* → beste plaatsen in de volgende voorstelling.
   - *Stop voorstellen* → deze voorstellingen overslaan, verder scannen naar nieuwe.
   In de app kun je eerst nog andere stoelen tikken.
5. **Live**: gegevens invullen en betalen. Kinepolis houdt stoelen maar een paar minuten vast.

Volgorde van voorstellen: vroegste dag eerst, dan je favoriete cinema, dan het uur.
Tot je *Ja* zegt, zijn de plaatsen **niet** vastgehouden; zijn ze intussen weg, dan zoekt
de app opnieuw en krijg je een nieuw voorstel.

## Wat (nog) niet getest is tegen de echte site

Deze omgeving kon kinepolis.be niet bereiken; alles is getest tegen nagemaakte pagina's.
- **Films op naam**: de app leest filmlinks (`/movies/detail/<id>/…`) van de pagina's in
  `film_paginas`. Welke Kinepolis-pagina's alle (ook komende) films tonen, is niet gecontroleerd;
  pas de lijst aan als de scanner niets vindt (de fout staat bij de scanner).
- De **ticketpagina** heb ik nooit gezien: de app zoekt "Volwassene", een +-knop en daarna
  "Doorgaan/Volgende". Lukt dat niet, dan zegt de app dat en rond je af in *Live*.
- **Turnstile** of een **wachtrij** bij een drop: de app wacht tot 10 minuten; kijk mee in *Live*.
- De voorstellings-id `<cinemacode>-<vistaSessionId>` (bv. `KKOR-164609`) komt uit de oude API.
- De push via `ha_notify_service` en de link naar het paneel (`/hassio/ingress/local_kinepolis_bot`).
- Studentenkaart: later.

## Bestanden

- `app/server.py`: webserver, scanners, voorstellen, Telegram-knoppen, live-koppeling (noVNC)
- `app/web/`: de app (HTML, CSS, JavaScript)
- `app/core.py`: instellingen, opslag, zoeken, films op naam, meldingen
- `app/api.py`: Kinepolis-API's (voorstellingen, vrije plaatsen)
- `app/booker.py`: browser: zaal openen, catalogus lezen, boeken
- `app/picker.py` + `app/seats.py`: stoelenkaart lezen en beste stoelen kiezen
- `app/test_*.py`: tests tegen nagemaakte pagina's
