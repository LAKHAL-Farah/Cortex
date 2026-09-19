"""Curated seed list of official OpenStack documentation pages to embed
into the `cortex-openstack-official-docs` Qdrant collection (v1.0, see
docs/architecture/adr-0010-openstack-expert-v1-expansion.md).

Deliberately small and hand-picked rather than a full-site crawl -- the
original planning doc's own data-source table (§6) is explicit that this
is the right amount of investment for this pipeline: "don't over-invest
in the docs embedding pipeline early -- the catalog is where the real
value is... add the docs embedding once that catalog runs out of easy
wins." One admin/troubleshooting entry point per service the curated
catalog (openstack_expert_catalog.py) already covers, so `run_ingest`
(ingest.py) has a small, bounded, predictable set of pages to fetch on
every run instead of an open-ended crawl that would need its own
politeness/rate-limiting concerns.

`category` matches openstack_expert_catalog.Category exactly (see that
module's `Category` Literal) -- not wired into retrieval filtering yet in
v1.0 (a category-scoped docs search would need the same category the
symptom matcher scored the query against), but keeping the taxonomy
aligned now means search.py can add that filter later without touching
this list. `service` is a short label (matches openstack_expert_catalog
entries' spirit, not a formal enum) used as the Qdrant payload's
`service` field so a retrieved chunk can say "from the Cinder docs"
rather than just a bare URL.

Every URL below is `/latest/` rather than pinned to a specific release
(this deployment tracks 2024.1 Caracal, see docs/knowledge/README.md) --
`/latest/` stays correct without a code change as upstream cuts new
releases; the alternative, pinning `/2024.1/`, would silently go stale
the day this deployment upgrades and nobody remembers to bump this file.

These are real docs.openstack.org URLs, chosen because they're the
project's own stable, long-lived admin/troubleshooting guide entry
points for each service -- but this list is authored without a live
fetch to confirm every path still resolves (see ScrapeError's handling
in scraper.py/ingest.py). A URL that's moved or 404s is skipped with a
logged warning and reported in `DocsIngestResult.failed_urls`; it never
fails the whole ingest run. Fix a stale URL here the same way you'd fix
a stale doc_ref in openstack_expert_catalog.py -- a small, reviewed edit,
not a pipeline rewrite.

2026-09-18 revision: swapped four entries that were resolving to
Sphinx table-of-contents/landing pages (near-zero embeddable prose, all
links) for the actual content pages those tocs point to, after a manual
fetch confirmed each one still resolves and has real body text. Nova's
original `admin/troubleshooting.html` had moved/renamed upstream and was
404ing outright (confirmed via ingest's own `failed_urls` reporting) --
fixed to the page it moved to. Keystone's `admin/index.html` entry was
NOT changed here: its toctree links to a page titled "Troubleshoot the
Identity service", but the resolved filename/slug for that page under
`/latest/` wasn't confirmed live, so it's left as-is rather than
guessing a URL that might silently 404. Revisit this one entry the same
way once someone can confirm the live path by hand.
"""
from typing import TypedDict


class DocSource(TypedDict):
    url: str
    category: str  # matches openstack_expert_catalog.Category
    service: str


OFFICIAL_DOC_SOURCES: list[DocSource] = [
    {
        # Was admin/troubleshooting.html -- that page moved/renamed
        # upstream and was 404ing (confirmed via a real ingest run's
        # failed_urls). This is where it moved to.
        "url": "https://docs.openstack.org/nova/latest/admin/support-compute.html",
        "category": "compute",
        "service": "nova",
    },
    {
        "url": "https://docs.openstack.org/nova/latest/admin/index.html",
        "category": "compute",
        "service": "nova",
    },
    {
        "url": "https://docs.openstack.org/cinder/latest/admin/blockstorage-troubleshoot.html",
        "category": "storage",
        "service": "cinder",
    },
    {
        "url": "https://docs.openstack.org/cinder/latest/admin/index.html",
        "category": "storage",
        "service": "cinder",
    },
    {
        # Was admin/index.html -- confirmed (by fetch) to be a pure
        # toctree/landing page with no real prose. Neutron's own admin
        # guide has no single generic troubleshooting page; this is its
        # most substantial troubleshooting-flavored content page
        # (OVN is the default ML2 driver for new deployments).
        "url": "https://docs.openstack.org/neutron/latest/admin/ovn/troubleshooting.html",
        "category": "network",
        "service": "neutron",
    },
    {
        # NOT swapped -- see module docstring's 2026-09-18 note. This
        # index page's toctree links to a "Troubleshoot the Identity
        # service" page, but the exact resolved URL under /latest/
        # wasn't confirmed live; left as the safer, already-working
        # (if thin) source rather than guessing.
        "url": "https://docs.openstack.org/keystone/latest/admin/index.html",
        "category": "identity",
        "service": "keystone",
    },
    {
        # Was admin/index.html -- confirmed (by fetch) to be a pure
        # toctree/landing page. This is the actual troubleshooting guide
        # it links to (covers image download/property-protection/instance
        # build issues, which is the content this pipeline actually wants).
        "url": "https://docs.openstack.org/glance/latest/admin/troubleshooting.html",
        "category": "image",
        "service": "glance",
    },
    {
        # Was admin/index.html -- confirmed (by fetch) to be a pure
        # toctree/landing page. Horizon has no dedicated troubleshooting
        # doc, so this is its most operationally-relevant content page
        # (starting/stopping/inspecting instances from the dashboard).
        "url": "https://docs.openstack.org/horizon/latest/admin/manage-instances.html",
        "category": "host",
        "service": "horizon",
    },
]