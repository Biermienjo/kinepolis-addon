# Changelog

## 0.6.3
- Knop **Bijwerken** weggehaald (Home Assistant laat een add-on zichzelf niet bijwerken).
  De add-on laat HA nog wel elk uur naar updates kijken; met *Auto update* gaat het vanzelf.

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
