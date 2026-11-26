import re
import sqlite3
from contextlib import closing
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

# ------------------------------------------------------------------------------------------- #
# Constantes #
# ------------------------------------------------------------------------------------------- #
CSV_VENTES = "base_de_donnees_bookworld.csv"
DB_REFERENCE = "bookworld_reference.db"
FINAL_DB = "bookworld_final.db"
SCHEMA_FILE = "schema_final.sql"
CATALOG_URL = "https://books.toscrape.com/"
FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest"

# Colonnes à supprimer pour le RGPD : les noms et prénoms ne servent pas à l'indicateur final
PERSONAL_COLUMNS = ["customer_first_name", "customer_last_name"]

# Ordre de chargement : les tables "dim_" doivent être remplies AVANT fact_orders,
# car fact_orders pointe vers elles (clés étrangères).
LOAD_ORDER = ["dim_country", "dim_channel", "dim_book", "fx_rates", "fact_orders", "sales_by_country"]

# La requête SQL d'agrégation
SQL_SALES_BY_COUNTRY = """
    SELECT
        f.country_code,
        c.country_name,
        COUNT(DISTINCT f.order_id)    AS total_orders,
        SUM(f.quantity)               AS total_quantity,
        ROUND(SUM(f.revenue_gbp), 2)  AS total_revenue_gbp,
        ROUND(SUM(f.revenue_eur), 2)  AS total_revenue_eur
    FROM fact_orders f
    LEFT JOIN dim_country c ON c.country_code = f.country_code
    GROUP BY f.country_code, c.country_name
    ORDER BY total_revenue_eur DESC
"""


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
    # sqlite3.connect crée une base vide si le fichier n'existe pas : on vérifie avant
    if not Path(DB_REFERENCE).exists():
        raise SystemExit(f"Erreur : base introuvable : {DB_REFERENCE}")

    # closing ferme automatiquement la connexion, même en cas d'erreur
    with closing(sqlite3.connect(DB_REFERENCE)) as con:
        df_pays = pd.read_sql_query(
            "SELECT country_code, country_name, currency_code, region, is_active FROM countries",
            con,
        )
        df_canaux = pd.read_sql_query(
            "SELECT channel_code, channel_name, channel_group, is_active FROM channels",
            con,
        )
        # Requête avec filtre : pays inactifs
        df_pays_inactifs = pd.read_sql_query(
            "SELECT country_code, country_name FROM countries WHERE is_active = 0",
            con,
        )

    return df_pays, df_canaux, df_pays_inactifs


def scrape_catalog():
    """Scrape la première page du catalogue : titre complet et prix en livres (£)."""
    try:
        response = requests.get(CATALOG_URL, timeout=10)
        response.raise_for_status()
    except requests.RequestException as erreur:
        raise SystemExit(f"Erreur : site inaccessible ({CATALOG_URL}) : {erreur}")

    # On donne response.content (et pas .text) pour éviter les problèmes d'encodage du symbole £
    soup = BeautifulSoup(response.content, "html.parser")

    livres = []
    for fiche in soup.select("article.product_pod"):
        # Le titre complet est dans l'attribut "title" du lien : le texte affiché est tronqué
        titre = fiche.select_one("h3 a")["title"]
        # Le prix est écrit "£51.77" : on garde uniquement le nombre
        texte_prix = fiche.select_one("p.price_color").get_text()
        prix = float(re.search(r"\d+\.\d+", texte_prix).group())
        livres.append({"title": titre, "price_gbp": prix})

    if not livres:
        raise SystemExit("Erreur : aucun livre trouvé sur la page (la structure du site a changé ?)")
    print(f"Catalogue : {len(livres)} livres récupérés")
    return pd.DataFrame(livres)


def fetch_exchange_rate():
    """Récupère le taux GBP -> EUR via l'API publique Frankfurter."""
    # Il n'y a pas de ventes hors UE, donc on ne gère pas d'autres devises pour l'instant.
    # Les pays européens concernés par les ventes n'utilisent pas d'autres types de devises.
    # L'ensemble des prix est exprimé en £
    try:
        response = requests.get(
            FRANKFURTER_URL, params={"base": "GBP", "symbols": "EUR"}, timeout=10
        )
        response.raise_for_status()
        data = response.json()
        taux = data["rates"]["EUR"]
        date_taux = data["date"]
    except (requests.RequestException, KeyError, ValueError) as erreur:
        raise SystemExit(f"Erreur : taux de change indisponible : {erreur}")

    print(f"Taux de change GBP -> EUR : {taux} (date {date_taux})")
    return {"rate_date": date_taux, "base_currency": "GBP", "quote_currency": "EUR", "rate": taux}


