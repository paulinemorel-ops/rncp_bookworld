import sqlite3
from contextlib import closing
from pathlib import Path

import logging
import re

import pandas as pd
import requests
from bs4 import BeautifulSoup

CSV_VENTES = "sales_raw.csv"
DB_REFERENCE = "bookworld_reference.db"
CATALOG_URL = "https://books.toscrape.com/"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class ExtractionError(Exception):
    """Levée lorsque l'extraction des données du catalogue échoue."""

def extract_sales():
    """Lit le csv des ventes brutes"""
    try:
        df_ventes = pd.read_csv(CSV_VENTES)
    except FileNotFoundError:
        raise SystemExit(f"Erreur : fichier introuvable : {CSV_VENTES}")
    return df_ventes


def extract_reference():
    """Lit les données de référence dans la base SQLite."""
    # sqlite3.connect crée une base vide si le fichier n'existe pas : on vérifie avant
    if not Path(DB_REFERENCE).exists():
        raise SystemExit(f"Erreur : base introuvable : {DB_REFERENCE}")

    with closing(sqlite3.connect(DB_REFERENCE)) as con:
        df_pays = pd.read_sql_query(
            "SELECT country_code, country_name, currency_code, region FROM countries",
            con,
        )
        df_canaux = pd.read_sql_query(
            "SELECT channel_code, channel_name, channel_group FROM channels",
            con,
        )
        # Requête avec filtre : pays inactifs
        df_pays_inactifs = pd.read_sql_query(
            "SELECT country_code, country_name FROM countries WHERE is_active = 0",
            con,
        )
        # Requête avec filtre : pays actifs
        df_pays_actifs = pd.read_sql_query(
            "SELECT country_code, country_name FROM countries WHERE is_active = 1",
            con,
        )
    return df_pays, df_canaux, df_pays_inactifs, df_pays_actifs

def _http_get(url: str, timeout: int = 10) -> requests.Response:
    """Effectue une requête HTTP GET avec gestion des erreurs réseau et statuts HTTP."""
    try:
        response = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (compatible; CatalogScraper/1.0)"},
        )
        response.raise_for_status()
        return response
    except requests.RequestException as exc:
        raise ExtractionError(f"Échec de la requête HTTP vers {url} : {exc}") from exc

def scrape_catalog(url: str = CATALOG_URL) -> pd.DataFrame:
    """Scrape la première page du catalogue : titre complet et prix en livres.

    Le titre complet est lu dans l'attribut `title` du lien : le texte affiché
    est tronqué par le site pour les titres longs.
    """
    response = _http_get(url)
    soup = BeautifulSoup(response.content, "html.parser")

    rows = []
    for pod in soup.select("article.product_pod"):
        link = pod.select_one("h3 a")
        price_tag = pod.select_one("p.price_color")
        if link is None or price_tag is None:
            logger.warning("Fiche livre incomplète ignorée")
            continue
        match = re.search(r"\d+(?:\.\d+)?", price_tag.get_text())
        if match is None:
            logger.warning("Prix illisible pour %r ignoré", link.get("title"))
            continue
        rows.append({"title": link.get("title", "").strip(), "price_gbp": float(match.group())})

    if not rows:
        raise ExtractionError("Aucun livre trouvé : la structure de la page a peut-être changé.")
    logger.info("Scraping : %d livres récupérés", len(rows))
    return pd.DataFrame(rows)

def run_pipeline():
    # Lecture du csv des ventes
    df_ventes = extract_sales()
    print(f"Ventes : {len(df_ventes)} lignes lues")

    # Lecture des données de référence (SQLite)
    df_pays, df_canaux, df_pays_inactifs, df_pays_actifs = extract_reference()
    print(f"Pays : {len(df_pays)} | Canaux : {len(df_canaux)}")
    print(f"Pays inactifs : {len(df_pays_inactifs)} | Pays actifs : {len(df_pays_actifs)}")

    try:
        df_catalogue = scrape_catalog()
    except ExtractionError as exc:
        raise SystemExit(f"Erreur : {exc}") from exc

    return df_ventes, df_pays, df_canaux, df_catalogue


if __name__ == "__main__":
    df_ventes, df_pays, df_canaux, df_catalogue = run_pipeline()
    print("Pipeline exécuté avec succès.")