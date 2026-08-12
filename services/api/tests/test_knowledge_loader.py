from app.services.knowledge.loader import chunk_markdown, load_knowledge_chunks

SAMPLE_DOC = """# Nova — Compute Service

Intro paragraph about Nova.

## Components

| Component | Role |
|---|---|
| nova-api | HTTP entry point |

## Containers (Kolla-Ansible)

Some container details here.
"""


def test_chunk_markdown_splits_on_headings():
    chunks = chunk_markdown(SAMPLE_DOC, "service-detail/nova.md", "Nova — Compute Service", "service-detail")

    # H1 title + two H2 sections
    assert len(chunks) == 3
    assert chunks[0].heading == "Nova — Compute Service"
    assert chunks[1].heading == "Components"
    assert chunks[2].heading == "Containers (Kolla-Ansible)"
    assert all(c.source_path == "service-detail/nova.md" for c in chunks)
    assert all(c.doc_title == "Nova — Compute Service" for c in chunks)
    assert all(c.category == "service-detail" for c in chunks)


def test_chunk_ids_are_stable_across_runs():
    first = chunk_markdown(SAMPLE_DOC, "service-detail/nova.md", "Nova", "service-detail")
    second = chunk_markdown(SAMPLE_DOC, "service-detail/nova.md", "Nova", "service-detail")
    assert [c.id for c in first] == [c.id for c in second]


def test_chunk_ids_differ_by_source_path():
    a = chunk_markdown(SAMPLE_DOC, "service-detail/nova.md", "Nova", "service-detail")
    b = chunk_markdown(SAMPLE_DOC, "service-detail/neutron.md", "Neutron", "service-detail")
    assert {c.id for c in a}.isdisjoint({c.id for c in b})


def test_load_knowledge_chunks_walks_subdirectories(tmp_path):
    knowledge_dir = tmp_path / "knowledge"
    (knowledge_dir / "service-detail").mkdir(parents=True)
    (knowledge_dir / "topology.md").write_text("# Topology\n\nSome topology text.\n")
    (knowledge_dir / "service-detail" / "nova.md").write_text(SAMPLE_DOC)

    chunks = load_knowledge_chunks(knowledge_dir)
    sources = {c.source_path for c in chunks}

    assert sources == {"topology.md", "service-detail/nova.md"}
    categories = {c.source_path: c.category for c in chunks}
    assert categories["topology.md"] == "general"
    assert categories["service-detail/nova.md"] == "service-detail"


def test_load_knowledge_chunks_is_deterministically_ordered(tmp_path):
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "b.md").write_text("# B\n\ntext\n")
    (knowledge_dir / "a.md").write_text("# A\n\ntext\n")

    chunks = load_knowledge_chunks(knowledge_dir)
    assert [c.source_path for c in chunks] == ["a.md", "b.md"]


def test_oversized_section_is_split_further(tmp_path):
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    long_body = "\n\n".join(f"Paragraph {i} " + ("filler " * 80) for i in range(10))
    (knowledge_dir / "big.md").write_text(f"# Big Doc\n\n## Long Section\n\n{long_body}\n")

    chunks = load_knowledge_chunks(knowledge_dir)
    long_section_chunks = [c for c in chunks if c.heading == "Long Section"]
    assert len(long_section_chunks) > 1
    assert all(len(c.text) <= 2000 for c in long_section_chunks)
