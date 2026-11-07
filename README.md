# carnet-photos

Relais photos du skill Claude `carnet-vacances`.

Claude dépose une demande dans `demandes/<carnet>.json` ; GitHub Actions cherche une photo
libre de droits par lieu sur Wikimedia Commons, la redimensionne et l'enregistre dans
`photos/<carnet>/` avec un `manifest.json` (auteur, licence, page source).
Toutes les photos sont sous licence libre et créditées dans les carnets.
