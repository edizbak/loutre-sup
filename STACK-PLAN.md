# Plan de stack de supervision

## Objectif et périmètre

Mettre en place une supervision opérationnelle, minimale et en lecture seule du cluster k3s et des services exposés d'`ultralout.re`. La stack doit rendre visibles l'état des nœuds et workloads, les ressources, ainsi que la disponibilité des routes; elle ne doit pas devenir un panneau d'administration du cluster.

Ce fichier décrit la conception, le séquencement et l'état d'implémentation. Le profil de développement est une configuration de test isolée, pas une cible de production. Les manifests de production, règles d'alerte et sondes seront ajoutés après validation des choix requis et selon une démarche TDD autant que possible.

## Avancement de l'implémentation

Un profil de développement est présent dans `k8s/dev/`. Il fournit Prometheus, kube-state-metrics, Grafana et un dashboard d'état minimal, avec Services internes, RBAC de lecture et stockage éphémère. Le namespace `monitoring-dev` et le bootstrap RBAC ont été créés sur le cluster après leurs dry-runs; les workloads de supervision ne sont pas encore déployés. Le profil ne constitue pas une cible de production.

L'inventaire du serveur a été reçu et analysé. Le namespace et le bootstrap RBAC sont installés; les workloads restent à déployer après création du Secret Grafana et validation du dry-run. Les choix de production restent ouverts : stockage/rétention, exposition/authentification, ressources définitives, métriques hôte (node-exporter), sondes externes, notifications et isolation réseau.

Les manifests sont séparés en trois ensembles : `k8s/dev/namespace/` pour le namespace, `k8s/dev/bootstrap/` pour le RBAC cluster-scopé et le compte/role de déploiement limité au namespace, et `k8s/dev/` pour les workloads. Le déploiement normal ne nécessite donc pas le kubeconfig administrateur. Le compte de déploiement peut modifier les workloads du namespace et doit être considéré comme privilégié sur les Secrets qu'ils peuvent monter ainsi que sur les permissions de lecture de kube-state-metrics qu'il peut indirectement obtenir; il ne peut pas gérer directement les Secrets via l'API ni modifier le RBAC.

Le premier dry-run combinant la création du namespace et les objets namespacés a échoué comme attendu : un server-side dry-run ne persiste pas le namespace avant de valider les objets suivants. Le namespace et le bootstrap ont ensuite été appliqués séparément après validation de leurs dry-runs; aucun workload de supervision n'a encore été déployé.

## Prochaine étape : Secret Grafana et dry-run workloads

La procédure opératoire détaillée est dans `docs/development-deployment.md`; le Secret est décrit dans `docs/grafana-admin-secret.md`. Après création et vérification du Secret par l'administrateur, effectuer avec le kubeconfig restreint :

```sh
kubectl apply --dry-run=server -k k8s/dev
```

Transmettre et examiner la sortie avant tout apply réel des workloads. Ce dry-run ne valide ni le scheduling, ni le pull des images.

Avant le premier `kubectl apply -k k8s/dev`, les points d'arrêt sont :

1. **Décision prise pour le profil dev :** l'accès aux Services ClusterIP de `monitoring-dev` depuis les autres pods du cluster est accepté pour cet environnement de développement. Aucune NetworkPolicy dev n'est requise avant l'installation. Cette acceptation ne s'étend pas à la production;
2. créer le Secret `grafana-admin-credentials` hors Git par le guide `docs/grafana-admin-secret.md`, sans en communiquer les données;
3. examiner et approuver séparément le dry-run des workloads après mise en place du kubeconfig restreint.

Après approbation explicite de chaque étape, l'opérateur applique le profil, attend les trois rollouts, vérifie les endpoints/cibles/règles avec des port-forwards temporaires, puis exécute `scripts/check-rbac.sh`. L'arrêt/rollback est documenté et ne doit supprimer que le namespace dédié s'il ne contient aucun workload étranger. Les accès serveur ont été exécutés manuellement par l'administrateur; les tests et la CI de ce dépôt n'utilisent pas les credentials du cluster.

