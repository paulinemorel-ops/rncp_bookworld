-- queries.sql : requêtes utilisées par pipeline.py
-- Q1 à Q4 lisent la base de référence (bookworld_reference.db). Q5 agrège les ventes.

-- Q1 : enrichissement des ventes avec les pays
-- Aucun filtre sur is_active : les ventes d'un pays inactif (ex. PT) restent des ventes réelles.
SELECT country_code, country_name, currency_code, region, is_active
FROM countries;

-- Q2 : enrichissement des ventes avec les canaux de vente
SELECT channel_code, channel_name, channel_group, is_active
FROM channels;

-- Q3 : requête avec filtre : pays inactifs
SELECT country_code, country_name
FROM countries
WHERE is_active = 0;

-- Q4 : requête avec filtre : pays actifs
SELECT country_code, country_name
FROM countries
WHERE is_active = 1;

-- Q5 : agrégation sales_by_country
-- Exécutée sur une base SQLite en mémoire contenant fact_orders et dim_country.
SELECT
    f.country_code,
    c.country_name,
    COUNT(DISTINCT f.order_id)    AS total_orders,
    SUM(f.quantity)               AS total_quantity,
    ROUND(SUM(f.revenue_gbp), 2)  AS total_revenue_gbp,
    ROUND(SUM(f.revenue_eur), 2)  AS total_revenue_eur,
    -- panier moyen en euros = chiffre d'affaires en euros / nombre de commandes
    ROUND(SUM(f.revenue_eur) / COUNT(DISTINCT f.order_id), 2) AS avg_basket_eur,
    -- part du pays dans le volume total vendu (en % des quantités)
    ROUND(100.0 * SUM(f.quantity) / (SELECT SUM(quantity) FROM fact_orders), 1) AS pct_quantity
FROM fact_orders f
LEFT JOIN dim_country c ON c.country_code = f.country_code
GROUP BY f.country_code, c.country_name
ORDER BY total_revenue_eur DESC;
