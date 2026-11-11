import pytest

from app.agents.tools.schemas import DynamicToolSchemaCompiler
from app.schemas.entities import (
    get_default_apex_bank_entity_schema,
    get_default_cloudpulse_entity_schema,
    get_default_medicare_entity_schema,
    get_default_olist_entity_schema,
)


def test_four_industry_metamodels():
    olist = get_default_olist_entity_schema()
    cloudpulse = get_default_cloudpulse_entity_schema()
    apex = get_default_apex_bank_entity_schema()
    medicare = get_default_medicare_entity_schema()

    assert len(olist.entities) >= 3
    assert len(cloudpulse.entities) >= 3
    assert len(apex.entities) >= 3
    assert len(medicare.entities) >= 3

    # Check pattern matching
    olist_patterns = olist.get_pattern_map()
    assert "order" in olist_patterns
    assert olist_patterns["order"].search("ORD-99102") is not None

    cloud_patterns = cloudpulse.get_pattern_map()
    assert "workspace" in cloud_patterns
    assert cloud_patterns["workspace"].search("WS-771a8b") is not None

    apex_patterns = apex.get_pattern_map()
    assert "account" in apex_patterns
    assert "transaction" in apex_patterns
    assert apex_patterns["account"].search("ACC-440192") is not None
    assert apex_patterns["transaction"].search("TXN-99120") is not None

    medicare_patterns = medicare.get_pattern_map()
    assert "patient" in medicare_patterns
    assert "appointment" in medicare_patterns
    assert "prescription" in medicare_patterns
    assert medicare_patterns["appointment"].search("APT-1092") is not None
    assert medicare_patterns["prescription"].search("RX-44912") is not None


def test_dynamic_tools_compile_for_all_industries():
    for schema_func in (
        get_default_olist_entity_schema,
        get_default_cloudpulse_entity_schema,
        get_default_apex_bank_entity_schema,
        get_default_medicare_entity_schema,
    ):
        schema = schema_func()
        for ent in schema.entities:
            tools = DynamicToolSchemaCompiler.compile_for_entity(
                entity_name=ent.name,
                identifier_pattern=ent.identifier_pattern,
                description=ent.description,
            )
            assert f"get_{ent.name}_summary" in tools
            assert f"list_customer_{ent.name}s" in tools
            assert "parameters" in tools[f"get_{ent.name}_summary"]
