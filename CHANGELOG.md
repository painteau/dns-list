# Changelog

Toutes les évolutions notables de `dns-list` sont documentées ici.

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/), versionnage
[SemVer](https://semver.org/lang/fr/). La section `[Unreleased]` accumule au fil de l'eau et
est renommée en numéro de version au moment de poser le tag.

Ce fichier est créé le 2026-09-05, après la mise en service : les évolutions antérieures ne sont
pas reconstituées, ce qui serait de la réécriture d'historique plutôt que de la documentation.
L'historique git reste la source de vérité pour ce qui précède.

## [Unreleased]

### Ajouté

- **`adobe.txt`** : 18 domaines repris de `Ruddernation-Designs/Adobe-URL-Block-List`,
  non resynchronisé depuis avril 2025. 9 entrées amont sont **volontairement écartées**
  et la raison est écrite dans le fichier : 7 adresses IP nues, inertes dans une liste
  DNS puisqu'un client qui se connecte à une IP ne résout aucun nom, et 2 points d'accès
  Sentry que `@@||sentry.io^$important` annule déjà — les reprendre créerait deux lignes
  mortes, et casserait l'envoi des sourcemaps en CI si le fichier servait un jour de
  `hosts` système, où la whitelist ne s'applique pas.
- **`nintendo.txt`** : `nega-lp1-upload.s3.us-east-1.amazonaws.com` (seul nouveau domaine
  de la prise en charge Switch 2 ajoutée en amont en juin 2025, le reste du trafic passant
  par les domaines `nintendo.*` déjà listés) et `90dns.test`, qui doit répondre l'adresse
  du serveur de test et non être bloqué, faute de quoi on n'a aucun moyen de constater que
  le blocage s'applique. Le fichier datait de juillet 2023.
- **`scripts/verifier-amont.py`** et un second job dans le workflow hebdomadaire :
  compare `adobe.txt` et `nintendo.txt` à leur source amont. Sa table `EXCLUSIONS` porte
  les entrées non reprises **avec leur raison** ; sans elle le contrôle serait rouge en
  permanence sur des choix assumés, et un contrôle toujours rouge finit ignoré.

### Corrigé

- **`onion-blacklist.txt`** : 44 règles étaient inopérantes ou redondantes. 23 doublons
  exacts, 19 règles sans `^` final (donc de portée différente du reste du fichier),
  1 adresse écrite `…ovqdonion` sans le point avant le TLD, 1 commentaire collé à sa
  règle sans séparateur, et 3 lignes en `https:^^domaine^` qui ne sont pas de la syntaxe
  de filtre et ne bloquaient rien. Aucun domaine perdu, vérifié par comparaison des
  ensembles avant/après.

### À arbitrer

- **`onion-blacklist.txt`** : 545 de ses 1198 règles visent des adresses `.onion` de
  deuxième génération (16 caractères). Tor a coupé le support des services v2 en octobre
  2021, ces adresses ne sont donc joignables par personne. Elles ne gênent pas, mais
  représentent près de la moitié du fichier. Non supprimées ici : c'est une décision,
  pas un correctif.
- **`onion-blacklist.txt`** n'a aucune source amont — un seul commit `Create`, jamais
  resynchronisé. Il n'y a donc rien à en rafraîchir automatiquement, et « ajouter les
  nouveaux » y demanderait d'abord de choisir une source.

### Ajouté (résolveurs)

- **`scripts/verifier-resolveurs.py`** : vérifie que chaque entrée de `good-dns.txt`
  répond, par une vraie requête DNS décodée et non par un test de port ouvert, sur les
  trois transports (DoH, DoT, UDP). Trois verdicts et pas deux, le troisième étant
  « indéterminé » quand la machine courante ne permet pas de trancher.
- **`.github/workflows/verifier-resolveurs.yml`** : rejoue cette vérification tous les
  lundis, et à chaque modification de la liste ou du vérificateur.
- **`good-dns.txt`** : DNS4EU, Mullvad, Applied Privacy et Digitale Gesellschaft, tous
  testés avant ajout. DNS4EU remplace dns0.eu, dont l'équipe le recommande elle-même
  dans son avis de fermeture.
- **`dns-provider.txt`** : les noms de DNS4EU, qui manquaient. C'est aujourd'hui la voie
  de contournement la plus probable, puisque c'est celle vers laquelle les utilisateurs
  de dns0.eu ont été renvoyés.

### Supprimé

- **`good-dns.txt`** : trois fournisseurs sur huit ne répondaient plus. `DNS0.EU` a
  arrêté son service fin octobre 2025 faute de ressources ; `FutureDNS` a son domaine
  parent en SERVFAIL ; `resolver.dnsprivacy.org.uk` est en NXDOMAIN et ses deux IP sont
  muettes. Les trois sont consignés en pied de fichier pour ne pas être reproposés.

### Corrigé

- **`good-dns.txt`** ne liste plus que les transports réellement servis par chaque
  fournisseur. Mullvad, Applied Privacy et Digitale Gesellschaft refusent délibérément
  le DNS en clair sur le port 53 ; les inscrire quand même donnerait une liste qui a
  l'air plus riche et qui marche moins.

### Notes

- Le vérificateur a d'abord déclaré morts quatre points d'accès DoH bien vivants :
  Quad9 et FDN exigent HTTP/2 et répondent `505 HTTP Version Not Supported` à une
  requête HTTP/1.1 pourtant valide, or `urllib` ne parle que HTTP/1.1. D'où le repli
  sur un vrai client HTTP/2 (`curl --http2`, sinon `node`), et le verdict
  « indéterminé » quand aucun n'est disponible. **Un garde-fou qui ment fait plus de
  dégâts que pas de garde-fou du tout.**
- Trois des onze échecs du premier passage étaient des faux négatifs dus à notre propre
  `dns-provider.txt`, qui bloque les résolveurs tiers : un test lancé depuis le parc voit
  morts des services vivants. Les verdicts ont été retranchés contre un résolveur externe.

## [0.1.0] - 2026-09-05

### Ajouté

- **Convention changelog du parc posée sur ce dépôt** : ce fichier, les hooks `pre-commit` et
  `pre-push` dans `.githooks/`, et le workflow `changelog-guard.yml` qui rejoue les mêmes
  contrôles en CI au moment du tag. Ce dépôt en était dépourvu alors qu'il est déployé, ce qui
  le laissait hors de la garantie que les autres ont.