# ------------------------------------------------------------------------------------------- #
# 2. Transformation - Nettoyage - Enrichissement #
# ------------------------------------------------------------------------------------------- #

def clean_sales(df_sales):
    """Nettoie les ventes et retire les données personnelles (RGPD)."""
    # RGPD : on supprime les noms et prénoms dès le début
    df = df_sales.drop(columns=PERSONAL_COLUMNS)
    nb_avant = len(df)

    # Doublons : une commande ne doit apparaître qu'une seule fois
    df = df.drop_duplicates(subset="order_id")

    # Valeurs manquantes : on supprime les lignes incomplètes
    df = df.dropna()

    # Formats : espaces en trop, majuscules pour les codes, date au format AAAA-MM-JJ
    for colonne in ["order_id", "book_id", "country_code", "channel_code", "book_name"]:
        df[colonne] = df[colonne].str.strip()
    df["country_code"] = df["country_code"].str.upper()
    df["channel_code"] = df["channel_code"].str.upper()
    df["order_date"] = pd.to_datetime(df["order_date"], format="%Y-%m-%d", errors="coerce")

    # Valeurs incohérentes : date illisible, quantité <= 0, remise hors de 0 à 1
    valide = (
        df["order_date"].notna()
        & (df["quantity"] > 0)
        & df["discount_rate"].between(0, 1)
    )
    df = df[valide].copy()
    df["order_date"] = df["order_date"].dt.strftime("%Y-%m-%d")

    print(f"Nettoyage : {nb_avant} -> {len(df)} commandes")
    if df.empty:
        raise SystemExit("Erreur : aucune commande valide après nettoyage")
    return df.reset_index(drop=True)


def enrich_sales(df_sales, df_catalogue, taux):
    """Ajoute le prix du catalogue, puis calcule le chiffre d'affaires en £ et en €."""
    # On associe chaque vente à son prix grâce au titre du livre
    df = df_sales.merge(df_catalogue, left_on="book_name", right_on="title", how="left")
    df = df.drop(columns="title")

    # Livres de la vente absents du catalogue (page 1 seulement) : on prévient et on les écarte
    sans_prix = df["price_gbp"].isna()
    if sans_prix.any():
        print(f"Attention : {sans_prix.sum()} commande(s) sans prix dans le catalogue, écartée(s)")
        df = df[~sans_prix].copy()
    if df.empty:
        raise SystemExit("Erreur : aucun livre des ventes ne correspond au catalogue")

    df["revenue_gbp"] = df["price_gbp"] * df["quantity"] * (1 - df["discount_rate"])
    df["revenue_eur"] = df["revenue_gbp"] * taux
    return df


def aggregate_by_country(orders, dim_country):
    """Agrégation sales_by_country, calculée en SQL."""
    # SQL ne s'exécute que sur une base : on copie les deux DataFrames dans une base en mémoire
    with closing(sqlite3.connect(":memory:")) as con:
        orders.to_sql("fact_orders", con, index=False)
        dim_country.to_sql("dim_country", con, index=False)
        # On exécute la requête et on récupère le résultat dans un DataFrame
        return pd.read_sql_query(SQL_SALES_BY_COUNTRY, con)


