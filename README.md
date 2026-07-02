# HLS Inspector

Version courante: `v1.1.0`

Application locale d’inventaire et d’analyse technique de pages autorisées. Elle détecte des ressources exposées dans le HTML ou les ressources liées, notamment des flux HLS, des vidéos et des documents/images courants.

## Release actuelle

Cette release apporte :

- un dashboard plus compact et plus lisible
- un mode historique en cartes par défaut et en tableau expert
- une carte "Dernière analyse" pour lire rapidement le dernier résultat
- des actions `Copier` et `Ouvrir` sur les URLs détectées
- un style visuel plus homogène, orienté analyse locale premium
- un README aligné sur le périmètre actuel de l’outil

## Avertissement légal

Cet outil doit être utilisé uniquement sur des pages et flux pour lesquels vous avez l’autorisation explicite d’analyse.
Il ne doit pas servir à contourner une protection, un DRM, une authentification, un paywall, ni à télécharger des films ou séries.
Il n’intègre aucun mécanisme de téléchargement, de contournement de sécurité, de scraping de tokens ou d’interception réseau.

## Description courte

HLS Inspector est un outil local d’inventaire technique pour pages autorisées. Il analyse le HTML et les ressources liées pour extraire les liens `.m3u8`, `.mp4`, `.pdf`, `.docx`, `.xlsx`, `.png`, `.jpg` et autres extensions courantes détectables sans exécuter de JavaScript distant.

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
5. Consulter le titre, l’URL de page, les ressources détectées, la date et le statut
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
- `GET /export/report/markdown` : rapport Markdown prêt à archiver

### Filtres d’historique

L’interface et l’API d’historique acceptent aussi un filtre `media`:

- `all`
- `streams`
- `videos`
- `documents`
- `images`
- `other`
- `empty`

## Limites de la V1

- Analyse HTML simple uniquement
- Pas de JavaScript distant exécuté
- Pas de Playwright, Selenium ou interception réseau
- Pas de téléchargement de segments `.ts`, `.m4s`, `.mp4`, `.pdf` ou images
- Pas de contournement DRM, login, token, cookie ou paywall
- Taille de réponse HTML limitée à environ 5 Mo

## Idées V2

- export Markdown de rapport
- statut d’analyse encore plus détaillé par source suivie
- journal local des performances et des erreurs réseau
- davantage de tests sur des pages réelles autorisées
- enrichissement du rapport avec des métadonnées vidéo supplémentaires quand elles sont disponibles
