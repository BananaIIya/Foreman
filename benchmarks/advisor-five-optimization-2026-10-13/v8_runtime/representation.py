"""Lossless formatting reduction, without interpreting scientific observations."""
import json


def compact_json(text):
    try:
        json.loads(text)
    except (ValueError, RecursionError):
        return text
    # Preserve number spellings, duplicate keys, ordering, escapes and all string
    # whitespace. A parse/dump round-trip could change those observations.
    result = []
    quoted = escaped = False
    for char in text:
        if quoted:
            result.append(char)
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
            result.append(char)
        elif char not in ' \t\r\n':
            result.append(char)
    return ''.join(result)


def bounded_text(text, limit):
    compact = compact_json(text)
    return {'text': compact[:limit], 'truncated': len(compact) > limit,
            'original_characters': len(text), 'represented_characters': len(compact),
            'json_whitespace_compacted': compact != text}
