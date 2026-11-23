"""Biolink v4.4.2 edge qualifiers for a fact, from the fields it already has. Values come only from Biolink enums:
KnowledgeLevelEnum, AgentTypeEnum, GeneOrGeneProductOrChemicalEntityAspectEnum, DirectionQualifierEnum."""

# knowledge level: statements that are statistical by nature (enrichment, GWAS, correlation)
_STATISTICAL = {"associates_with", "correlates_with", "positively_correlated_with", "negatively_correlated_with",
                "associated_with_likelihood_of", "associated_with_increased_likelihood_of",
                "associated_with_decreased_likelihood_of", "coexpressed_with"}

# object aspect and direction qualifier per relation
_ASPECT_DIRECTION = {
    "upregulates": ("abundance", "increased"), "downregulates": ("abundance", "decreased"),
    "has_increased_amount": ("abundance", "increased"), "has_decreased_amount": ("abundance", "decreased"),
    "activates": ("activity", "increased"), "inhibits": ("activity", "decreased"),
    "regulates_expression_of": ("expression", ""),
}


def qualifiers(record: dict) -> dict:
    """knowledge_level, agent_type, object_aspect_qualifier, object_direction_qualifier for one fact."""
    rel = getattr(record.get("relation"), "value", record.get("relation")) or ""
    if rel in _STATISTICAL:
        level = "statistical_association"
    elif record.get("evidence") == "predicted":
        level = "prediction"
    else:
        level = "knowledge_assertion"
    aspect, direction = _ASPECT_DIRECTION.get(rel, ("", ""))
    return {"knowledge_level": level,
            "agent_type": "image_processing_agent" if record.get("section") == "figure" else "text_mining_agent",
            "object_aspect_qualifier": aspect, "object_direction_qualifier": direction}
