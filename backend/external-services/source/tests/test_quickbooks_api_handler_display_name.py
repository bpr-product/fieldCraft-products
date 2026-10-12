from source.Utils import QuickBooksAPIHandler as qbo_api
from source.Utils import constants as const


def test_short_code_uses_first_six_database_value_characters():
    assert qbo_api.build_qbo_short_code("8a77df28-02d2-44a6-bde5-dc091f07d99d") == "8A77DF"


def test_build_qbo_display_name_uses_entity_type_constant_and_short_code():
    assert (
        qbo_api.build_qbo_display_name(
            "Jordan G",
            const.QBO_ENTITY_TYPE_EMPLOYEE,
            "8a77df28-02d2-44a6-bde5-dc091f07d99d",
        )
        == "Jordan G-employee-8A77DF"
    )


def test_build_qbo_display_name_does_not_normalize_base_name():
    assert (
        qbo_api.build_qbo_display_name(
            "Jordan   G",
            const.QBO_ENTITY_TYPE_CUSTOMER,
            "bcf89794-0a6e-4ac6-a6a1-126fd49932d6",
        )
        == "Jordan   G-customer-BCF897"
    )
