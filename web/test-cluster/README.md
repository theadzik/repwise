# Trying the service on the cluster

The service in a namespace of its own, `repwise-test`, applied by hand with
`kubectl` and outside ArgoCD. It is the local stack in `../compose.yaml` moved
onto the cluster: PostgreSQL through CloudNativePG, two API replicas, a worker,
and a proxy that stands in for oauth2-proxy. The same stand-ins take the place
of repwise and garminconnect, so nothing here reaches Garmin.

The real deployment is in the homelab repository. Nothing here is it.

## Before

The image has to be published by `.github/workflows/web.yml`, because the
cluster admits a `ghcr.io/theadzik` image only when that workflow signed it.
Set `newTag` in `kustomization.yaml` to the tag it published.

## Up

```bash
kubectl create namespace repwise-test
kubectl -n repwise-test create secret generic repwise-web \
  --from-literal=TOKEN_KEYS="$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
kubectl kustomize --load-restrictor LoadRestrictionsNone web/test-cluster |
  kubectl apply -f -
kubectl -n repwise-test wait --for=condition=Ready cluster.postgresql.cnpg.io/repwise-db --timeout=5m
kubectl -n repwise-test rollout status deployment/repwise-api deployment/repwise-worker
kubectl -n repwise-test port-forward service/repwise-proxy 8088:8080
```

The page is then at <http://127.0.0.1:8088>. The stand-in signs in to Garmin
with the password `right`, asks an email starting `mfa@` for a code, and takes
`123456`.

## Down

```bash
kubectl delete namespace repwise-test
```

The database's volume goes with it: its storage class deletes.
