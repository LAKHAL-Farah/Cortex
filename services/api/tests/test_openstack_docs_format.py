from app.services.openstack_docs import format as fmt

RAW = (
    "Duplicated or deleted OVN agents\u00c2\u00b6\nThe\n\u00e2\u0080\u009covn-controller\u00e2\u0080\u009d\n"
    "process runs on every host.\n$\nopenstack network agent list\n"
    "+----+------+\n| ID | Host |\n+----+------+\n| a1 | u20ovn |\n+----+------+\n"
    "Then list chassis:\n$ sudo ovn-sbctl list Chassis | grep name\nhostname : u20ovn\n"
)


def test_fix_mojibake_repairs_latin1_and_cp1252_variants_and_keeps_real_text():
    assert fmt.fix_mojibake("a\u00c2\u00b6") == "a\u00b6"
    assert fmt.fix_mojibake("\u00e2\u20ac\u02dcx\u00e2\u20ac\u2122") == "\u2018x\u2019"
    assert fmt.fix_mojibake("caf\u00e9 ok") == "caf\u00e9 ok"


def test_clean_inline_drops_pilcrow():
    assert fmt.clean_inline("Heading\u00c2\u00b6") == "Heading"


def test_markdown_has_fenced_command_and_real_table_and_no_duplicate_heading():
    md = fmt.doc_text_to_markdown(RAW, "Duplicated or deleted OVN agents\u00c2\u00b6")
    assert "```bash\n$ openstack network agent list\n```" in md
    assert "| ID | Host |\n| --- | --- |\n| a1 | u20ovn |" in md
    assert "```bash\n$ sudo ovn-sbctl list Chassis | grep name\nhostname : u20ovn\n```" in md
    assert "\u201covn-controller\u201d process runs on every host." in md
    assert "Duplicated or deleted" not in md
    assert "\u00b6" not in md and "\u00c2" not in md


def test_excerpt_truncates_on_block_boundary():
    text = "\n\n".join(f"Paragraph number {i} " + "word " * 60 for i in range(20))
    md = fmt.doc_text_to_markdown(text, None, max_chars=600)
    assert "excerpt truncated" in md


def test_looks_like_index_detects_toc_but_not_real_content():
    toc = "\n".join(["Network components", "Overlay protocols", "DNS Integration", "QoS"] * 8)
    assert fmt.looks_like_index(toc)
    assert not fmt.looks_like_index(RAW)


def test_command_split_into_token_lines_is_rejoined_and_prose_stays_out():
    raw = (
        "Neutron uses them.\n$\nopenstack\nnetwork agent list -c ID -c \"Agent Type\"\n"
        "+--+--+\n| ID | Host |\n+--+--+\n| a | b |\n+--+--+\n"
        "List the \u201cChassis\u201d registers:\n$\nsudo\novn-sbctl list Chassis\n| grep name\n"
        "hostname : u20ovn\nDelete the stale \u201cChassis\u201d register:\n$\nsudo\novn-sbctl destroy Chassis ce9a\n"
    )
    md = fmt.doc_text_to_markdown(raw)
    assert '```bash\n$ openstack network agent list -c ID -c "Agent Type"\n```' in md
    assert "```bash\n$ sudo ovn-sbctl list Chassis | grep name\nhostname : u20ovn\n```" in md
    assert "```bash\n$ sudo ovn-sbctl destroy Chassis ce9a\n```" in md
    assert "List the \u201cChassis\u201d registers:" in md.replace("\n\n", " ")
