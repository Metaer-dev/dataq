import frappe
import pandas as pd
from frappe import _
from frappe.core.doctype.data_import.importer import get_df_for_column_header
from ..util import (
    snake_to_camel,
    convert_str_to_standard,
    is_label_return_field,
    get_rules_cache,
)


def doctype_validate(doctype, which_event):
    if frappe.has_permission(doctype.doctype, which_event):
        data = doctype.as_dict()
        if doctype.doctype == "DocType":
            return
        gx_validate(doctype.doctype, data if isinstance(data, list) else [data], False)


def gx_validate(doctype, collect, force=True):
    """
    Args:
        doctype: that needs to be checked
    Returns:
        validate result
    """
    from great_expectations import analytics

    analytics.config.ENV_CONFIG.gx_analytics_enabled = False

    import great_expectations as gx
    from great_expectations import expectations as gxe
    import copy

    # from collections import defaultdict

    rules = get_rules_cache(doctype)

    if not force and not rules:
        return True, None

    df = copy.deepcopy(collect)
    if not isinstance(df, pd.DataFrame):
        convert_str_to_standard(df)
        df = pd.DataFrame.from_records(df)

    if "doc" in df:
        # data come from Drive
        df = pd.DataFrame.from_records(df["doc"])

    if not rules:
        return True, df

    context = gx.get_context()

    # Create an Expectation Suite
    suite_name = "frappy_expectation_suite"
    suite = gx.ExpectationSuite(name=suite_name)
    # Add the Expectation Suite to the Data Context
    suite = context.suites.add(suite)

    data_source_name = "pandas"
    data_source = context.data_sources.add_pandas(data_source_name)

    data_asset_name = "df_data_asset"
    data_asset = data_source.add_dataframe_asset(name=data_asset_name)

    batch_definition_name = "df_batch_definition"
    batch_definition = data_asset.add_batch_definition_whole_dataframe(
        batch_definition_name
    )

    backup = copy.deepcopy(rules)
    for which_gx, args in rules.items():

        if "column" in args:
            args["column"] = get_df_for_column_header(doctype, args["column"]).fieldname

        gx_function = getattr(gx.expectations, which_gx)

        args.pop("from")
        expectation = gx_function(**args)
        # Add the previously created Expectation to the Expectation Suite
        suite.add_expectation(expectation)

    definition_name = "my_validation_definition"
    validation_definition = gx.ValidationDefinition(
        data=batch_definition, suite=suite, name=definition_name
    )

    # Add the Validation Definition to the Data Context
    validation_definition = context.validation_definitions.add(validation_definition)

    batch_parameters = {"dataframe": df}
    validation_results = validation_definition.run(batch_parameters=batch_parameters)

    if not validation_results.success:
        msg = []
        for one in validation_results.results:
            if not one.success:
                gx_function = snake_to_camel(one.expectation_config.type)

                try:
                    which_rule = backup[gx_function]["from"]
                    msg.append(
                        f"<a href='/app/data-rules/{which_rule}' target='_blank'>{gx_function}</a>"
                    )
                except:
                    which_rule = gx_function
                    msg.append(
                        f"<a href='/app/gx-function/{gx_function}' target='_blank'>{gx_function}</a>"
                    )

        frappe.throw(
            msg=msg, title=_("The following data rules did not pass"), as_list=True
        )
    return validation_results, df


@frappe.whitelist()
def get_child_table_data(parent_doctype, parent_name, child_table_fieldname):
    try:
        # 检查父文档的权限
        if not frappe.has_permission(parent_doctype, "read", parent_name):
            frappe.throw(("No permission to read {0}").format(parent_doctype))

        parent_doc = frappe.get_doc(parent_doctype, parent_name)

        # 获取子表数据
        child_data = parent_doc.get(child_table_fieldname)

        # 转换为可序列化的格式
        return [child.as_dict() for child in child_data]
    except frappe.PermissionError:
        frappe.throw(("No permission to access this data"))
    except Exception as e:
        frappe.log_error(f"Error in get_child_table_data: {str(e)}")
