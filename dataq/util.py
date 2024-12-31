from functools import wraps
import frappe
from frappe.exceptions import DoesNotExistError
from frappe.translate import get_all_translations
from frappe import _


def get_decorator_skip_fatherclass_methods_in_childclass(*methods_to_skip):
    """
    Gets the decorator function used to customize the lines of code to skip when initializing a parent class
    :param skip_lines: lines or snippets of code to skip
    :example
        class B(A):
            @get_decorator_skip_fatherclass_methods_in_childclass()('dosomething', 'other_method')
            def __init__(self, arg):
                super().__init__(arg)
    """

    def skip_methods(*methods_to_skip):
        def decorator(init_func):
            @wraps(init_func)
            def wrapper(self, *args, **kwargs):
                original_methods = {}
                for method in methods_to_skip:
                    original_methods[method] = getattr(self, method)
                    setattr(self, method, lambda *a, **kw: None)

                try:
                    init_func(self, *args, **kwargs)
                finally:
                    for method, original in original_methods.items():
                        setattr(self, method, original)

            return wrapper

        return decorator

    return skip_methods


def get_decorator_kip_fatherclass_anything_in_child():
    """
    Get the decorator function used to customize the lines of code to be skipped when the parent class is initialized.
    :param skip_lines: lines or snippets of code to skip
    :example
        class B(A):
            @get_decorator_skip_fatherclass_methods_in_childclass(
                "res = self.dosomething()",  # skip this method
                "if arg:"                    # skip `if` block
            )
            def __init__(self, arg):
                super().__init__(arg)
    """
    import inspect
    import textwrap

    def custom_init(*skip_lines):

        def decorator(init_func):
            def wrapper(self, *args, **kwargs):
                parent_init = getattr(super(self.__class__, self), "__init__")
                source = inspect.getsource(parent_init)

                lines = source.split("\n")
                for i, line in enumerate(lines):
                    if "def __init__" in line:
                        lines = lines[i + 1 :]
                        break

                source = "\n".join(lines)
                source = textwrap.dedent(source)

                modified_lines = []
                skip_lines_lower = [line.strip().lower() for line in skip_lines]

                current_lines = source.split("\n")
                i = 0
                while i < len(current_lines):
                    line = current_lines[i].strip()
                    skip_this_line = False

                    for skip_line in skip_lines_lower:
                        if skip_line in line.lower():
                            skip_this_line = True
                            if line.startswith("if"):
                                block_indent = len(current_lines[i]) - len(
                                    current_lines[i].lstrip()
                                )
                                i += 1
                                while i < len(current_lines):
                                    next_line = current_lines[i]
                                    if (
                                        next_line.strip()
                                        and len(next_line) - len(next_line.lstrip())
                                        <= block_indent
                                    ):
                                        i -= 1
                                        break
                                    i += 1
                            break

                    if not skip_this_line and line:
                        modified_lines.append(current_lines[i])
                    i += 1

                modified_source = "\n".join(modified_lines)

                namespace = {}
                exec(
                    f"def modified_init(self, *args, **kwargs):\n{textwrap.indent(modified_source, '    ')}",
                    globals(),
                    namespace,
                )

                namespace["modified_init"](self, *args, **kwargs)

            return wrapper

        return decorator

    return custom_init


def update_cache_through_validation(func):
    """
    Used to validate the cache, and if validation fails, the data is re-fetched from the database and cached
    :param validate_func: Validation function, receives the function return value as a parameter, and returns True or False
    :return the cache value after validation

    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        # get validate function from kwargs
        validate_func = kwargs.pop("func", None)
        validate_args = kwargs.pop("args", None)

        # get the caceh first time
        value = func(*args, **kwargs)

        # If a validation function is provided and validation fails
        if validate_func and not validate_func(value, validate_args):
            # clear cache
            func.clear_cache()

            # Retrieve the value for the second time
            value = func(*args, **kwargs)

            # The second validation, if it is still an error, throws an error and logs
            if not validate_func(value):
                frappe.log_error(f"validation failed：{func.__name__}")
                frappe.throw("cache validation failed")
        return value

    return wrapper


def update_cache_for_get(func):
    """
    When retrieving data in the cache fails, retrieve it from the database again and update the cache.
    :param
        get_func: Getter function, receive function return value as parameter, return the obtained value or None

    :return required values
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        validate_func = kwargs.pop("func", None)
        validate_args = kwargs.pop("args", None)
        value, func_call_key = func(*args, **kwargs)
        ret = validate_func(value, validate_args)
        if validate_func and not ret:
            # get translation in db
            ret = frappe.get_value(
                "Translation",
                {"translated_text": validate_args["key"]},
                ["source_text"],
            )
            if not ret:
                frappe.throw(
                    _(
                        "Validation failed because the doctype could not be found. You should check your files or data, such as whether your file name is correct"
                    )
                )
            else:
                user = kwargs.get("user", None)
                ttl = kwargs.get("ttl", None)
                shared = kwargs.get("shared", False)
                func_call_key_cache = {}
                if frappe.cache.exists(func_call_key, user=user, shared=shared):
                    func_call_key_cache = frappe.cache.get_value(
                        func_call_key, user=user, shared=shared
                    )
                else:
                    func_call_key_cache = func(*args, **kwargs)
                func_call_key_cache[validate_args["key"]] = ret
                frappe.cache.set_value(
                    func_call_key,
                    func_call_key_cache,
                    expires_in_sec=ttl,
                    user=user,
                    shared=shared,
                )

        return ret

    return wrapper


