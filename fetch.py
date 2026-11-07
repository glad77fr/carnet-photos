"""
fetch.py : relais photos pour le skill carnet-vacances.

Exécuté par GitHub Actions (qui a un accès internet complet). Pour chaque fichier
demandes/<carnet>.json, cherche une photo libre de droits par lieu sur Wikimedia
Commons (ou télécharge l'URL fournie), la redimensionne et l'enregistre dans
photos/<carnet>/<id>.jpg, avec un manifest.json (crédits, licence, source).
"""
