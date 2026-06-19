# Re-export from taxonomy.py for backward compatibility.
# This file is kept so existing imports do not break.
# New code should import directly from src.schema.taxonomy or src.schema.
from .taxonomy import (  # noqa: F401
    RelationType, EntityType, SectionLabel,
    TAXONOMY, CROSS_SCHEMA_MAP, BIOLINK_MAPPING, OPPOSING_RELATIONS,
)
