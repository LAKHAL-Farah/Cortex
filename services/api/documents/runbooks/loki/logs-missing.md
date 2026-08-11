# Vérification des logs d'un nœud dans Loki

## Objectif

Cette procédure permet de vérifier que les journaux d'un nœud de
l'infrastructure Cortex sont correctement collectés par Promtail
et envoyés vers Loki.

Cette procédure peut être utilisée pour les nœuds suivants :

- controller
- compute1
- compute2
- storage

## Identifier le nœud concerné

Déterminer d'abord quel nœud ne remonte plus ses journaux dans Cortex.

Exemples :

- controller
- compute1
- compute2
- storage

Récupérer également son adresse IP si nécessaire.

## Vérifier Loki

Depuis le nœud qui héberge Loki :

```bash
curl http://127.0.0.1:3100/ready


## Vérification dans Grafana

Dans Grafana Explore, sélectionner la source Loki puis rechercher les logs
avec le label correspondant au nœud compute1.

Vérifier que le message CORTEX_LOKI_TEST apparaît avec un horodatage récent.
