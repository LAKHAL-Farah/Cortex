# Diagnostic d'une instance OpenStack en état ERROR

## Objectif

Identifier la cause d'une instance OpenStack passée en état ERROR.

## Première vérification

Afficher les détails de l'instance :

openstack server show <instance-id>

Vérifier particulièrement le champ fault.

## Vérification Nova

Identifier le compute node sur lequel l'instance a été planifiée.

Vérifier ensuite l'état du service nova-compute.

## Vérification des logs

Consulter les logs Nova associés à l'instance et rechercher l'erreur correspondant au moment de l'échec.

## Vérification des ressources

Vérifier que le compute node dispose encore de suffisamment de CPU, RAM et stockage.

## Validation

Après correction, recréer ou redémarrer l'instance selon la procédure opérationnelle utilisée par l'infrastructure.