def transform(df_sales, df_catalogue, rate_info, df_pays, df_canaux):
    """Produit l'ensemble des tables de la base finale."""
    sales = clean_sales(df_sales)
    orders = enrich_sales(sales, df_catalogue, rate_info["rate"])

    # Table des pays : le référentiel (sans is_active)
    dim_country = df_pays[["country_code", "country_name", "currency_code", "region"]]
    # Les Pays-Bas (NL) ont des ventes mais sont absents du référentiel : on les ajoute à la main
    if "NL" not in dim_country["country_code"].values:
        ligne_nl = pd.DataFrame([{
            "country_code": "NL", "country_name": "Netherlands",
            "currency_code": "EUR", "region": "Europe",
        }])
        dim_country = pd.concat([dim_country, ligne_nl], ignore_index=True)
        print("Attention : NL absent du référentiel, ajouté à la main (Netherlands)")

    dim_channel = df_canaux[["channel_code", "channel_name", "channel_group"]]

    dim_book = (
        orders[["book_id", "book_name", "price_gbp"]]
        .drop_duplicates("book_id")
        .rename(columns={"book_name": "title"})
        .sort_values("book_id")
    )

    fact_orders = orders[[
        "order_id", "order_date", "book_id", "country_code", "channel_code",
        "quantity", "discount_rate", "revenue_gbp", "revenue_eur",
    ]]

    return {
        "dim_country": dim_country,
        "dim_channel": dim_channel,
        "dim_book": dim_book,
        "fx_rates": pd.DataFrame([rate_info]),
        "fact_orders": fact_orders,
        "sales_by_country": aggregate_by_country(orders, dim_country),
    }


# ------------------------------------------------------------------------------------------- #
# 3. Chargement #
# ------------------------------------------------------------------------------------------- #

def load_final_db(tables):
    """Crée la base finale à partir de schema_final.sql, puis la remplit avec les tables."""
    # Le fichier schema_final.sql doit être dans le même dossier que pipeline.py
    if not Path(SCHEMA_FILE).exists():
        raise SystemExit(f"Erreur : schéma introuvable : {SCHEMA_FILE}")

    # On repart d'une base vide à chaque exécution : le pipeline peut être relancé sans problème
    Path(FINAL_DB).unlink(missing_ok=True)

    try:
        with closing(sqlite3.connect(FINAL_DB)) as con:
            # Active le contrôle des clés étrangères (désactivé par défaut dans SQLite)
            con.execute("PRAGMA foreign_keys = ON")

            # 1) Création des tables vides (le schéma ne contient AUCUNE colonne nominative : RGPD)
            con.executescript(Path(SCHEMA_FILE).read_text(encoding="utf-8"))

            # 2) Remplissage des tables dans le bon ordre
            for nom in LOAD_ORDER:
                tables[nom].to_sql(nom, con, if_exists="append", index=False)
                print(f"Table {nom} chargée : {len(tables[nom])} lignes")

            # 3) Vérification : chaque clé étrangère doit pointer vers une ligne qui existe
            if con.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("clés étrangères incohérentes")

            con.commit()
    except Exception as erreur:
        # En cas d'échec, on supprime la base à moitié remplie pour ne pas laisser un fichier faux
        Path(FINAL_DB).unlink(missing_ok=True)
        raise SystemExit(f"Erreur : chargement impossible dans {FINAL_DB} : {erreur}")


# ------------------------------------------------------------------------------------------- #
# Programme principal #
# ------------------------------------------------------------------------------------------- #

def run_pipeline():
    # Lecture du csv des ventes
    df_sales = extract_sales()
    print(f"Ventes : {len(df_sales)} lignes lues")

    # Lecture des données de référence (SQLite)
    df_pays, df_canaux, df_pays_inactifs = extract_reference()
    print(f"Pays : {len(df_pays)} | Canaux : {len(df_canaux)} | Pays inactifs : {len(df_pays_inactifs)}")

    # Pays inactifs qui ont quand même des ventes : on les garde, mais on prévient
    inactifs_avec_ventes = set(df_pays_inactifs["country_code"]) & set(df_sales["country_code"])
    if inactifs_avec_ventes:
        print(f"Attention : pays inactifs avec des ventes (conservées) : {sorted(inactifs_avec_ventes)}")

    # Site web et API de taux de change
    df_catalogue = scrape_catalog()
    rate_info = fetch_exchange_rate()

    # Nettoyage, enrichissement, agrégation, puis chargement dans la base finale
    tables = transform(df_sales, df_catalogue, rate_info, df_pays, df_canaux)
    load_final_db(tables)
    return tables


if __name__ == "__main__":
    tables = run_pipeline()
    print(tables["sales_by_country"].to_string(index=False))
    print(f"Base finale créée : {FINAL_DB}")
    print("Pipeline exécuté avec succès.")
