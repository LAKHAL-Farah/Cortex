# Recovery cinder-backup après une panne RabbitMQ

## Objectif

Cette procédure décrit comment diagnostiquer et rétablir le service
`cinder-backup` lorsqu'une interruption ou un problème RabbitMQ empêche
Cinder de communiquer correctement avec les autres composants OpenStack.

## Identifier le nœud concerné

Déterminer le nœud qui héberge le service `cinder-backup`.

Identifier également le nœud ou le service qui héberge RabbitMQ.

La méthode de diagnostic dépend du type de déploiement utilisé :

- services systemd ;
- déploiement conteneurisé.

## Vérifications RabbitMQ

Avant de redémarrer `cinder-backup`, vérifier que RabbitMQ est disponible.

### Déploiement systemd

Vérifier l'état du service :

```bash
systemctl status rabbitmq-server