## Résultats de l'inventaire serveur

L'inventaire en lecture seule fourni le 2026-11-05 confirme :

- k3s `v1.35.4+k3s1` sur un seul nœud `control-plane` sous Debian 13; une perte du nœud correspond donc à une perte totale du cluster et de la stack locale.
- Le nœud dispose de 4 vCPU et environ 8 Gio de RAM. À l'instant de l'inventaire, Metrics Server rapporte environ 4 % CPU et 38 % mémoire utilisés. Cela donne une photographie ponctuelle, pas une marge de capacité garantie.
- Le disque racine est d'environ 59 Gio, avec environ 18 Gio libres (70 % utilisés). Les PVC existants MySQL et MinIO utilisent la classe `local-path`, `ReadWriteOnce`, `Delete`, sans expansion; aucun stockage partagé ou backup n'est visible dans cet inventaire.
- Metrics Server est déjà déployé et son API `metrics.k8s.io` est disponible. Il fournit `kubectl top`, mais ne remplace pas Prometheus pour l'historique ni les règles d'alerte.
- Il n'y a pas de `ResourceQuota`, `LimitRange` ou `NetworkPolicy` actuellement. ingress-nginx est le seul IngressClass; les routes publiques se trouvent dans le namespace `default`.
- Des anciens pods `Completed`, `Error`, `ContainerStatusUnknown` et `Evicted` apparaissent dans la liste fournie alors que les déploiements courants étaient prêts. Le dashboard/alerting devra distinguer les pods contrôlés encore attendus des pods terminés ou anciens pour éviter les faux positifs.
- Les règles dev couvrent l'indisponibilité des cibles scrappées, les replicas de déploiement indisponibles et un nœud non Ready. Les tests prouvent l'évaluation PromQL; aucune notification ne sera délivrée tant qu'un canal et un destinataire ne sont pas choisis.
- Le profil Prometheus courant ne collecte pas encore la consommation CPU/mémoire réelle exposée par Metrics Server : le dashboard compare les requêtes déclarées par les pods aux capacités allocatables, mais ne montre pas l'utilisation réelle. Ne pas présenter ces séries comme une supervision complète de capacité.

Conséquences pour la suite : conserver le profil dev sans `hostPath`, PVC ni node-exporter; ne pas dupliquer Metrics Server; garder toutes les interfaces en ClusterIP. Ne pas décider de la taille/longévité du stockage de production à partir de la seule capacité libre observée, et confirmer la stratégie de sauvegarde avant d'utiliser un PVC local. Le monitoring sur ce nœud unique n'apporte ni haute disponibilité ni visibilité si l'hôte est éteint.

## Jalons et points d'arrêt

1. **Socle de développement — terminé** : profil isolé Kustomize, comptes de service, RBAC de lecture, services ClusterIP, Prometheus/Grafana/kube-state-metrics et dashboard minimal. Pas d'Ingress ni de secret commité.
2. **Contrats, inventaire et alertes de base — terminé pour le profil dev** : tests de sécurité/configuration, règles Prometheus pour cible indisponible, replicas de déploiement indisponibles, nœud non Ready et absence de métriques de nœud, tests de règles promtool, validation du rendu Kustomize et schémas Kubernetes. L'inventaire du 2026-11-05 a été analysé; les alertes n'ont pas encore de canal de notification.
3. **Choix de production — en attente** : confirmer stratégie stockage, ressources, sécurité, exposition et déploiement à partir de l'inventaire. Aucun apply avant ce jalon et accord explicite.
4. **Extension du profil — bloquée par des décisions d'accès** : décider l'intégration de la consommation CPU/mémoire de Metrics Server; ajouter sondes externes et node-exporter seulement après validation des ports/routes, privilèges/montages hôte, ressources et faux positifs Mercure. Définir les destinataires et le canal des alertes.
5. **Production — hors de portée avant validation utilisateur** : concevoir persistance/rétention, authentification Grafana, configuration d'exposition et isolation réseau. La stack de production devra être protégée contre l'accès d'un workload non fiable/compromis; définir et tester une politique réseau basée sur le CNI réel et les flux nécessaires avant tout déploiement. Écrire les tests correspondants avant les manifests, puis planifier une validation séparée sur cluster.

