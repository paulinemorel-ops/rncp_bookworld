from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import logging
import re

import pandas as pd
import requests
from bs4 import BeautifulSoup


#Définition des constantes pour les fichiers et l'URL du catalogue
CSV_VENTES = "base_de_donnees_bookworld.csv"
DB_REFERENCE = "bookworld_reference.db"
CATALOG_URL = "https://books.toscrape.com/"
PERSONAL_COLUMNS = ["customer_first_name", "customer_last_name"]

#Set up du logging pour afficher les messages d'information et d'erreur
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

class ExtractionError(Exception):
    """Levée lorsque l'extraction des données du catalogue échoue."""

# ------------------------------------------------------------------------------------------- #
# 1. Extraction #
# ------------------------------------------------------------------------------------------- #

def extract_sales():
    """Lit le csv des ventes brutes"""
    try:
        df_sales = pd.read_csv(CSV_VENTES)
    except FileNotFoundError:
        raise SystemExit(f"Erreur : fichier introuvable : {CSV_VENTES}")
    return df_sales


def extract_reference():
    """Lit les données de référence dans la base SQLite."""
    # sqlite3.connect crée une base vide si le fichier n'existe pas
    if not Path(DB_REFERENCE).exists():
        raise SystemExit(f"Erreur : base introuvable : {DB_REFERENCE}")

    # utiliser closing permet d'ouvrir et de fermer automatiquement la connection à la base sqlite qui doit obligatoirement être fermé.
    # cette fonction permet la fermeture même en cas d'échec du code.
    with closing(sqlite3.connect(DB_REFERENCE)) as con:
        df_pays = pd.read_sql_query(
            "SELECT country_code, country_name, currency_code, region, is_active FROM countries",
            con,
        )
        df_canaux = pd.read_sql_query(
            "SELECT channel_code, channel_name, channel_group, is_active FROM channels",
            con,
        )
        df_categories = pd.read_sql_query(
            "SELECT category_name, margin_rate, strategic_flag, default_channel_code, is_active FROM category_rules",
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

    return df_pays, df_canaux, df_pays_inactifs, df_pays_actifs, df_categories

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

def fetch_exchange_rate() -> dict:
    """Récupère le taux GBP -> EUR via l'API publique Frankfurter."""
    # Il n'y a pas de ventes hors UE, donc on ne gère pas d'autres devises pour l'instant.
    # Les pays europèens concernés par les ventes n'utilisent pas d'autres type de devises.
    last_exc: Exception | None = None
    for url, params in FRANKFURTER_ENDPOINTS:
        try:
            data = _http_get(url, params=params).json()
            rate = float(data["rates"]["EUR"])
            if rate <= 0:
                raise ValueError(f"taux invalide : {rate}")
            logger.info("Taux de change GBP->EUR : %s (date %s)", rate, data.get("date"))
            return {
                "rate_date": str(data["date"]),
                "base_currency": "GBP",
                "quote_currency": "EUR",
                "rate": rate,
            }
        except (ExtractionError, KeyError, TypeError, ValueError) as exc:
            last_exc = exc
            logger.warning("Endpoint Frankfurter inutilisable (%s) : %s", url, exc)
    raise ExtractionError("Taux de change indisponible") from last_exc


# ------------------------------------------------------------------------------------------- #
# 2. Transformation - Nettoyage - Implementation #
# ------------------------------------------------------------------------------------------- #
def clean_sales(df_sales: pd.DataFrame) -> pd.DataFrame:
    """Nettoie les ventes et retire les données personnelles (RGPD)."""
    # RGPD : les noms et prénoms ne sont pas nécessaires à l'usage final.
    df = df_sales.drop(columns=PERSONAL_COLUMNS, errors="ignore").copy()
    initial = len(df)

    # Retirer les lignes sans identifiant AVANT de convertir en texte : NaN deviendrait la chaîne "nan" et ne serait plus détecté.
    missing_ids = df["order_id"].isna() | df["book_id"].isna()
    if missing_ids.any():
        logger.warning("%d commande(s) sans identifiant écartée(s)", int(missing_ids.sum()))
    df = df[~missing_ids].copy() # Retirer les lignes sans identifiant

    for col in ["order_id", "book_id", "country_code", "channel_code", "book_name"]:
        df[col] = df[col].astype(str).str.strip()
    df["country_code"] = df["country_code"].str.upper()
    df["channel_code"] = df["channel_code"].str.upper()

    # Retirer les lignes avec identifiant vide s'il y a des espaces ou des chaînes vides
    empty_ids = (df["order_id"] == "") | (df["book_id"] == "")
    df = df[~empty_ids].copy() # Retirer les lignes avec identifiant vide

    # Retirer les doublons sur order_id, par défaut la première occurrence est conservée
    df = df.drop_duplicates(subset="order_id")

    # Nettoyage des colonnes numériques et date (les données invalides sont converties en NaN)
    df["order_date"] = pd.to_datetime(df["order_date"],format="%d/%m/%Y", errors="coerce")
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
    df["discount_rate"] = pd.to_numeric(df["discount_rate"], errors="coerce")

    # Retirer les lignes invalides : date manquante, quantité <= 0, taux de remise invalide
    invalid = (
        df["order_date"].isna()
        | df["quantity"].isna() | (df["quantity"] <= 0)
        | df["discount_rate"].isna() | ~df["discount_rate"].between(0, 1)
    )
    if invalid.any():
        logger.warning("%d commande(s) invalide(s) écartée(s)", int(invalid.sum()))
    df = df[~invalid].copy()

    df["quantity"] = df["quantity"].astype(int)
    df["order_date"] = df["order_date"].dt.strftime("%Y-%m-%d")
    logger.info("Nettoyage : %d -> %d commandes", initial, len(df))
    if df.empty:
        raise sqlite3.DataError("Aucune commande valide après nettoyage.")
    return df.reset_index(drop=True)

# Normalisation du titre pour la jointure entre les ventes et le catalogue : suppression des espaces superflus et mise en minuscule.
def normalize_title(title: str) -> str:
    """Clé de jointure tolérante : espaces et casse ignorés."""
    return re.sub(r"\s+", " ", str(title)).strip().casefold()

def enrich_sales(sales: pd.DataFrame, catalog: pd.DataFrame, rate: float) -> pd.DataFrame:
    """Ajoute le prix scrapé puis calcule le chiffre d'affaires en GBP et en EUR.

    revenue_gbp = prix_gbp x quantité x (1 - remise)
    revenue_eur = revenue_gbp x taux GBP->EUR
    """
    catalog = catalog.assign(title_key=catalog["title"].map(normalize_title))
    catalog = catalog.drop_duplicates("title_key")[["title_key", "price_gbp"]]

    df = sales.assign(title_key=sales["book_name"].map(normalize_title))
    df = df.merge(catalog, on="title_key", how="left").drop(columns="title_key")

    unmatched = df["price_gbp"].isna()
    if unmatched.all():
        raise sqlite3.DataError("Aucun livre des ventes ne correspond au catalogue scrapé.")
    if unmatched.any():
        titles = sorted(df.loc[unmatched, "book_name"].unique())
        logger.warning("%d commande(s) écartée(s), prix introuvable pour : %s",
                       int(unmatched.sum()), titles)
        df = df[~unmatched].copy()

    df["revenue_gbp"] = df["price_gbp"] * df["quantity"] * (1 - df["discount_rate"])
    df["revenue_eur"] = df["revenue_gbp"] * rate
    return df.reset_index(drop=True)

def run_pipeline():
    # Lecture du csv des ventes
    df_sales = extract_sales()
    print(f"Ventes : {len(df_sales)} lignes lues")

    # Lecture des données de référence (SQLite)
    df_pays, df_canaux, df_pays_inactifs, df_pays_actifs, df_categories = extract_reference()
    print(f"Pays : {len(df_pays)} | Canaux : {len(df_canaux)}")
    print(f"Pays inactifs : {len(df_pays_inactifs)} | Pays actifs : {len(df_pays_actifs)}")

    try:
        df_catalogue = scrape_catalog()
    except ExtractionError as exc:
        raise SystemExit(f"Erreur : {exc}") from exc

    return df_sales, df_pays, df_canaux, df_catalogue


if __name__ == "__main__":
    df_sales, df_pays, df_canaux, df_catalogue = run_pipeline()
    print("Pipeline exécuté avec succès.")
