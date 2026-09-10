#!/usr/bin/env python3
"""
Cfg-text <-> structured-values conversion and validation (issue #10).

The cfg file text and the structured parameter values convert into each
other faithfully:

- :func:`parse_cfg` — cfg file text -> structured values (coerced against
  the schema's field types; unknown commands and wrong arg counts are
  errors, so the schema is provably complete for whatever it parses).
- :func:`generate_cfg` — structured values -> full cfg file text, including
  the vendor comment headers, the sensorStop/flushCfg lead-in, and the
  trailing sensorStart (what the sensor consumes on apply).
- :func:`normalize_values` — raw (possibly string) input from the editor ->
  typed values or per-field errors. This is the gate an Apply must pass
  before anything is generated or persisted (ADR-0004: local per-param
  range validation blocks bad applies).

Pure functions over the schema — no IO (see ``config_store.py``).
"""

from setup_tool.config_schema import (
    CONTROL_COMMANDS,
    PARAM_BY_COMMAND,
    SCHEMA,
    SECTION_HEADERS,
    TIERS,
)

#: Line ending + final newline, byte-faithful to the vendor template.
NEWLINE = '\r\n'


def parse_cfg(text):
    """Parse cfg file text into structured values.

    Raises :class:`ValueError` naming the offending line for unknown
    commands or wrong argument counts — the template and everything this
    module generates must be fully described by the schema.
    """
    values = {p.command: [] for p in SCHEMA}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('%'):
            continue
        tokens = line.split()
        command, args = tokens[0], tokens[1:]
        if command in CONTROL_COMMANDS:
            continue
        param = PARAM_BY_COMMAND.get(command)
        if param is None:
            raise ValueError(f'unknown cfg command: {command!r}')
        if len(args) != len(param.fields):
            raise ValueError(
                f'{command}: expected {len(param.fields)} arguments, '
                f'got {len(args)}')
        values[command].append([
            float(a) if field.type == 'float' else int(a)
            for field, a in zip(param.fields, args)])
    # Every schema command must have appeared exactly `instances` times.
    for param in SCHEMA:
        if len(values[param.command]) != param.instances:
            raise ValueError(
                f'{param.command}: expected {param.instances} line(s), '
                f'got {len(values[param.command])}')
    return values


def cfg_command_lines(text):
    """The command lines of a cfg text, in order — comments/blanks dropped.

    What the sensor's CLI consumes line by line; Apply sends exactly these
    at CLI baud (``read_radar.send_config`` filters a file the same way).
    """
    return [line.strip() for line in text.splitlines()
            if line.strip() and not line.strip().startswith('%')]


def format_token(field, value):
    """Render one field value as its cfg-file token."""
    if field.fmt == 'g':
        # Compact float: 2 -> "2", 0.5 -> "0.5" (box/tracker commands).
        return format(float(value), '.12g')
    return format(value, field.fmt)


def generate_cfg(values):
    """Generate the full cfg file text from structured values.

    Layout is byte-faithful to the vendor template: comment headers, the
    sensorStop/flushCfg lead-in, parameters in schema order, sensorStart at
    the end. ``values`` must already be normalized (typed, schema-shaped).
    """
    lines = []
    for index, param in enumerate(SCHEMA):
        if param.command in SECTION_HEADERS and index > 0:
            lines.append('')
            lines.extend(SECTION_HEADERS[param.command])
        for instance in values[param.command]:
            tokens = [param.command]
            tokens += [format_token(field, value)
                       for field, value in zip(param.fields, instance)]
            lines.append(' '.join(tokens) + param.line_suffix)

    body = [
        # The SDK header comments sit above the sensorStop/flushCfg lead-in,
        # exactly like the vendor template.
        *SECTION_HEADERS[SCHEMA[0].command],
        'sensorStop',
        'flushCfg',
        *lines,
        'sensorStart',
        '',                       # trailing newline after the last command
    ]
    return NEWLINE.join(body)


def _error(command, instance, field, message):
    """One flat inline-error entry (shape the editor consumes)."""
    return {'command': command, 'instance': instance,
            'field': field, 'message': message}


def _coerce_field(field, raw, command, instance, errors):
    """Coerce one raw editor value against a field; None on failure."""
    try:
        value = int(str(raw), 10) if field.type == 'int' else float(raw)
    except (TypeError, ValueError):
        errors.append(_error(
            command, instance, field.key,
            f'{field.label}: {raw!r} is not a valid {field.type} value'))
        return None
    if (field.minimum is not None and value < field.minimum) or \
       (field.maximum is not None and value > field.maximum):
        unit = f' {field.unit}' if field.unit else ''
        errors.append(_error(
            command, instance, field.key,
            f'{field.label}: {value} outside documented range '
            f'[{field.minimum}, {field.maximum}]{unit}'))
        return None
    return value


def normalize_values(raw_values):
    """Validate raw editor input against the schema.

    ``raw_values`` has the same shape as structured values but the leaves
    may be strings (what the editor sends). Returns ``(values, errors)``:
    ``values`` is None if anything failed; ``errors`` is a flat list of
    ``{command, instance, field, message}`` for inline display.
    """
    errors = []
    values = {}

    for param in SCHEMA:
        if param.command not in raw_values:
            errors.append(_error(param.command, 0, '',
                                 f'{param.command}: missing parameter'))
            continue
        raw_instances = raw_values[param.command]
        if len(raw_instances) != param.instances:
            errors.append(_error(
                param.command, 0, '',
                f'{param.command}: expected {param.instances} instance(s), '
                f'got {len(raw_instances)}'))
            continue
        normalized = []
        for instance, raw_line in enumerate(raw_instances):
            if len(raw_line) != len(param.fields):
                errors.append(_error(
                    param.command, instance, '',
                    f'{param.command}: expected {len(param.fields)} values, '
                    f'got {len(raw_line)}'))
                normalized = None
                break
            line = []
            for field, raw in zip(param.fields, raw_line):
                value = _coerce_field(field, raw, param.command,
                                      instance, errors)
                if value is None:
                    normalized = None
                    break
                line.append(value)
            if normalized is None:
                break
            normalized.append(line)
        if normalized is not None:
            values[param.command] = normalized

    for command in raw_values:
        if command not in PARAM_BY_COMMAND:
            errors.append(_error(command, 0, '',
                                 f'{command}: unknown parameter'))

    if errors:
        return None, errors
    return values, []


def schema_json():
    """The schema as a JSON-serializable payload for the editor page.

    ``tiers`` carries the tier order, labels and guidance blurbs (the page
    renders them verbatim); ``params`` lists parameters in cfg order — the
    editor regroups them by tier.
    """
    return {
        'tiers': [
            {'id': t.id, 'label': t.label, 'blurb': t.blurb}
            for t in TIERS
        ],
        'params': [
            {
                'command': param.command,
                'tier': param.tier,
                'title': param.title,
                'info': param.info,
                'instances': param.instances,
                'instance_label': param.instance_label,
                'fields': [
                    {
                        'key': f.key,
                        'label': f.label,
                        'type': f.type,
                        'fmt': f.fmt,
                        'default': f.default,
                        'min': f.minimum,
                        'max': f.maximum,
                        'info': f.info,
                        'unit': f.unit,
                    }
                    for f in param.fields
                ],
            }
            for param in SCHEMA
        ],
    }
