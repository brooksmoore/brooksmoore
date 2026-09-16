import pathlib, shutil, subprocess, sys

SRC = pathlib.Path("voicelights/intents.py")
BAK = pathlib.Path("/tmp/intents.orig")
shutil.copy(SRC, BAK)
original = BAK.read_text()

MUTANTS = [
 ("and-merge for multi-target", 'if tokens[j] in {"and", "plus", "with"}:', 'if tokens[j] in {"plus", "with"}:'),
 ("dimmer is relative",        'if joined & DIMMER_WORDS:\n            return -1', 'if joined & DIMMER_WORDS:\n            return 0'),
 ("brighter is relative",      'if joined & BRIGHTER_WORDS:\n            return 1', 'if joined & BRIGHTER_WORDS:\n            return 0'),
 ("colour beats temperature",  'if color_name:\n            commands.append(', 'if color_name and not temp_name:\n            commands.append('),
 ("zero means off",            'if number <= 0:', 'if number < 0:'),
 ("wake word stripping",       'return tokens[i + 1 :]', 'return tokens'),
 ("pronoun memory",            'if any(t in PRONOUNS for t in rest) and carry:', 'if False:'),
 ("spoken number words",       'out.append(str(value))', 'out.append(tok)'),
 ("compound numbers",          'value += NUMBER_WORDS[tokens[i + 1]]', 'value += 0'),
 ("off outranks everything",   'if any(t in OFF_WORDS for t in tokens):\n            return [Command("off"', 'if False:\n            return [Command("off"'),
 ("scene lookup",              'if phrase and not any(t in OFF_WORDS for t in rest):', 'if False:'),
 ("longest-alias-first order",  'self.alias_index.sort(key=lambda pair: -len(pair[0].split()))', 'self.alias_index.sort(key=lambda pair: len(pair[0].split()))'),
 ("clause splitting on 'then'", '_SPLIT = re.compile(r"\\bthen\\b|\\balso\\b|;")', '_SPLIT = re.compile(r";")'),
 ("big/small step sizes",       'if joined & BIG_STEP_WORDS:\n            return BIG_STEP', 'if joined & BIG_STEP_WORDS:\n            return DEFAULT_STEP'),
 ("nonsense is rejected",       'result.understood = bool(result.commands)', 'result.understood = True'),
 ("percent clamping",           'return max(0, min(100, int(match.group(1))))', 'return int(match.group(1))'),
 ("status question rule",       'if self._is_status_question(tokens, explicit):', 'if False:'),
 ("status needs a light word",  'return explicit or any(t in ON_WORDS | OFF_WORDS for t in tokens)', 'return True'),
 ("on before toggle",           'if any(t in ON_WORDS for t in tokens):\n            return [Command("on"', 'if False:\n            return [Command("on"'),
]

survived = []
for name, old, new in MUTANTS:
    if old not in original:
        print(f"  ??  {name}: mutation target not found -- fix the harness")
        survived.append(name + " (NOT APPLIED)")
        continue
    SRC.write_text(original.replace(old, new, 1))
    proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                          capture_output=True, text=True)
    killed = proc.returncode != 0
    print(f"  {'killed ' if killed else 'SURVIVED'}  {name}")
    if not killed:
        survived.append(name)

SRC.write_text(original)
print()
print(f"{len(MUTANTS) - len(survived)}/{len(MUTANTS)} mutants killed")
if survived:
    print("survivors:", ", ".join(survived))