Exécuter `sh scripts/validate.sh` après toute modification locale. Chaque jalon terminé doit être communiqué avant de commencer un jalon qui requiert des commandes serveur ou une décision de l'utilisateur.

## État connu

- L'application Anima est décrite par des manifests Kubernetes indépendants dans `anima/k8s/`.
- L'API et le frontend sont exposés par des Services ClusterIP; MySQL et MinIO utilisent des volumes persistants locaux.
- La CI Anima construit et publie les images applicatives sur GHCR puis applique certains manifests à l'aide d'un secret kubeconfig facultatif. Elle ne déploie pas de composants de supervision ni de contrôleur d'ingress.
- L'ingress actuel route le frontend, l'API, les fichiers MinIO et Mercure sur `ultralout.re`; le remplacement nginx par Traefik sera expérimenté séparément.
- La topologie réelle, les capacités et classe(s) de stockage, ainsi que l'absence de quotas et de politiques réseau sont documentées ci-dessus. Les durées de rétention, les besoins d'alerte et les modalités d'accès restent à décider.

## Architecture de départ proposée

- **Prometheus** pour la collecte et le stockage de séries temporelles.
- **node-exporter** pour les métriques hôte, uniquement après validation des montages, privilèges et contraintes d'exécution requis par la version retenue.
- **kube-state-metrics** pour l'état des objets Kubernetes, avec une identité dédiée et des droits limités aux ressources effectivement consommées.
- **Grafana** comme interface recommandée, protégée par l'authentification retenue pour le portail. Prometheus ne sera pas exposé publiquement.
- Des **sondes de disponibilité externes** pour les routes publiques principales (site, API, fichiers si testables sans secret, et endpoints Mercure appropriés). Distinguer une sonde HTTP de bout en bout des métriques internes de cluster.

Cette architecture est un point de départ à confirmer après inventaire; elle ne fixe ni chart, ni méthode d'installation, ni versions, ni ressources, ni durée de rétention, ni nom d'hôte.

## Principes de sécurité et d'exploitation

- Déployer les composants dans un namespace de supervision dédié, avec ServiceAccounts et secrets séparés.
- Accorder uniquement les verbes de lecture nécessaires. Refuser toute capacité de créer, modifier ou supprimer des ressources Kubernetes; valider les permissions effectives avec des tests d'autorisation.
- Pour la collecte hôte, limiter les accès au strict nécessaire et utiliser des montages en lecture seule autant que possible. Documenter toute exception de privilège ou de montage avant de la retenir.
- Rendre l'interface accessible uniquement à des utilisateurs autorisés. N'accorder à Grafana que l'accès de lecture aux sources nécessaires.
- Définir requests/limits, stockage, sauvegarde et rétention en fonction des capacités mesurées; prévoir l'effet d'une panne ou saturation du stockage.
- Ne pas inclure de secret, kubeconfig ou donnée de production dans le dépôt. Ne pas réutiliser le kubeconfig privilégié de la CI applicative.
- Le dépôt GitHub `loutre-sup` est maintenant initialisé; toute nouvelle automatisation doit conserver une CI sans credentials cluster.

## Séquencement

