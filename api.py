"""API BookWorld : expose les ventes par pays.

Lancer :
    export API_TOKEN="mon-token-secret"
    python api.py

Routes :
    GET /health            -> état de l'API (sans token)
    GET /sales-by-country  -> ventes par pays (header  Authorization: Bearer <token>)
"""
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from flask import Flask, jsonify, request

FINAL_DB = "bookworld_final.db"
API_TOKEN = os.getenv("API_TOKEN")  # le token n'est jamais écrit dans le code

app = Flask(__name__)
app.json.sort_keys = False  # garde l'ordre des colonnes dans le JSON


def read_sales_by_country():
    """Lit la table sales_by_country dans la base finale."""
    if not Path(FINAL_DB).exists():  # sinon sqlite3.connect créerait une base vide
        raise sqlite3.Error("base absente")
    with closing(sqlite3.connect(FINAL_DB)) as con:
        con.row_factory = sqlite3.Row  # permet de transformer chaque ligne en dictionnaire
        lignes = con.execute("SELECT * FROM sales_by_country ORDER BY total_revenue_eur DESC")
        return [dict(ligne) for ligne in lignes]


def token_is_valid():
    """Vérifie l'en-tête  Authorization: Bearer <token>."""
    return bool(API_TOKEN) and request.headers.get("Authorization") == f"Bearer {API_TOKEN}"


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.get("/sales-by-country")
def sales_by_country():
    if not token_is_valid():
        return jsonify(error="Token manquant ou invalide"), 401
    try:
        data = read_sales_by_country()
    except sqlite3.Error:
        return jsonify(error="Base finale indisponible : lancez d'abord pipeline.py"), 503
    return jsonify(count=len(data), data=data)


if __name__ == "__main__":
    if not API_TOKEN:
        raise SystemExit("Erreur : définissez la variable d'environnement API_TOKEN")
    app.run(port=8000)
