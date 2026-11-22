# Wecker für Home Assistant

Wecker für die ganze Familie mit eigener Seite in der Seitenleiste. Jede Person stellt ihre Wecker selbst, Eltern sehen und verwalten alle.

## Funktionen

- **Profile je Person.** Ein Profil ist mit einem HA-Benutzer verknüpft. Kinder sehen nur ihre eigenen Wecker, Eltern und Administratoren alle; oben wird zwischen den Profilen umgeschaltet.
- **Beliebig viele Wecker** je Person. Mögliche Zeitpläne:
  - Wochentage
  - Werktage (über den Werktag-Sensor, also ohne Wochenende und Feiertage)
  - einmalig an einem Datum
  - „nächsten überspringen“
- **Ausnahme-Kalender:** An Tagen mit einem Termin im Kalender klingelt es nicht, z. B. in den Schulferien oder im Urlaub.
- **Ton** auf jedem Lautsprecher:
  - Alexa: per Sprachbefehl, z. B. „{sender} auf TuneIn“
  - Music Assistant: Sender-URL oder Music-Assistant-Link
  - andere `media_player`: Stream-URL

  Startlautstärke, langsamer Anstieg und eine Höchstdauer sind einstellbar.
- **Sonnenaufgang:** Lampen werden vor der Weckzeit langsam heller (Helligkeit und Farbtemperatur einstellbar).
- **Rollläden** öffnen und **Skript** starten beim Klingeln.
- **Vorlauf-Ereignis** `wecker_vorlauf` X Minuten vorher, z. B. um das Bad zu heizen oder die Kaffeemaschine einzuschalten.
- **Stopp und Schlummern** über die Seite, Tasten-Entitäten, Push-Aktionen der Companion App oder Dienste (also auch über Sprachassistenten-Routinen).
- **Ersatz-Push**, wenn der Lautsprecher nicht erreichbar ist.
- **Export und Import** aller Daten als JSON, zum Sichern oder für eine neue Installation.
- **Übernahme** eines bisherigen Helfer-Weckers (`input_boolean.wecker_<person>_<n>_aktiv` usw.). Die alten Helfer bleiben unverändert, und die übernommenen Wecker sind zunächst aus, damit beide parallel laufen können.

## Entitäten je Profil

| Entität | Zweck |
|---|---|
| `sensor.wecker_<name>_naechster_wecker` | nächste Weckzeit (Zeitstempel) |
| `switch.wecker_<name>_wecker_aktiv` | Hauptschalter der Person (z. B. Urlaub) |
| `button.wecker_<name>_stopp` / `_schlummern` | Stopp, Schlummern |
| `binary_sensor.wecker_<name>_klingelt` | an, solange es klingelt oder schlummert |

Die genauen Entitäts-IDs hängen von der Sprache und vom Profilnamen ab.

## Dienste

`wecker.stoppen`, `wecker.schlummern` (jeweils optional mit `profil`), `wecker.ausloesen` (`profil`, `wecker`, `test`) und `wecker.ueberspringen` (`profil`, optional `wecker`).

## Ereignisse

`wecker_klingelt`, `wecker_schlummert`, `wecker_beendet` und `wecker_vorlauf`. Sie enthalten `profil`, `name`, `wecker_id` und `wecker`.

## Installation

1. In Home Assistant unter **Einstellungen → Marketplace** dieses Repository als benutzerdefiniertes Repository (Typ Integration) hinzufügen.
2. **Wecker** installieren und HA neu starten.
3. Unter **Einstellungen → Geräte & Dienste → Integration hinzufügen** „Wecker“ einrichten.
4. Auf der Seite **Wecker** unter Einstellungen Profile und Sender anlegen.

## Lizenz

MIT
