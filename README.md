# 🏡 Huizenjacht

> **Multi-agent system om samen met je partner jullie droomhuis in de Vlaamse Ardennen te vinden.**

Zoeken naar een hoeve of landelijk pand is tijdrovend: elke dag meerdere immowebsites doorspitten, sociale media checken én uitzoeken wat de overheid toelaat op een bepaald perceel. Huizenjacht automatiseert dat volledig.

---

## Hoe het werkt

```
Elke ochtend om 07:00
        │
        ▼
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  Scraper Agents  │────▶│ Enrichment Agents│────▶│ Notification     │
│                  │     │                  │     │                  │
│ • Immoweb        │     │ • Overheidsdata  │     │ • E-mail digest  │
│ • Zimmo          │     │   (Geopunt,      │     │   naar           │
│ • Realo          │     │    bestemmings-  │     │   jonathan.      │
│ • Logic Immo     │     │    zone, vloeden,│     │   dhaene@        │
│ • Lokale immo-   │     │    erfgoed)      │     │   gmail.com      │
│   sites Vlaamse  │     │                  │     │                  │
│   Ardennen       │     │ • AI Analyse     │     │ • Match e-mail   │
│ • Facebook /     │     │   (score 0–10,   │     │   wanneer jullie │
│   sociale media  │     │    pro's/con's,  │     │   beiden ❤️      │
└──────────────────┘     │    aanbevelingen)│     │   geven          │
                         └──────────────────┘     └──────────────────┘
                                  │
                                  ▼
                    ┌─────────────────────────┐
                    │  docs/data/             │
                    │  properties.json        │◀── gecommit door Actions
                    │  likes.json             │◀── bijgewerkt door web app
                    └─────────────────────────┘
                                  │
                                  ▼
                    ┌─────────────────────────┐
                    │  📱 GitHub Pages        │
                    │  Mobiele web app        │
                    │                         │
                    │  Jonathan  ❤️  ❤️       │
                    │  Partner   ❤️            │
                    │            ↓             │
                    │        🎉 MATCH!         │
                    └─────────────────────────┘
```

---

## Zoekcriteria

| Criterium | Waarde |
|---|---|
| Regio | Vlaamse Ardennen |
| Postcodes | 9600, 9620, 9630, 9660, 9680, 9688, 9690, 9700, 9750, 9770, 9790 |
| Max. prijs | € 600.000 |
| Min. slaapkamers | 3 |
| Min. perceeloppervlakte | 5.000 m² |
| Trefwoorden | landelijk, hoeve, boerderij, weiland, stal, schuur, B&B, gastenverblijf, agrarisch |

---

## Kan dit draaien op GitHub Pages?

**Ja — het systeem draait volledig gratis binnen GitHub:**

| Component | Draait op | Kosten |
|---|---|---|
| Dagelijkse scraping & e-mail | **GitHub Actions** (cron) | Gratis (2.000 min/maand) |
| Mobiele web app | **GitHub Pages** (statische hosting) | Gratis |
| Likes & matches opslaan | **GitHub repository** (JSON via API) | Gratis |
| AI-analyse | OpenAI API (optioneel) | Pay-per-use of gratis fallback |

> **Geen server, geen database, geen maandelijkse kosten.**

---

## Installatie & Setup (eenmalig)

### 1. Fork of clone deze repository

```bash
git clone https://github.com/jonathandhaene/Huizenjacht.git
cd Huizenjacht
```

### 2. Zet GitHub Pages aan

Ga naar **Settings → Pages** in je repository:
- Source: **Deploy from a branch**
- Branch: `main` / folder: `docs`

De web app is dan bereikbaar op:
`https://jonathandhaene.github.io/Huizenjacht/`

### 3. Configureer GitHub Actions Secrets en Variables

Ga naar **Settings → Secrets and variables → Actions**:

