// Parse only data. Never evaluate model output or execute a returned command.
const states = new Set(['working', 'completed', 'blocked']);

function valid(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    && states.has(value.status) && typeof value.summary === 'string'
    && Array.isArray(value.relevant_paths)
    && value.relevant_paths.every(path => typeof path === 'string');
}

export function parseCheckpoint(text) {
  if (typeof text !== 'string') return undefined;
  const source = text.trim();
  try {
    const value = JSON.parse(source);
    return valid(value) ? value : undefined;
  } catch { /* A public response may wrap its checkpoint in prose or a fence. */ }

  const found = [];
  let start = -1, depth = 0, quoted = false, escaped = false;
  for (let index = 0; index < source.length; index++) {
    const character = source[index];
    if (depth === 0) {
      if (character === '{') { start = index; depth = 1; quoted = false; escaped = false; }
      continue;
    }
    if (quoted) {
      if (escaped) escaped = false;
      else if (character === '\\') escaped = true;
      else if (character === '"') quoted = false;
      continue;
    }
    if (character === '"') quoted = true;
    else if (character === '{') depth++;
    else if (character === '}') {
      depth--;
      if (depth === 0) {
        try {
          const value = JSON.parse(source.slice(start, index + 1));
          if (valid(value)) found.push(value);
        } catch { /* Unparseable text is never guessed into a checkpoint. */ }
      }
    }
  }
  // Ambiguous multiple reports must go back to routing, not pick a convenient one.
  return found.length === 1 ? found[0] : undefined;
}