def redis_cache_with_key(flag=""):
    """Decorator to cache method calls and its return values in Redis

    :param
        key: Different caches in the same cache class can be distinguished by setting the key.
        ttl: The expiration time of redis cache, which does not expire by default
        user: `true` should cache be specific to session user.
                shared: `true` should cache be shared across sites
    """

    def wrapper(func=None):
        func_key = f"{func.__module__}.{func.__qualname__}"

        def clear_cache():
            frappe.cache.delete_keys(func_key)

        func.clear_cache = clear_cache

        @wraps(func)
        def redis_cache_wrapper(*args, **kwargs):
            func_call_key = func_key + "." + kwargs.get(flag)
            user = kwargs.get("user", None)
            ttl = kwargs.get("ttl", None)
            shared = kwargs.get("shared", False)
            if frappe.cache.exists(func_call_key, user=user, shared=shared):
                return (
                    frappe.cache.get_value(func_call_key, user=user, shared=shared),
                    func_call_key,
                )
            val = func(*args, **kwargs)

            frappe.cache.set_value(
                func_call_key, val, expires_in_sec=ttl, user=user, shared=shared
            )
            return val, func_call_key

        return redis_cache_wrapper

    return wrapper


def get_func(reverse_dict, validate_args):
    translated = ""
    try:
        zh = validate_args["key"]
        translated = reverse_dict[zh]
    except (KeyError, DoesNotExistError) as e:
        return None
    return translated


@update_cache_for_get
@redis_cache_with_key(flag="lang")
def reverse_all_translation_to_dict(lang):
    # Get all translations
    translations = get_all_translations(lang)

    # Build reverse mapping
    reverse_dict = {v: k for k, v in translations.items()}
    return reverse_dict


def get_original_doc_name(
    doctype_name=None,
    app=None,
    get_func=get_func,
    lang=(frappe.session.data.lang or "en"),
):
    original_doc_name = frappe.db.exists("DocType", doctype_name)
    if original_doc_name:
        return original_doc_name
    try:
        get_all_translations(lang)[doctype_name]
        original_doc_name = doctype_name
    except KeyError as e:
        original_doc_name = reverse_all_translation_to_dict(
            lang=lang,
            func=get_func,
            args={"key": doctype_name, "app": app},
            ttl=None,
            user=None,
            shared=False,
        )

    return original_doc_name


def __generate_request_cache_key(args: tuple, kwargs: dict):
    """Generate a key for the cache."""
    if not kwargs:
        return hash(args)
    return hash((args, frozenset(kwargs.items())))


def snake_to_camel(s):
    """Convert a string in underscore format to camelCase format

    :param
        s: string in underscore format

    :return
        string in camelCase format
    """

    words = s.split("_")
    return "".join(word.title() for word in words)


def convert_str_to_standard(data):
    """
    Convert numbers of type string in nested dictionary to numbers

    :param
        data: nested dictionaries

    :return
        converted nested dictionary
    """
    import ast

    if isinstance(data, dict):

        for key, value in data.items():
            if isinstance(value, (dict, list)):
                convert_str_to_standard(value)
            else:
                data[key] = convert_str_to_standard(value)
    elif isinstance(data, list):
        for i, item in enumerate(data):
            if isinstance(item, (dict, list)):
                convert_str_to_standard(item)
            else:
                data[i] = convert_str_to_standard(item)
    else:
        if isinstance(data, str):
            try:
                return ast.literal_eval(data)
            except:
                return data
        else:
            return data


