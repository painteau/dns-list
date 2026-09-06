#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Vérifie que les résolveurs listés dans good-dns.txt répondent encore.

Lecture seule : le script ne modifie jamais la liste, il rapporte. Trois transports
sont testés, chacun avec une requête DNS réellement construite et une réponse
réellement décodée. Un simple test de port ouvert dirait « vivant » d'un résolveur
qui refuse toutes les requêtes.

  https://...  DoH, RFC 8484 (POST, application/dns-message)
  tls://...    DoT, RFC 7858 (TCP 853, cadrage longueur sur 2 octets)
  1.2.3.4      DNS classique (UDP 53)

Trois verdicts et pas deux : « ok », « ko », et « indéterminé » quand la machine
courante ne permet pas de trancher (voir le pavé sur HTTP/2 plus bas). Un
indéterminé ne fait pas échouer le script, il demande une vérification humaine.

Code de sortie 1 s'il reste au moins un « ko », pour servir de garde-fou en CI.
Idempotent : relançable autant de fois que voulu, aucun effet de bord.
"""

from __future__ import annotations

import argparse
import base64
import functools
import os
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# Le nom interrogé est volontairement banal et stable : on teste le résolveur,
# pas la zone. example.com est réservé par la RFC 2606, il ne disparaîtra pas.
NOM_TEST = "example.com"
DELAI = 8.0

RACINE = Path(__file__).resolve().parent.parent


def construire_requete(nom: str) -> bytes:
    """Construit une requête DNS A minimale (ID 0, récursion demandée)."""
    entete = struct.pack(">HHHHHH", 0, 0x0100, 1, 0, 0, 0)
    qname = b"".join(
        struct.pack("B", len(etiquette)) + etiquette.encode("ascii")
        for etiquette in nom.split(".")
    ) + b"\x00"
    return entete + qname + struct.pack(">HH", 1, 1)  # QTYPE=A, QCLASS=IN


def reponse_valide(donnees: bytes) -> bool:
    """Vrai si la réponse est un message DNS avec au moins un enregistrement."""
    if len(donnees) < 12:
        return False
    drapeaux, _, ancount = struct.unpack(">HHH", donnees[2:8])
    est_reponse = drapeaux & 0x8000
    rcode = drapeaux & 0x000F
    return bool(est_reponse) and rcode == 0 and ancount > 0


@dataclass
class Resultat:
    fournisseur: str
    cible: str
    transport: str
    etat: str  # "ok", "ko" ou "indetermine"
    detail: str


# ---------------------------------------------------------------------------
# DoH
#
# Piège payé le 2026-09-06, à ne pas réintroduire : plusieurs résolveurs
# (Quad9, FDN) exigent HTTP/2 et répondent 505 "HTTP Version Not Supported"
# à une requête HTTP/1.1 pourtant parfaitement valide. urllib ne parle que
# HTTP/1.1, donc la première version de ce script a déclaré morts quatre points
# d'accès bien vivants. Un garde-fou qui ment fait plus de dégâts que pas de
# garde-fou du tout : on essaie donc un vrai client HTTP/2 en premier, et à
# défaut on rapporte "indéterminé" plutôt que d'accuser à tort.
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def client_http2() -> str | None:
    """Nom du client HTTP/2 disponible sur cette machine, sinon None."""
    curl = shutil.which("curl")
    if curl:
        try:
            version = subprocess.run(
                [curl, "--version"], capture_output=True, text=True, timeout=10
            ).stdout
            if "HTTP2" in version:
                return "curl"
        except Exception:  # noqa: BLE001
            pass
    if shutil.which("node"):
        return "node"
    return None


SCRIPT_NODE = r"""
const http2 = require('http2');
// Avec `node -e`, argv ne contient pas de nom de fichier de script :
// les arguments commencent a l'indice 1 et non 2.
const [origine, chemin] = process.argv.slice(1);
const requete = Buffer.from(process.env.DNS_QUERY_B64, 'base64');
const client = http2.connect(origine);
let fini = false;
const sortir = (code) => {
  if (fini) return;
  fini = true;
  try { client.close(); } catch {}
  process.exit(code);
};
client.on('error', () => sortir(2));
setTimeout(() => sortir(3), 9000);
const flux = client.request({
  ':method': 'POST', ':path': chemin,
  'content-type': 'application/dns-message',
  'accept': 'application/dns-message',
  'content-length': requete.length,
});
const morceaux = [];
flux.on('data', (d) => morceaux.push(d));
flux.on('error', () => sortir(2));
flux.on('end', () => { process.stdout.write(Buffer.concat(morceaux)); sortir(0); });
flux.end(requete);
"""


def tester_doh(url: str) -> tuple[str, str]:
    client = client_http2()
    if client == "curl":
        return _doh_curl(url)
    if client == "node":
        return _doh_node(url)
    return _doh_urllib(url)


def _doh_curl(url: str) -> tuple[str, str]:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as fichier:
        fichier.write(construire_requete(NOM_TEST))
        chemin_requete = fichier.name
    try:
        acheve = subprocess.run(
            [
                "curl", "--silent", "--show-error", "--http2",
                "--max-time", str(int(DELAI)),
                "--header", "content-type: application/dns-message",
                "--header", "accept: application/dns-message",
                "--data-binary", f"@{chemin_requete}",
                url,
            ],
            capture_output=True,
            timeout=DELAI + 5,
        )
    except Exception as err:  # noqa: BLE001
        return "ko", type(err).__name__
    finally:
        Path(chemin_requete).unlink(missing_ok=True)
    if acheve.returncode != 0:
        return "ko", f"curl code {acheve.returncode}"
    if not reponse_valide(acheve.stdout):
        return "ko", "reponse DNS invalide ou vide"
    return "ok", "ok"


def _doh_node(url: str) -> tuple[str, str]:
    separateur = url.find("/", len("https://"))
    if separateur == -1:
        origine, chemin = url, "/"
    else:
        origine, chemin = url[:separateur], url[separateur:]
    environnement = dict(os.environ)
    environnement["DNS_QUERY_B64"] = base64.b64encode(construire_requete(NOM_TEST)).decode()
    try:
        acheve = subprocess.run(
            ["node", "-e", SCRIPT_NODE, origine, chemin],
            capture_output=True,
            timeout=DELAI + 5,
            env=environnement,
        )
    except Exception as err:  # noqa: BLE001
        return "ko", type(err).__name__
    if acheve.returncode != 0:
        return "ko", "connexion HTTP/2 refusee ou expiree"
    if not reponse_valide(acheve.stdout):
        return "ko", "reponse DNS invalide ou vide"
    return "ok", "ok"


def _doh_urllib(url: str) -> tuple[str, str]:
    demande = urllib.request.Request(
        url,
        data=construire_requete(NOM_TEST),
        method="POST",
        headers={
            "Content-Type": "application/dns-message",
            "Accept": "application/dns-message",
            # Certains resolveurs refusent un User-Agent vide ou celui d'urllib.
            "User-Agent": "dns-list-verifier/1.0",
        },
    )
    try:
        with urllib.request.urlopen(demande, timeout=DELAI) as reponse:
            corps = reponse.read()
    except urllib.error.HTTPError as err:
        if err.code in (400, 405, 415, 505):
            return (
                "indetermine",
                f"HTTP {err.code} en HTTP/1.1, exige probablement HTTP/2 : "
                "installer curl avec HTTP/2 ou node pour trancher",
            )
        return "ko", f"HTTP {err.code}"
    except Exception as err:  # noqa: BLE001 - on veut la cause en clair dans le rapport
        return "ko", type(err).__name__ + (f": {err}" if str(err) else "")
    if not reponse_valide(corps):
        return "ko", "reponse DNS invalide ou vide"
    return "ok", "ok"


def tester_dot(hote: str) -> tuple[str, str]:
    requete = construire_requete(NOM_TEST)
    cadre = struct.pack(">H", len(requete)) + requete
    contexte = ssl.create_default_context()
    # Une IP en tls:// ne peut pas etre validee par nom : on garde le chiffrement
    # mais on n'exige pas le certificat, sinon tls://1.1.1.1 echouerait a tort.
    est_ip = hote.count(".") == 3 and all(part.isdigit() for part in hote.split("."))
    if est_ip:
        contexte.check_hostname = False
        contexte.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((hote, 853), timeout=DELAI) as brut:
            with contexte.wrap_socket(
                brut, server_hostname=None if est_ip else hote
            ) as tls:
                tls.sendall(cadre)
                entete = tls.recv(2)
                if len(entete) < 2:
                    return "ko", "connexion fermee sans reponse"
                taille = struct.unpack(">H", entete)[0]
                corps = b""
                while len(corps) < taille:
                    morceau = tls.recv(taille - len(corps))
                    if not morceau:
                        break
                    corps += morceau
    except Exception as err:  # noqa: BLE001
        return "ko", type(err).__name__ + (f": {err}" if str(err) else "")
    if not reponse_valide(corps):
        return "ko", "reponse DNS invalide"
    return "ok", "ok"


def tester_udp(ip: str) -> tuple[str, str]:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as prise:
            prise.settimeout(DELAI)
            prise.sendto(construire_requete(NOM_TEST), (ip, 53))
            corps, _ = prise.recvfrom(4096)
    except Exception as err:  # noqa: BLE001
        return "ko", type(err).__name__
    if not reponse_valide(corps):
        return "ko", "reponse DNS invalide"
    return "ok", "ok"


def lire_liste(chemin: Path) -> list[tuple[str, str]]:
    """Rend une liste de (fournisseur, cible) en suivant les en-tetes en commentaire."""
    entrees: list[tuple[str, str]] = []
    fournisseur = "(sans en-tete)"
    # Seule la premiere ligne de commentaire d'un bloc nomme le fournisseur.
    # Les suivantes sont des notes ; les prendre pour des en-tetes decoupait
    # chaque bloc en autant de faux fournisseurs dans le rapport.
    debut_de_bloc = True
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne:
            debut_de_bloc = True
            continue
        if ligne.startswith("#"):
            if debut_de_bloc:
                fournisseur = ligne.lstrip("#").strip()
                debut_de_bloc = False
            continue
        debut_de_bloc = False
        entrees.append((fournisseur, ligne))
    return entrees


def tester(fournisseur: str, cible: str) -> Resultat:
    if cible.startswith("https://"):
        etat, detail = tester_doh(cible)
        return Resultat(fournisseur, cible, "DoH", etat, detail)
    if cible.startswith("tls://"):
        etat, detail = tester_dot(cible[len("tls://"):])
        return Resultat(fournisseur, cible, "DoT", etat, detail)
    etat, detail = tester_udp(cible)
    return Resultat(fournisseur, cible, "UDP", etat, detail)


MARQUES = {"ok": "OK ", "ko": "KO ", "indetermine": "?? "}


def main() -> int:
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument(
        "--fichier",
        default=str(RACINE / "good-dns.txt"),
        help="liste de resolveurs a verifier (defaut : good-dns.txt)",
    )
    analyseur.add_argument(
        "--tolerant",
        action="store_true",
        help="sort toujours en 0, meme si des resolveurs echouent",
    )
    arguments = analyseur.parse_args()

    chemin = Path(arguments.fichier)
    if not chemin.is_file():
        print(f"Fichier introuvable : {chemin}", file=sys.stderr)
        return 2

    client = client_http2()
    print(
        f"Client HTTP/2 utilise pour DoH : {client or 'aucun (verdicts DoH indetermines)'}"
    )

    resultats = [tester(f, c) for f, c in lire_liste(chemin)]

    largeur = max((len(r.cible) for r in resultats), default=0)
    fournisseur_courant = None
    for resultat in resultats:
        if resultat.fournisseur != fournisseur_courant:
            fournisseur_courant = resultat.fournisseur
            print(f"\n{fournisseur_courant}")
        marque = MARQUES[resultat.etat]
        print(
            f"  {marque} {resultat.transport:<4} {resultat.cible:<{largeur}}  {resultat.detail}"
        )

    echecs = [r for r in resultats if r.etat == "ko"]
    doutes = [r for r in resultats if r.etat == "indetermine"]
    print(
        f"\n{len(resultats) - len(echecs) - len(doutes)}/{len(resultats)} resolveurs "
        f"repondent, {len(echecs)} en echec, {len(doutes)} indetermines."
    )
    if echecs and not arguments.tolerant:
        print(
            "\nUn echec isole peut etre un blocage reseau local plutot qu'un resolveur "
            "mort : rejouer depuis un autre reseau avant de retirer une entree. "
            "Notre propre dns-provider.txt bloque des resolveurs tiers, ce qui suffit "
            "a faire echouer un test lance depuis le parc.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
