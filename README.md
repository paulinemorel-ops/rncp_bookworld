# BookWorld – Pipeline de données et API

Projet data complet pour BookWorld (librairie en ligne fictive) : collecte de plusieurs sources, construction d'un jeu de données propre des ventes par pays, base SQL finale conforme au RGPD, et API REST pour exposer l'indicateur **ventes agrégées par pays**.

```
CSV de ventes ──────────┐
Base SQLite référentiel ─┤
books.toscrape.com ──────┼──► pipeline.py ──► bookworld_final.db ──► api.py ──► GET /sales-by-country
API Frankfurter (GBP→EUR)┘
```

## 1. Installation

Prérequis : Python 3.10 ou supérieur.

```bash
git clone <url-du-depot>
cd bookworld

python -m venv .venv
source .venv/bin/activate          # Windows PowerShell : .venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

## 2. Exécuter le pipeline

```bash
python pipeline.py
```

Le pipeline :

1. lit `sales_raw.csv` et la base `bookworld_reference.db` ;
2. scrape la première page de <https://books.toscrape.com/> (titres et prix en £) ;
3. récupère le taux GBP → EUR via l'API publique [Frankfurter](https://frankfurter.dev) ;
4. nettoie les ventes, **supprime les noms et prénoms (RGPD)**, calcule le chiffre d'affaires
   (`prix × quantité × (1 − remise)`) en £ puis en €, et agrège par pays ;
5. crée la base finale `bookworld_final.db` à partir de `schema_final.sql`.

Il faut un accès Internet (scraping + taux de change). En cas d'échec (site ou API inaccessible), le pipeline s'arrête avec un message d'erreur explicite (délai d'attente de 10 s, une seule tentative), sans laisser de base partielle.

Choix de gestion des données (détaillés dans le rapport) :

- **NL** est absent de la table `countries` : son nom (« Netherlands ») est codé en dur.
- **PT** est inactif dans le référentiel mais ses commandes sont conservées (ventes réelles).
- Une commande dont le livre n'est pas trouvé dans le catalogue scrapé est écartée (message d'avertissement).

## 3. Lancer l'API

L'API refuse de démarrer sans token. Définissez-le dans une variable d'environnement :

```bash
# Linux / macOS
export API_TOKEN="remplacez-par-un-secret-long-et-aleatoire"
# Windows PowerShell
$env:API_TOKEN = "remplacez-par-un-secret-long-et-aleatoire"

python api.py          # écoute sur http://127.0.0.1:8000
```

Exécutez d'abord `python pipeline.py` : l'API lit la table `sales_by_country` de `bookworld_final.db`.

### Endpoints

| Méthode | Route | Authentification | Description |
|---|---|---|---|
| GET | `/health` | Non | Indique que l'API répond |
| GET | `/sales-by-country` | Oui (Bearer) | Ventes agrégées par pays |

## 4. Utiliser l'authentification par token (API)

Le token est envoyé dans l'en-tête `Authorization` :

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}

curl -H "Authorization: Bearer $API_TOKEN" http://127.0.0.1:8000/sales-by-country
# {"count":8,"data":[{"country_code":"FR","country_name":"France","total_orders":56,...}, ...]}

curl -i http://127.0.0.1:8000/sales-by-country
# HTTP/1.1 401 UNAUTHORIZED   (token absent ou invalide)
```

Codes de retour : `200` succès, `401` token manquant ou invalide, `503` base finale indisponible (lancez d'abord `pipeline.py`).

## 5. Publier sur GitHub : token ou clé SSH

Deux méthodes d'authentification pour `git push` (GitHub n'accepte plus le mot de passe du compte) :

**Option A – Personal Access Token (HTTPS)**
1. GitHub → *Settings* → *Developer settings* → *Personal access tokens* → générer un token avec le droit `repo`.
2. Lors du `git push`, utiliser le token à la place du mot de passe.

```bash
git init
git add .
git commit -m "Projet BookWorld"
git branch -M main
git remote add origin https://github.com/<utilisateur>/<depot>.git
git push -u origin main
```

**Option B – Clé SSH**
```bash
ssh-keygen -t ed25519 -C "votre-email@exemple.com"
cat ~/.ssh/id_ed25519.pub        # à copier dans GitHub → Settings → SSH and GPG keys
ssh -T git@github.com            # vérification

git remote add origin git@github.com:<utilisateur>/<depot>.git
git push -u origin main
```

Ne jamais committer un token ou une clé privée (le fichier `.env` est ignoré par `.gitignore`).

## 6. Fichiers du projet

| Fichier | Rôle |
|---|---|
| `pipeline.py` | Extraction, nettoyage, enrichissement, agrégation et chargement |
| `queries.sql` | Requêtes SQL d'extraction sur la base de référence (version finale lisible) |
| `schema_final.sql` | Schéma de la base finale (sans donnée nominative) |
| `api.py` | API REST Flask avec authentification par token |
| `sales_raw.csv` | Ventes brutes (source) |
| `bookworld_reference.db` | Référentiels métier SQLite (pays, canaux, catégories) |
| `bookworld_final.db` | Base finale générée par `pipeline.py` |
| `requirements.txt` | Dépendances Python |

### Tables de la base finale

`dim_country`, `dim_channel`, `dim_book`, `fx_rates`, `fact_orders` (une ligne par commande), `sales_by_country` (agrégation : `country_code`, `country_name`, `total_orders`, `total_quantity`, `total_revenue_gbp`, `total_revenue_eur`).

## 7. RGPD

Les colonnes `customer_first_name` et `customer_last_name` du CSV brut sont supprimées dès l'étape de nettoyage : elles ne sont pas nécessaires à l'usage final (ventes agrégées par pays) et n'existent dans aucune table de la base finale (principe de minimisation, art. 5.1.c du RGPD).
