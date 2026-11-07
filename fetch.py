"""
fetch.py : relais photos pour le skill carnet-vacances.

Exécuté par GitHub Actions (qui a un accès internet complet). Pour chaque fichier
demandes/<carnet>.json, cherche une photo libre de droits par lieu sur Wikimedia
Commons (ou télécharge l'URL fournie), la redimensionne et l'enregistre dans
photos/<carnet>/<id>.jpg, avec un manifest.json (crédits, licence, source).
"""
import glob
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time

import requests
from PIL import Image, ImageOps

UA = {"User-Agent": "carnet-photos/1.0 (relais du skill carnet-vacances; GitHub Actions)"}
API = "https://commons.wikimedia.org/w/api.php"
VERSION = "4"  # changer cette valeur force le retraitement de toutes les demandes
# Titres à écarter : cartes, logos, catastrophes, animaux, etc.
NOIRE = re.compile(r"tsunami|map\b|karte|flag|logo|damage|aftermath|destr|satellite|diagram|chart|macaca|monkey|"
                   r"langur|lizard|monitor|snake|portrait|\bsign|menu|ticket|crowd of|wreck|flood|scuba|diving|underwater|waste|garbage|trash|rubbish|storm|pollution|construction|\.svg|\.tif", re.I)


def strip(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def search(q, n=30):
    params = dict(action="query", format="json", generator="search", gsrsearch=f"{q} filetype:bitmap",
                  gsrnamespace=6, gsrlimit=n, prop="imageinfo",
                  iiprop="url|size|extmetadata|mime", iiurlwidth=1600)
    for essai in range(5):  # Wikimedia limite le débit : on relance avec un délai croissant
        r = requests.get(API, params={**params, "maxlag": 5}, headers=UA, timeout=30)
        try:
            ok = r.status_code == 200 and "error" not in r.json()
        except ValueError:
            ok = False
        if ok:
            break
        print(f"    recherche ralentie ({r.status_code}), nouvel essai")
        time.sleep(5 * (essai + 1))
    r.raise_for_status()
    pages = sorted((r.json().get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 99))
    out = []
    for p in pages:
        info = (p.get("imageinfo") or [{}])[0]
        if info.get("mime") not in ("image/jpeg", "image/png"):
            continue
        w, h = info.get("width", 0), info.get("height", 0)
        if w < 800 or w < h * 1.0 or w > h * 2.4:  # ni portrait ni panorama trop étiré
            continue
        if NOIRE.search(p.get("title", "")):
            continue
        em = info.get("extmetadata", {})
        lic = strip(em.get("LicenseShortName", {}).get("value"))
        if not lic:
            continue
        artist = strip(em.get("Artist", {}).get("value"))[:80] or "auteur inconnu"
        out.append({"url": info.get("thumburl") or info["url"], "titre": p.get("title", ""),
                    "credit": f"{artist}, {lic}, via Wikimedia Commons", "page": info.get("descriptionurl")})
    return out


def score(c, mots):
    t = c["titre"].lower()
    return sum(1 for m in mots if m.lower() in t)


def download(url):
    for essai in range(4):
        r = requests.get(url, headers=UA, timeout=60)
        if r.status_code == 200:
            break
        time.sleep(5 * (essai + 1))
    r.raise_for_status()
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(r.content))).convert("RGB")
    if img.width > 1600:
        img = img.resize((1600, int(img.height * 1600 / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=86, optimize=True, progressive=True)
    return buf.getvalue()


def yt_search(q, mots, n=10):
    """Recherche YouTube via yt-dlp ; renvoie la vidéo la plus vue qui correspond au lieu."""
    try:
        out = subprocess.run([sys.executable, "-m", "yt_dlp", "--flat-playlist", "--dump-json", "--no-warnings", f"ytsearch{n}:{q}"],
                             capture_output=True, text=True, timeout=180)
    except Exception as ex:
        print(f"    yt-dlp en échec ({ex})")
        return None
    if out.returncode != 0:
        print(f"    yt-dlp : {out.stderr[-300:]}")
    res = []
    for line in out.stdout.splitlines():
        try:
            v = json.loads(line)
        except ValueError:
            continue
        t, dur, vues = v.get("title") or "", v.get("duration") or 0, v.get("view_count") or 0
        if dur < 90 or dur > 40 * 60:  # ni « shorts » ni vidéos de plusieurs heures
            continue
        if mots and not any(m.lower() in t.lower() for m in mots):
            continue
        res.append({"url": f"https://www.youtube.com/watch?v={v['id']}", "titre": t,
                    "chaine": v.get("channel") or v.get("uploader") or "", "duree": int(dur), "vues": int(vues)})
    res.sort(key=lambda x: -x["vues"])
    return res[0] if res else None


def repli_depuis_zone(lieu, dest):
    z = lieu.get("zone")
    if not z:
        return lieu.get("repli", [])
    zk = re.sub(r"^(koh|ko)\s+", "", z, flags=re.I)
    return [{"requete": q, "mots": [zk], "illustration": z}
            for q in (f"{z} beach {dest}", f"{z} {dest}", f"{zk} beach", zk)]


def traiter(path):
    raw = open(path, "rb").read()
    dem = json.loads(raw)
    carnet = dem.get("carnet") or os.path.splitext(os.path.basename(path))[0]
    out_dir = os.path.join("photos", carnet)
    man_path = os.path.join(out_dir, "manifest.json")
    h = hashlib.sha1(raw + VERSION.encode()).hexdigest()
    if os.path.exists(man_path) and json.load(open(man_path)).get("_hash") == h:
        print(f"{carnet} : déjà traité")
        return
    os.makedirs(out_dir, exist_ok=True)
    man, used = {"_hash": h, "_demande": hashlib.sha1(raw).hexdigest(), "photos": {}, "manquants": [],
                 "videos": {}}, set()
    dest = dem.get("dest", "")
    for v in dem.get("videos_generales", []):
        got_v = yt_search(v["requete"], v.get("mots", []))
        if got_v:
            man["videos"][v["id"]] = got_v
            print(f"  vidéo {v['id']} : {got_v['titre']}")
    for lieu in dem.get("lieux", []):
        lid = lieu["id"]
        got = None
        if lieu.get("video"):
            got_v = yt_search(lieu["video"]["requete"], lieu["video"].get("mots", []))
            if got_v:
                man["videos"][lid] = got_v
                print(f"  vidéo {lid} : {got_v['titre']}")
        if lieu.get("url"):
            try:
                got = {"data": download(lieu["url"]), "credit": lieu.get("credit") or lieu["url"], "titre": lieu["url"]}
            except Exception as e:
                print(f"  {lid} : URL en échec ({e})")
        essais = [{"requete": q, "mots": lieu.get("mots", [])} for q in lieu.get("requetes", [])]
        essais += [{"requete": r["requete"], "mots": r.get("mots", []), "illustration": r.get("illustration")}
                   for r in repli_depuis_zone(lieu, dest)]
        for e in essais:
            if got:
                break
            try:
                cands = [c for c in search(e["requete"]) if c["url"] not in used]
            except Exception as ex:
                print(f"  {lid} : recherche en échec ({ex})")
                time.sleep(2)
                continue
            excl = [x.lower() for x in lieu.get("exclure", [])]
            cands = [c for c in cands if not any(x in c["titre"].lower() for x in excl)]
            if e["mots"]:  # au moins une des expressions doit figurer dans le titre de la photo
                cands = sorted([c for c in cands if score(c, e["mots"]) >= 1], key=lambda c: -score(c, e["mots"]))
            for c in cands[:3]:
                try:
                    got = {"data": download(c["url"]), "credit": c["credit"], "titre": c["titre"],
                           "page": c.get("page"), "illustration": e.get("illustration"), "requete": e["requete"]}
                    used.add(c["url"])
                    break
                except Exception as ex:
                    print(f"  {lid} : téléchargement en échec ({ex})")
            time.sleep(1.5)
        if got:
            fn = f"{lid}.jpg"
            open(os.path.join(out_dir, fn), "wb").write(got.pop("data"))
            got["fichier"] = fn
            man["photos"][lid] = got
            print(f"  {lid} : OK ({got['titre']})")
        else:
            man["manquants"].append(lid)
            print(f"  {lid} : aucune photo")
    json.dump(man, open(man_path, "w"), ensure_ascii=False, indent=1)
    print(f"{carnet} : {len(man['photos'])} photos, {len(man['manquants'])} manquantes")


if __name__ == "__main__":
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "yt-dlp"], check=False)
    for p in sorted(glob.glob("demandes/*.json")):
        traiter(p)
