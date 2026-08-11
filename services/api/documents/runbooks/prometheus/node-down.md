# Diagnostic d'un nœud absent de Prometheus

## Objectif

Diagnostiquer un nœud de l'infrastructure OpenStack qui n'apparaît plus comme UP dans Prometheus ou dont les métriques ne remontent plus dans Cortex.

Cette procédure peut être utilisée pour les nœuds suivants :

- controller
- compute1
- compute2
- storage

## Identifier le nœud concerné

Déterminer quel nœud ne remonte plus ses métriques dans Cortex ou apparaît comme DOWN dans Prometheus.

Vérifier son hostname ainsi que l'adresse utilisée par Prometheus pour le scrape.

Exemples de nœuds :

- controller
- compute1
- compute2
- storage

## Vérification de la cible Prometheus

Dans Prometheus, vérifier les targets `node_exporter`.

Le nœud concerné doit normalement apparaître avec le statut :

```text
UP