1. **Inventaire réalisé** : un seul nœud k3s, Metrics Server existant, stockage local-path sur le disque racine. Actualiser le relevé si le cluster change avant la mise en œuvre.
2. **Valider la conception** : préciser les composants et leur mode d'installation, le namespace, les identités/RBAC, les ressources, les données persistantes, la protection de Grafana et les critères de réussite. Reporter les décisions dans `loutre-archi/` et mettre à jour ce document.
3. **Définir les tests avant les manifests (TDD)** :
   - validation syntaxique et schéma des ressources Kubernetes rendues;
   - assertions sur les cibles Prometheus, labels et configuration des sondes;
   - assertions négatives sur les autorisations Kubernetes (aucune écriture);
   - contrôles des ressources, stockage et exposition réseau attendus;
   - tests de disponibilité des cibles, dashboards et règles d'alerte.
4. **Implémenter par petits changements** les manifests/configurations et faire réussir les contrôles correspondants. Éviter les secrets en clair et les valeurs non confirmées.
5. **Valider sur le cluster** : vérifier les pods/cibles, les requêtes utiles, les alertes, l'accès protégé, les permissions effectives et le comportement lors d'une indisponibilité. Séparer ces contrôles d'intégration des tests statiques de configuration.
6. **Documenter l'exploitation** : accès, mise à jour, sauvegarde/restauration, maintenance, diagnostic et procédure de retrait. Définir les alertes à notifier et leur destinataire avant de les considérer en production.

## CI, versionnement et GitOps

Une CI GitHub Actions sans credentials de cluster est ajoutée dans
`.github/workflows/validate.yml`; elle exécute `sh scripts/validate.sh` sur les PR vers
`master` et les pushes sur `master`. La politique proposée de commits cohérents, tags SemVer
manuels et adoption graduelle d'Argo CD est décrite dans `docs/ci-versioning-gitops.md`.
Les déclarations `gitops/argocd/dev/` limitent une Application à `k8s/dev`, au dépôt et au
namespace `monitoring-dev`, avec une liste blanche des types namespacés. La validation locale
rend ce bundle et teste ses contrats. Argo CD n'est pas installé, les déclarations ne sont pas
appliquées et aucun déploiement automatisé n'est activé. L'observation réelle attend une
identité Kubernetes en lecture seule et une configuration Argo CD sans autorisation de sync
manuel; le bootstrap cluster-scope reste une opération administrateur séparée. Voir
`docs/argocd-dev-observe.md` avant toute installation ou application.

## Critères d'acceptation initiaux

- Les cibles convenues sont joignables et leur état est visible; les sondes signalent correctement la réussite et l'échec des routes sélectionnées.
- Les dashboards donnent une vue exploitable de la santé des nœuds, des workloads et des services visés.
- Les alertes retenues ont été testées de bout en bout et ont un destinataire/documentation.
- Les limites de stockage et la rétention sont documentées et compatibles avec les capacités du cluster.
- Grafana est authentifié; Prometheus et les endpoints internes de métriques ne sont pas publiquement accessibles.
- Les tests confirment que les identités de collecte ne peuvent pas modifier les ressources Kubernetes; l'accès hôte est restreint et justifié.
- Les étapes de déploiement, mise à jour et reprise sont documentées, et les vérifications reproductibles sont automatisées autant que possible.

## Décisions de production à résoudre

- Quelles données persistantes/but de sauvegarde sont acceptables avec le stockage local-path non extensible et une politique de reclaim `Delete`?
- Quelle rétention est nécessaire et soutenable sur le disque racine, sans compromettre les volumes applicatifs?
- La supervision CPU/mémoire doit-elle inclure une intégration spécifique aux métriques Metrics Server, ou peut-elle rester hors du premier lot Prometheus?
- Où exécuter les sondes externes, et comment tester Mercure sans créer de faux positifs?
- Quel mécanisme d'authentification et quels groupes autoriseront Grafana?
- Quelles alertes nécessitent une notification, par quel canal et à qui?
- Quel processus versionné déploiera la stack, distinct des déploiements des images Anima?