def is_label_return_field(field_meta_df, field_name):
    try:
        if field_name in field_meta_df or field_name in field_meta_df.index:
            return (
                field_name
                if field_name in field_meta_df["fieldname"].values
                else field_meta_df.loc[field_name, "fieldname"]
            )
        return field_name
    except KeyError:
        frappe.throw(
            "The column of the data do not match the column of the definition of doctype"
        )


def get_app_name():
    import os

    current_path = os.path.abspath(__file__)
    apps_path = os.path.join("apps", "")
    apps_index = current_path.find(apps_path)
    if apps_index > -1:
        path_after_apps = current_path[apps_index + len(apps_path) :]
        return path_after_apps.split(os.sep)[0]
    return None


def get_rules_cache(doctype, is_enabled=True):
    from collections import defaultdict

    data = frappe.get_list(
        "Data Rules",
        fields=["name", "which_gx", "args.args_name", "args.args_value"],
        filters={"which_doctype": doctype, "is_enabled": True},
    )
    rules = defaultdict(dict)
    for item in data:
        args_value = convert_str_to_standard(item["args_value"])
        if isinstance(args_value, str) and not args_value:
            continue
        rules[item["which_gx"]][item["args_name"]] = args_value
        rules[item["which_gx"]]["from"] = item["name"]
    # {'label':{'column': xxx, 'value':xxx, 'from': xxx}}
    return rules


def import_excel_file_from_server_to_document(
    doctype, file_path, import_type="Insert", submit_after_import=False, console=True
):
    """
    Process Excel files that already exist on the server and import data

    :param
        doctype: which doctype type to import into
        file_path: full path to the Excel file on the server
        import_type: "Insert" or "Update"
        submit_after_import: whether or not to submit the document after importing
        console: whether to import in command line mode or via UI mode. default command line
    """

    # create Data Import doctype
    data_import = frappe.new_doc("Data Import")
    data_import.reference_doctype = doctype
    data_import.import_type = import_type
    data_import.submit_after_import = submit_after_import

    file_doc = frappe.new_doc("File")
    file_doc.file_url = file_path
    file_doc.attached_to_doctype = "Data Import"
    file_doc.attached_to_name = data_import.name
    file_doc.insert()

    data_import.import_file = file_doc.file_url
    data_import.save()

    if console:
        # use Importer（like commandline）
        from frappe.core.doctype.data_import.importer import Importer

        importer = Importer(data_import)
        return importer.import_data()
    else:
        # use DataImport（like Web UI）
        return data_import.start_import()


def background_import(doctype, file_path):
    frappe.enqueue(
        "import_excel_from_server",
        doctype=doctype,
        file_path=file_path,
        queue="long",
        timeout=3000,
    )


def import_from_dataframe_to_document(
    doctype,
    df,
    import_type="Insert New Records",
    submit_after_import=False,
    console=True,
):
    """
    Import data to docytpe from Dataframe

    :param
        doctype (str): dotype to import into
        df (pd.DataFrame): Dataframe data
        import_type (str): "Insert" or "Update"
    """
    data_import = frappe.new_doc("Data Import")
    data_import.reference_doctype = doctype
    data_import.import_type = import_type
    data_import.submit_after_import = submit_after_import

    data_import.insert()
    from frappe.core.doctype.data_import.importer import Importer

    importer = Importer(doctype=doctype, file_path=None, data_import=data_import)

    headers = df.columns.tolist()
    rows = df.values.tolist()
    importer.parse_data_from_template(
        raw_data={"columns": headers, "data": rows},
        data_import=data_import,
        doctype=doctype,
        import_type=import_type,
    )

    if console:
        # use Importer（like commandline）
        from frappe.core.doctype.data_import.importer import Importer

        # importer = Importer(data_import)
        return importer.import_data()
    else:
        # use DataImport（like Web UI）
        return data_import.start_import()


def get_extension(self):
    import os

    return os.path.splitext(self.file_name)


import re


def remove_brackets_and_extension(filename):
    filename_without_brackets = re.sub(r"[（(].*?[）)]", "", filename)
    filename_without_single_bracket = re.sub(
        r"[（(]|[）)]", "", filename_without_brackets
    )
    filename_without_extension = re.sub(
        r"\.[^\.]+$", "", filename_without_single_bracket
    )
    return filename_without_extension.strip()
