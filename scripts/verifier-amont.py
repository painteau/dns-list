#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare les listes derivees d'une source amont avec cette source.

Deux fichiers de ce depot sont des copies d'un projet tiers : adobe.txt vient de
Ruddernation-Designs/Adobe-URL-Block-List, nintendo.txt de gitlab.com/a/90dns.
Ni l'un ni l'autre n'avait ete resynchronise depuis longtemps (avril 2025 et
juillet 2023), et rien ne le signalait. Ce script pose ce controle.

Lecture seule : il rapporte les entrees presentes en amont et absentes ici. Il
ne fusionne rien, la reprise reste une decision humaine, notamment parce qu'un
ajout amont peut casser un usage legitime (armmf.adobe.com sert aussi les mises
a jour d'Acrobat Reader).

Point critique : les EXCLUSIONS ci-dessous doivent rester a jour. Sans elles, ce
controle serait rouge en permanence pour des lignes qu'on a decide de ne pas
reprendre, et un controle toujours rouge finit ignore, donc ne controle plus rien.

Code de sortie 1 s'il existe un ecart non explique.
Idempotent : aucun effet de bord, relançable a volonte.
"""

from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
DELAI = 30

# Entrees presentes en amont et volontairement non reprises ici. Toute entree
# ajoutee a cette table doit l'etre avec sa raison, qui est le seul moyen pour
# une relecture future de distinguer un choix d'un oubli.
EXCLUSIONS = {
    "adobe.txt": {
        # Une entree de type hosts associe un NOM a une adresse. Y mettre une
        # adresse des deux cotes ne bloque rien : un client qui se connecte
        # directement a une IP ne resout aucun nom. C'est du ressort du pare-feu.
        "13.35.238.72": "adresse IP nue, inerte dans une liste DNS",
        "13.35.238.103": "adresse IP nue, inerte dans une liste DNS",
        "13.35.238.108": "adresse IP nue, inerte dans une liste DNS",
        "18.154.7.93": "adresse IP nue, inerte dans une liste DNS",
        "23.22.254.206": "adresse IP nue, inerte dans une liste DNS",
        "3.209.122.138": "adresse IP nue, inerte dans une liste DNS",
        "54.144.73.197": "adresse IP nue, inerte dans une liste DNS",
        # Couverts par @@||sentry.io^$important dans whitelist.txt, pose expres
        # parce que le blocage de Sentry cassait l'envoi des sourcemaps en CI.
        "o1383653.ingest.sentry.io": "annule par la whitelist, et casserait la CI",
        "o987771.ingest.us.sentry.io": "annule par la whitelist, et casserait la CI",
    },
    "nintendo.txt": {},
}


def telecharger(url: str) -> str:
    demande = urllib.request.Request(url, headers={"User-Agent": "dns-list/1.0"})
    with urllib.request.urlopen(demande, timeout=DELAI) as reponse:
        return reponse.read().decode("utf-8", errors="replace")


def entrees_hosts(texte: str) -> set[str]:
    """Extrait les noms d'un fichier au format hosts, en ignorant les commentaires."""
    noms = set()
    for ligne in texte.splitlines():
        ligne = ligne.split("#", 1)[0].strip()
        if not ligne:
            continue
        parties = ligne.split()
        if len(parties) < 2:
            continue
        if not re.match(r"^\d+\.\d+\.\d+\.\d+$", parties[0]):
            continue
        # Le fichier local prefixe ses jokers d'une etoile (*nintendo.com), pas l'amont.
        noms.add(parties[1].lstrip("*.").lower())
    return noms


def entrees_dnsmasq(texte: str) -> set[str]:
    """Extrait les noms d'un dnsmasq.conf de 90DNS.

    Tolere l'egale manquante : l'amont porte une ligne "address/..." au lieu de
    "address=/...", et une expression exigeant l'egale raterait silencieusement
    la seule entree ajoutee pour la Switch 2.
    """
    noms = set()
    for correspondance in re.finditer(r"^address=?/\.?([^/]+)/", texte, re.MULTILINE):
        noms.add(correspondance.group(1).lower())
    return noms


SOURCES = [
    {
        "fichier": "adobe.txt",
        "url": "https://raw.githubusercontent.com/Ruddernation-Designs/"
               "Adobe-URL-Block-List/master/hosts",
        "analyseur_amont": entrees_hosts,
        "analyseur_local": entrees_hosts,
    },
    {
        "fichier": "nintendo.txt",
        # L'API plutot que l'URL brute : gitlab.com sert un challenge Cloudflare
        # sur ses pages, que l'API ne pose pas.
        "url": "https://gitlab.com/api/v4/projects/a%2F90dns/repository/files/"
               "dnsmasq%2Fdnsmasq.conf/raw?ref=master",
        "analyseur_amont": entrees_dnsmasq,
        "analyseur_local": entrees_hosts,
    },
]


def main() -> int:
    total_ecarts = 0
    for source in SOURCES:
        nom = source["fichier"]
        print(f"\n=== {nom}")
        try:
            amont = source["analyseur_amont"](telecharger(source["url"]))
        except Exception as err:  # noqa: BLE001
            # Une source injoignable n'est pas un parc conforme : on echoue.
            print(f"  AMONT INJOIGNABLE : {type(err).__name__}: {err}", file=sys.stderr)
            total_ecarts += 1
            continue
        if not amont:
            print("  AMONT VIDE, format probablement change", file=sys.stderr)
            total_ecarts += 1
            continue

        local = source["analyseur_local"]((RACINE / nom).read_text(encoding="utf-8"))
        exclusions = EXCLUSIONS.get(nom, {})
        manquants = sorted(amont - local - set(exclusions))

        print(f"  amont={len(amont)}  local={len(local)}  "
              f"exclusions={len(exclusions)}  manquants={len(manquants)}")
        for entree in manquants:
            print(f"    + {entree}")
        total_ecarts += len(manquants)

        # Une exclusion qui ne correspond plus a rien en amont est du bruit :
        # la signaler evite que cette table devienne un cimetiere illisible.
        obsoletes = sorted(set(exclusions) - amont)
        for entree in obsoletes:
            print(f"    (exclusion devenue inutile, retirable : {entree})")

    if total_ecarts:
        print(
            f"\n{total_ecarts} ecart(s). Reprendre au cas par cas : un ajout amont "
            "peut casser un usage legitime. Si une entree ne doit pas etre reprise, "
            "l'inscrire dans EXCLUSIONS de ce script AVEC SA RAISON.",
            file=sys.stderr,
        )
        return 1
    print("\nAucun ecart avec l'amont.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
