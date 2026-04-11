from __future__ import annotations

import inspect
from typing import Any, Literal, get_args, get_origin


_JSON_TYPE_BY_PYTHON_TYPE: dict[type[Any], str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _schema_for_annotation(annotation: Any) -> dict[str, Any]:
    if annotation is inspect.Parameter.empty:
        return {"type": "string"}

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is Literal:
        values = list(args)
        if not values:
            return {"type": "string"}
        first = values[0]
        json_type = _JSON_TYPE_BY_PYTHON_TYPE.get(type(first), "string")
        return {"type": json_type, "enum": values}

    if origin in (list, tuple, set):
        item_annotation = args[0] if args else str
        return {"type": "array", "items": _schema_for_annotation(item_annotation)}

    if origin is dict:
        value_annotation = args[1] if len(args) > 1 else str
        return {"type": "object", "additionalProperties": _schema_for_annotation(value_annotation)}

    if origin in (Any,):
        return {"type": "object"}

    if origin is not None and type(None) in args:
        non_none = [arg for arg in args if arg is not type(None)]
        if len(non_none) == 1:
            return _schema_for_annotation(non_none[0])

    if annotation in _JSON_TYPE_BY_PYTHON_TYPE:
        return {"type": _JSON_TYPE_BY_PYTHON_TYPE[annotation]}

    annotation_name = getattr(annotation, "__name__", str(annotation))
    if annotation_name in {"str", "int", "float", "bool"}:
        reverse = {"str": "string", "int": "integer", "float": "number", "bool": "boolean"}
        return {"type": reverse[annotation_name]}

    return {"type": "string"}


def build_tool_definition(name: str, handler: Any) -> dict[str, Any]:
    signature = inspect.signature(handler)
    annotations = inspect.get_annotations(handler, eval_str=True)
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    for param_name, param in signature.parameters.items():
        if param_name in {"self", "cls", "caller"}:
            continue
        annotation = annotations.get(param_name, param.annotation)
        parameter_schema = _schema_for_annotation(annotation)
        if param.default is not inspect.Parameter.empty:
            parameter_schema["default"] = param.default
        parameters["properties"][param_name] = parameter_schema
        if param.default is inspect.Parameter.empty:
            parameters["required"].append(param_name)

    return {
        "name": name,
        "description": inspect.getdoc(handler) or "",
        "parameters": parameters,
    }