**Secrets** (gevoelige waarden):
| Naam | Waarde |
|---|---|
| `SMTP_USERNAME` | je Gmail-adres (bijv. `jij@gmail.com`) |
| `SMTP_PASSWORD` | [Gmail App Password](https://support.google.com/accounts/answer/185833) (niet je gewone wachtwoord) |
| `OPENAI_API_KEY` | OpenAI API-sleutel *(optioneel — zonder werkt de rule-based scorer)* |

**Variables** (niet-gevoelige waarden):
| Naam | Standaard | Omschrijving |
|---|---|---|
| `NOTIFICATION_EMAIL` | `jonathan.dhaene@gmail.com` | E-mailadres voor dagelijkse digest |
| `SMTP_HOST` | `smtp.gmail.com` | SMTP server |
| `SMTP_PORT` | `587` | SMTP poort |
| `USER1_NAME` | `Jonathan` | Naam partner 1 (getoond in de app) |
| `USER2_NAME` | *(leeg)* | Naam partner 2 |
| `MAX_PRICE` | `600000` | Max. vraagprijs in EUR |
| `MIN_BEDROOMS` | `3` | Min. slaapkamers |
| `MIN_LAND_AREA` | `5000` | Min. perceeloppervlakte in m² |
| `SEARCH_POSTAL_CODES` | `9600,9620,...` | Kommagescheiden postcodes |

### 4. Zet de web app op op je smartphone

**Stap 1:** Open de GitHub Pages URL op je smartphone:
`https://jonathandhaene.github.io/Huizenjacht/`

**Stap 2:** Maak een GitHub Personal Access Token aan:
1. Ga naar [github.com/settings/tokens/new](https://github.com/settings/tokens/new?scopes=repo&description=Huizenjacht)
2. Geef het token een naam: `Huizenjacht`
3. Selecteer bereik: **repo** (volledige toegang)
4. Klik **Generate token** en kopieer het (begint met `ghp_`)

**Stap 3:** Vul in de setup-pagina van de app:
- Jouw naam (bijv. `Jonathan`)
- Het token dat je zojuist aanmaakte

**Stap 4:** Voeg de app toe aan je startscherm:
- **iPhone (Safari):** Deel-knop → "Zet op beginscherm"
- **Android (Chrome):** Menu → "Toevoegen aan startscherm"

**Herhaal stappen 1–4 op het toestel van je partner** met haar/zijn naam en een eigen token.

---

## Dagelijks gebruik

### Panden bekijken
- Open de app → tab **Panden**
- Filter op: Alle panden / Nieuw vandaag / Mijn likes / Matches / **🗑️ Prullenbak**
- Tik op een kaart voor alle details (foto's, overheidsdata, AI-analyse)
- **Swipe rechts** (touch) om te liken, **swipe links** om te verbergen

### Foto's bekijken (lightbox)
- Tik op een foto voor de volledige weergave
- Navigeer met pijltjes of swipe links/rechts
- Sluit met ✕ of de Escape-toets

### Een pand liken
- Tik het ❤️ op een kaart of in de detailweergave
- Je like wordt onmiddellijk gesynchroniseerd naar GitHub

### Wanneer jullie allebei liken → 🎉 Match!
- De app toont een match-notificatie zodra de tweede persoon liket
- Jullie ontvangen ook een **match-e-mail** de volgende ochtend

### Afbeeldingen prullenbak
- Tik 🗑️ op een kaart om de afbeeldingen naar de prullenbak te verplaatsen
- Herstel via ♻️ op de kaart of via het detail-menu
- Beheer alle prullenbakken via **Instellingen → 🗑️ Prullenbak beheer**:
  - Per pand herstellen of leegmaken
  - Selecteer meerdere panden en leeg ze samen
  - "Alles verwijderen" voor een globale schoonmaak
- **Na 14 dagen** worden prullenbakitems automatisch verwijderd door de dagelijkse pipeline

### Wat zie je per pand?
Elk pand wordt automatisch verrijkt met:
- **Lokaal gecachete foto's**: afbeeldingen opgeslagen in `docs/data/images/` voor betrouwbare weergave
- **Overheidsgegevens** (Geopunt / AGIV): bestemmingszone, agrarisch gebied, overstromingsrisico, erfgoedbescherming
- **AI-score** (0–10): hoe goed past dit pand bij jullie criteria?
- **Pro's & con's** + aanbevolen vervolgstappen (bijv. RUP-attest opvragen, omgevingsloket checken)

---

## Afbeeldingen cachen

De dagelijkse pipeline downloadt automatisch afbeeldingen van nieuwe panden en slaat ze op als:
```
docs/data/images/<property-id>/<url-hash>.<ext>
```

- Maximaal 5 afbeeldingen per pand worden gecached
- Eenmaal gecached worden afbeeldingen niet opnieuw gedownload
- Gedownloade afbeeldingen blijven beschikbaar ook als de originele bron niet meer bereikbaar is
- De paden worden opgeslagen in `images_local` in `properties.json`

---

## Projectstructuur

```
Huizenjacht/
│
├── .github/workflows/
│   └── scrape.yml              # Dagelijkse GitHub Actions cron job
│
├── agents/
│   ├── scrapers/
│   │   ├── base.py             # HTTP client basisklasse
│   │   ├── immoweb.py          # Immoweb scraper
│   │   ├── zimmo.py            # Zimmo scraper
│   │   ├── realo.py            # Realo scraper
│   │   ├── logic_immo.py       # Logic Immo scraper
│   │   ├── local_immo.py       # Lokale immokantoren Vlaamse Ardennen
│   │   └── social_media.py     # Publieke Facebook-groepen
│   ├── enrichment/
│   │   ├── government.py       # Geopunt / Vlaams overheidsdata
│   │   └── ai_analyzer.py      # OpenAI of rule-based scorer
│   ├── notification/
│   │   └── email_agent.py      # HTML e-mail digest
│   └── orchestrator.py         # Coördineert alle agents (klassieke modus)
│
├── config/
│   └── settings.py             # Pydantic Settings (laadt .env)
│
├── docs/                       ← GitHub Pages root
│   ├── index.html              # Mobiele web app (SPA)
│   ├── style.css               # Mobile-first CSS
│   ├── app.js                  # Vanilla JS app
│   ├── manifest.json           # PWA manifest (voeg toe aan startscherm)
│   └── data/
│       ├── properties.json     # Panden (bijgewerkt door Actions)
│       ├── likes.json          # Likes (bijgewerkt door web app via GitHub API)
│       ├── trash.json          # Prullenbak metadata (14-dagen retentie)
│       └── images/             # Lokaal gecachete foto's (bijgewerkt door Actions)
│           └── <property-id>/
│               └── <hash>.jpg
│
├── models/
│   └── property.py             # Pydantic datamodellen
│
├── scheduler/
│   └── daily_runner.py         # Dagelijkse scheduler (klassieke modus)
│
├── scripts/
│   ├── github_pages_pipeline.py  # Pipeline voor GitHub Actions modus
│   ├── image_cache.py            # Afbeeldingen downloaden en lokaal cachen
│   └── trash_manager.py          # Prullenbak beheer (trash/restore/purge)
│
├── tests/                      # Pytest-testsuit (112 tests)
│
├── main.py                     # Hoofdentry-point
├── requirements.txt
└── .env.example                # Voorbeeldconfiguratie
```

---

## Lokaal draaien (optioneel)

```bash
# Installeer afhankelijkheden
pip install -r requirements.txt

# Kopieer en vul configuratie in
cp .env.example .env
# → Pas SMTP_USERNAME, SMTP_PASSWORD, OPENAI_API_KEY in .env aan

# Eénmalig uitvoeren (dry run — geen e-mail)
python main.py --run-now --dry-run

# GitHub Pages modus (sla op naar docs/data/properties.json)
python main.py --github-pages

# Start de dagelijkse scheduler (draait continu)
python main.py --schedule

# Tests uitvoeren
python -m pytest tests/ -v
```

---

## GitHub Actions: handmatig triggeren

Wil je de scan nu uitvoeren zonder te wachten tot 07:00?

1. Ga naar de **Actions** tab in je repository
2. Selecteer **🏡 Dagelijkse Huizenjacht**
3. Klik **Run workflow**

---

## Veelgestelde vragen

**Mijn token werkt niet.**
Zorg dat je bij het aanmaken van het token het bereik **repo** hebt geselecteerd. Fine-grained tokens met enkel `contents:write` werken ook.

**Ik zie geen panden.**
De eerste run van Actions moet eerst uitvoeren. Trigger handmatig via de Actions tab, of wacht tot de volgende ochtend om 07:00.

**De overheidsdata is leeg.**
Dit is normaal als het adres niet precies gevonden wordt door de geocoder. De AI-scorer werkt ook zonder overheidsdata.

**Kan ik zoekcriteria aanpassen?**
Ja — via GitHub Actions Variables (zie [stap 3](#3-configureer-github-actions-secrets-en-variables)).

**Werkt dit ook als mijn repo privé is?**
GitHub Pages vereist een betaald plan voor privé-repositories. De rest (Actions, opslag) werkt wel. Maak de repo publiek voor volledige gratis werking, of gebruik een GitHub Pro account.

---

## Licentie

MIT — vrij te gebruiken en aan te passen.
