-- =============================================================================
-- schema_final.sql : schéma de la base finale (bookworld_final.db)
--
-- RGPD (minimisation des données, art. 5.1.c) : les colonnes customer_first_name
-- et customer_last_name présentes dans les données brutes ne sont PAS recréées
-- ici. Aucune table de la base finale ne contient de donnée nominative.
-- =============================================================================

PRAGMA foreign_keys = ON;

-- Suppression dans l'ordre inverse des dépendances (le schéma est rejouable)
DROP TABLE IF EXISTS sales_by_country;
DROP TABLE IF EXISTS fact_orders;
DROP TABLE IF EXISTS fx_rates;
DROP TABLE IF EXISTS dim_book;
DROP TABLE IF EXISTS dim_channel;
DROP TABLE IF EXISTS dim_country;

-- Dimension pays (issue de la base de référence + valeurs de repli, ex. NL)
CREATE TABLE dim_country (
    country_code  TEXT PRIMARY KEY CHECK (length(country_code) = 2),
    country_name  TEXT NOT NULL,
    currency_code TEXT,
    region        TEXT
);

-- Dimension canal de vente
CREATE TABLE dim_channel (
    channel_code  TEXT PRIMARY KEY,
    channel_name  TEXT NOT NULL,
    channel_group TEXT NOT NULL
);

-- Dimension livre (titre et prix en livres issus du scraping)
CREATE TABLE dim_book (
    book_id   TEXT PRIMARY KEY,
    title     TEXT NOT NULL,
    price_gbp REAL NOT NULL CHECK (price_gbp >= 0)
);

-- Taux de change utilisé pour la conversion GBP -> EUR (traçabilité)
CREATE TABLE fx_rates (
    rate_date      TEXT NOT NULL,
    base_currency  TEXT NOT NULL,
    quote_currency TEXT NOT NULL,
    rate           REAL NOT NULL CHECK (rate > 0),
    PRIMARY KEY (rate_date, base_currency, quote_currency)
);

-- Table de faits : une ligne par commande, sans donnée nominative
CREATE TABLE fact_orders (
    order_id      TEXT PRIMARY KEY,
    order_date    TEXT NOT NULL,
    book_id       TEXT NOT NULL REFERENCES dim_book(book_id),
    country_code  TEXT NOT NULL REFERENCES dim_country(country_code),
    channel_code  TEXT NOT NULL REFERENCES dim_channel(channel_code),
    quantity      INTEGER NOT NULL CHECK (quantity > 0),
    discount_rate REAL NOT NULL CHECK (discount_rate BETWEEN 0 AND 1),
    revenue_gbp   REAL NOT NULL,
    revenue_eur   REAL NOT NULL
);

CREATE INDEX idx_fact_orders_country ON fact_orders(country_code);
CREATE INDEX idx_fact_orders_book    ON fact_orders(book_id);

-- Agrégation exposée par l'API : ventes par pays
CREATE TABLE sales_by_country (
    country_code      TEXT PRIMARY KEY REFERENCES dim_country(country_code),
    country_name      TEXT NOT NULL,
    total_orders      INTEGER NOT NULL,
    total_quantity    INTEGER NOT NULL,
    total_revenue_gbp REAL NOT NULL,
    total_revenue_eur REAL NOT NULL,
    avg_basket_eur    REAL NOT NULL,
    pct_quantity      REAL NOT NULL
);
