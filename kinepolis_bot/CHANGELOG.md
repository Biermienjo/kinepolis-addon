# Changelog

## 0.7.0
- **Wanneer kan je?** Kies *Alle dagen* of tik in een **kalender** de dagen aan waarop je kunt
  (meerdere, ook maanden vooruit; snelknop *Weekends*). De scanner kijkt precies naar die dagen.
- **Sneller**: na *Kies* staat de film meteen klaar (geen wachten op voorstellingen); de
  cinemalijst verschijnt meteen; de Kinepolis-sleutel wordt al bij het opstarten opgehaald.
- **Zoeken op naam sneller**: stoppen zodra de film in de data van de pagina staat, en de
  filmlijst wordt op de achtergrond ingelezen en een uur bijgehouden.
- `telegram_chat_id` staat standaard op 5932217902.

## 0.6.5
- **Films op naam**: de browser wacht tot de filmkaarten verschenen zijn en haalt films ook uit
  de filmdata (JSON) die de pagina laadt, ook als de kaarten zelf niet zichtbaar worden.
- Levert een pagina 0 films op, dan bewaart de app **wat de browser zag**: titel en tekst in het
  log, en een screenshot (link *📷 Wat zag de browser?* bij zoeken en bij een wachtende scanner).

## 0.6.4
- **Films op naam sneller en betrouwbaarder**: eerst de volledige filmlijst
  (`kinepolis.be/nl/films/`), stoppen zodra de film gevonden is, en de lijst 10 minuten
  onthouden. Oude pagina's uit de eerste versies worden overgeslagen (die vertraagden het
  zoeken tot het afgebroken werd).
- **Tikfouten** in de naam mogen: "duen part 3" vindt "Dune Part Three".
- Het log toont per pagina hoeveel films er gevonden zijn (`[catalogus] …`).

## 0.6.3
- **Stoelen aanklikken**: een gekozen stoel wordt nu ook herkend als de site hem anders toont
  (class of label i.p.v. `aria-pressed`). Bij twijfel geldt een actieve knop *Tickets selecteren*.
  Lukt het niet, dan staat in het log hoe de stoel eruitziet (`[stoelen] …`).
- **Films op naam**: lange filmlijsten (zoals `kinepolis.be/nl/films/`) worden helemaal
  doorgescrold, zodat ook films ver onderaan (bv. Dune Part Three) gevonden worden.
- **Telegram**: fout "chat_id is empty" bij het weghalen van de knoppen opgelost; de melding in
  de app wijst alleen nog naar je token als de token echt het probleem is.
- Knop **Bijwerken** weggehaald (Home Assistant laat een add-on zichzelf niet bijwerken).
  De add-on laat HA wel elk uur naar updates kijken; met *Auto update* gaat het vanzelf.

## 0.6.2
- Fix "kon geen geldige sleutel ophalen": de browser zoekt de Kinepolis-sleutel nu ook op een
  filmpagina, in de opslag van de pagina en in de antwoorden van Kinepolis.
- Meer varianten van de cookieknop herkend.
- Lukt het toch niet, dan staat in het log precies wat de browser zag (`[sleutel] …`).

## 0.6.1
- Bijwerken vanuit de app: onderaan **Scanners** zie je je versie en een knop **Bijwerken**.
- De add-on laat Home Assistant elk uur naar updates kijken (met *Auto update* gaat het vanzelf).
- Dit changelog.

## 0.6.0
- Fix "HTTP Error 401": de app vangt de toegangssleutel van Kinepolis op via de browser
  en vernieuwt hem als hij vervalt.

## 0.5.1
- Duidelijker log als Telegram onbereikbaar is (in plaats van "token niet herkend").

## 0.5.0
- Nieuwe Kinepolis-site: films met HO-code (bv. `web.kinepolis.be/films/…/HO00013277`),
  voorstellingen, zalen en formaten via de nieuwe API. Uitverkochte voorstellingen worden overgeslagen.
- Een drop zie je meteen: de eerste speeldag wordt telkens vers opgevraagd.
- Cinemanamen en filmtitels leert de app zelf; Leuven (`SL`) standaard erbij.

## 0.4.0
- Installeren en bijwerken via de GitHub-repository.
