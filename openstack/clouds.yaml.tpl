# Template for the clouds.yaml mounted into the API at /etc/openstack (prod).
# SAFE TO COMMIT: passwords are placeholders. Do not put real secrets in this file.
#
# Generate the real file (gitignored) with:   ./openstack/render-clouds.sh
# It replaces each quoted @@NAME@@ token below with the value of NAME from infra/.env.prod.
#
# Everything except the passwords is edited here, in git.
clouds:
  cortex-reader:
    auth:
      auth_url: http://10.0.1.4:5000/v3
      username: cortex-reader
      password: "@@OS_READER_PASSWORD@@"
      project_name: admin
      user_domain_name: Default
      project_domain_name: Default
    region_name: RegionOne
    interface: internal
    identity_api_version: 3
  cortex-operator:
    auth:
      auth_url: http://10.0.1.4:5000/v3
      username: cortex-operator
      password: "@@OS_OPERATOR_PASSWORD@@"
      project_name: admin
      user_domain_name: Default
      project_domain_name: Default
    region_name: RegionOne        # check: openstack endpoint list -c Region -f value | sort -u
    interface: internal           # copy whatever cortex-reader uses
    identity_api_version: 3
    # If your Kolla API uses TLS, copy cortex-reader's cacert line, and the CA file
    # must be inside the same mounted directory (the container sees only that dir).
