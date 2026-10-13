# CI and supply chain

Push to `main` -> gates (gitleaks, ruff, eslint, audits, tests) -> build -> Trivy
gate (HIGH/CRITICAL) -> push to GHCR tagged `:<git-sha>` -> Syft SBOM -> cosign
keyless signature + SBOM attestation -> SLSA build provenance -> (optional) PR in
`cortex-gitops` bumping the image digest. CI never deploys.

Images: `ghcr.io/lakhal-farah/cortex-{api,web,collector}` (deploy by digest, never `:latest`).

## Verify an image

    cosign verify ghcr.io/lakhal-farah/cortex-api@sha256:<digest> \
      --certificate-oidc-issuer https://token.actions.githubusercontent.com \
      --certificate-identity-regexp '^https://github.com/LAKHAL-Farah/Cortex/\.github/workflows/ci\.yml@'

    cosign verify-attestation --type cyclonedx <same ref and flags>
    gh attestation verify oci://ghcr.io/lakhal-farah/cortex-api@sha256:<digest> -R LAKHAL-Farah/Cortex

## Gates

`STRICT_GATES` in `ci.yml` controls ruff, eslint, `npm audit` and `pip-audit`
(report-only while `"false"`). Gitleaks and Trivy always block. Reviewed Trivy
exceptions go in `.trivyignore` with a reason and an expiry.
