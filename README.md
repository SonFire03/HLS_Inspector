# HLS Inspector

Version initiale: `v1.0.0`

Application locale d’inventaire et d’analyse technique de pages vidéo autorisées contenant des flux HLS et, quand ils sont exposés dans le HTML ou les ressources liées, des liens `.mp4`.

## Avertissement légal

Cet outil doit être utilisé uniquement sur des pages et flux pour lesquels vous avez l’autorisation explicite d’analyse.
Il ne doit pas servir à contourner une protection, un DRM, une authentification, un paywall, ni à télécharger des films ou séries.
Il n’intègre aucun mécanisme de téléchargement, de contournement de sécurité, de scraping de tokens ou d’interception réseau.

## Description courte

HLS Inspector est un outil local d’inventaire technique pour pages vidéo autorisées. Il analyse le HTML et les ressources liées pour extraire les liens `.m3u8` et `.mp4` détectables sans exécuter de JavaScript distant.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Lancement

```bash
python app.py
```

L’application écoute par défaut sur `http://127.0.0.1:5000`.

## Usage

1. Ouvrir `http://127.0.0.1:5000`
2. Coller une URL HTTP ou HTTPS autorisée
3. Ou déposer un texte / fichier `.txt` contenant une ou plusieurs URLs
4. Cliquer sur `Analyser`
5. Consulter le titre, l’URL de page, les liens `.m3u8`, les liens `.mp4`, la date et le statut
6. Exporter l’historique en JSON ou CSV si nécessaire

## Routes disponibles

- `GET /` : interface web locale
- `GET /analysis/<id>` : page dédiée de détail d’analyse
- `POST /api/analyze` : analyse une URL
- `GET /api/history` : retourne l’historique groupé avec filtres et pagination
- `DELETE /api/history/<id>` : supprime une analyse complète
- `DELETE /api/history` : vide l’historique
- `GET /export/json` : export complet JSON
- `GET /export/csv` : export complet CSV
- `GET /export/detail/json` : export détaillé JSON
- `GET /export/detail/csv` : export détaillé CSV
- `GET /export/report/html` : rapport HTML prêt à partager

## Limites de la V1

- Analyse HTML simple uniquement
- Pas de JavaScript distant exécuté
- Pas de Playwright, Selenium ou interception réseau
- Pas de téléchargement de segments `.ts`, `.m4s` ou `.mp4`
- Pas de contournement DRM, login, token, cookie ou paywall
- Taille de réponse HTML limitée à environ 5 Mo

## Idées V2

- export Markdown de rapport
- statut d’analyse encore plus détaillé par source suivie
- journal local des performances et des erreurs réseau
- davantage de tests sur des pages réelles autorisées
- enrichissement du rapport avec des métadonnées vidéo supplémentaires quand elles sont disponibles
