#!/usr/bin/env python3
"""Topology gate: fail when a tracked tree describes private infrastructure.

Why this exists: a public repository once shipped a deploy runbook naming the
production gateway, its subnet and its VM map. Nothing in CI looked at that
class of text, so the next runbook would have gone out the same way.

What it flags (every rule is fail-closed):
  ip-public   an IPv4 address outside the built-in safe set
  ip-private  an RFC 1918 address (10/8, 172.16/12, 192.168/16)
  name        an internal host/user/lane name from the names file
  path        a reference to the private ops tree (its directory name followed by a slash)

Built-in safe IPv4 set: 0.0.0.0, 127/8, 255.255.255.255, the three RFC 5737
documentation ranges, and 169.254.169.254 (the cloud-metadata SSRF constant).
Anything else needs an allowlist entry.

The names file stores SHA-256 hashes, not names, so the file that guards the
topology does not itself publish it. Add a name with `--hash <name> <type>`.

Exceptions live ONLY in the allowlist file, one per line, and every line must
carry a reason after `#`. There is no skip flag and no inline marker.

  path <glob> <rule|*>   # why   -- findings of <rule> in files matching <glob>
  value <literal>        # why   -- one exact IPv4 literal, anywhere
  tokenhash <sha256>     # why   -- one exact token (lowercased), anywhere

Exit codes: 0 clean, 1 findings, 2 configuration error (missing or malformed
names/allowlist file counts as a failure, never as "nothing to check").

Canonical copy lives in a private ops tree; per-repo copies are vendored.
"""
import argparse
import fnmatch
import hashlib
import os
import re
import subprocess
import sys

NAMES_DEFAULT = ".topology-gate/names.sha256"
ALLOW_DEFAULT = ".topology-gate/allowlist.txt"

IPV4 = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d]|\.\d)")
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
# Split so that this file does not trip its own rule.
PATHREF = re.compile(r"(?<![A-Za-z0-9_.-])(?:b" r"ob|fleet" r"-ops)/")
NAME_TYPES = ("full", "any", "first", "last")
RULES = ("ip-public", "ip-private", "name", "path")


def sha(s):
    return hashlib.sha256(s.lower().encode()).hexdigest()


def safe_ip(o):
    a, b, c, d = o
    if o == (0, 0, 0, 0) or o == (255, 255, 255, 255) or o == (169, 254, 169, 254):
        return True
    if a == 127:
        return True
    return (a, b, c) in ((192, 0, 2), (198, 51, 100), (203, 0, 113))


def is_private(o):
    a, b = o[0], o[1]
    return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)


class ConfigError(Exception):
    pass


def read_config(path, what):
    """Lines of a config file; a read failure is a path-free ConfigError (exit 2), never a traceback."""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().splitlines()
    except (OSError, UnicodeDecodeError):
        raise ConfigError("%s unreadable (fail-closed)" % what) from None


def load_names(path):
    if not os.path.isfile(path):
        raise ConfigError("names file missing (fail-closed)")
    out = {t: set() for t in NAME_TYPES}
    for n, raw in enumerate(read_config(path, "names file"), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2 or parts[0] not in NAME_TYPES or not re.fullmatch(r"[0-9a-f]{64}", parts[1]):
            raise ConfigError("names file line %d: expected '<%s> <sha256>'" % (n, "|".join(NAME_TYPES)))
        out[parts[0]].add(parts[1])
    if not any(out.values()):
        raise ConfigError("names file is empty (fail-closed)")
    return out


def load_allow(path):
    paths, values, tokens = [], set(), set()
    if not os.path.isfile(path):
        return paths, values, tokens
    for n, raw in enumerate(read_config(path, "allowlist"), 1):
        body, _, why = raw.partition("#")
        body = body.strip()
        if not body:
            continue
        if not why.strip():
            raise ConfigError("allowlist line %d: entry without a reason after '#'" % n)
        p = body.split()
        if p[0] == "path" and len(p) == 3 and (p[2] == "*" or p[2] in RULES):
            paths.append((p[1], p[2]))
        elif p[0] == "value" and len(p) == 2:
            values.add(p[1])
        elif p[0] == "tokenhash" and len(p) == 2 and re.fullmatch(r"[0-9a-f]{64}", p[1]):
            tokens.add(p[1])
        else:
            # Never echo the entry: it may hold the very value being allowlisted.
            raise ConfigError("allowlist line %d: malformed entry" % n)
    return paths, values, tokens


def name_hit(tok, names):
    low = tok.lower()
    h = sha(low)
    if h in names["full"]:
        return True
    segs = [s for s in re.split(r"[-_]", low) if s]
    if any(sha(s) in names["any"] for s in segs):
        return True
    # first/last only make sense on a compound token: a bare "vm" is just a word.
    if len(segs) < 2:
        return False
    return sha(segs[0]) in names["first"] or sha(segs[-1]) in names["last"]


def scan_text(path, text, names, allow):
    apaths, avalues, atokens = allow
    found = []

    def ok(rule):
        return any(fnmatch.fnmatchcase(path, g) and r in ("*", rule) for g, r in apaths)

    for ln, line in enumerate(text.splitlines(), 1):
        for m in IPV4.finditer(line):
            o = tuple(int(x) for x in m.groups())
            if max(o) > 255 or safe_ip(o):
                continue
            lit = m.group(0)
            rule = "ip-private" if is_private(o) else "ip-public"
            if lit in avalues or ok(rule):
                continue
            found.append((path, ln, rule, lit))
        for m in TOKEN.finditer(line):
            tok = m.group(0)
            if name_hit(tok, names) and sha(tok) not in atokens and not ok("name"):
                found.append((path, ln, "name", tok))
        for m in PATHREF.finditer(line):
            if not ok("path"):
                found.append((path, ln, "path", m.group(0)))
    return found


def tracked(root, rev):
    if rev:
        out = subprocess.run(["git", "-C", root, "ls-tree", "-r", "-z", "--name-only", rev],
                             check=True, capture_output=True).stdout
    else:
        out = subprocess.run(["git", "-C", root, "ls-files", "-z"], check=True, capture_output=True).stdout
    return [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]


def read(root, rev, p):
    if rev:
        r = subprocess.run(["git", "-C", root, "show", "%s:%s" % (rev, p)], capture_output=True)
        if r.returncode != 0:
            raise ConfigError("a tracked blob is unreadable (fail-closed)")
        data = r.stdout
    else:
        full = os.path.join(root, p)
        if os.path.islink(full):
            # Same text `git show rev:path` yields: the link target, never the file behind it.
            try:
                data = os.readlink(full).encode("utf-8", "surrogateescape")
            except OSError:
                raise ConfigError("a tracked symlink is unreadable (fail-closed)") from None
        elif not os.path.isfile(full):
            # Absent, a directory in place of a file, or not inspectable: skipping would let an
            # altered checkout pass unscanned. (A submodule would land here too, loudly: none are used.)
            raise ConfigError("a tracked file is missing or not inspectable (fail-closed)")
        else:
            try:
                with open(full, "rb") as f:
                    data = f.read()
            except OSError:
                raise ConfigError("a tracked file is unreadable (fail-closed)") from None
    if b"\0" in data:
        # Binary (or NUL-padded text): never skipped, or a leading NUL would hide a whole file.
        # Scan the printable runs, so image bytes do not raise false hits.
        return "\n".join(m.decode("ascii") for m in re.findall(rb"[\x20-\x7e]{2,}", data))
    return data.decode("utf-8", "replace")


def run(root, rev, names_path, allow_path, self_exempt=()):
    names = load_names(names_path)
    allow = load_allow(allow_path)
    files = tracked(root, rev)
    findings = []
    for p in files:
        # The path itself is text a stranger sees: scan it as well as the body.
        # Slashes kept for the ops-tree reference, blanked so each segment is a name token.
        by_path = list(dict.fromkeys(scan_text(p, p, names, allow) + scan_text(p, p.replace("/", " "), names, allow)))
        # Mask on the raw match too: an allowlisted path is still a path that matches a rule.
        masked = bool(by_path) or bool(
            scan_text(p, p, names, ([], set(), set())) + scan_text(p, p.replace("/", " "), names, ([], set(), set())))
        body = []
        if p not in self_exempt:
            text = read(root, rev, p)
            if text is not None:
                body = scan_text(p, text, names, allow)
        # A flagged path must not be restated in the log either: label it by hash.
        label = "<path#%s>" % sha(p)[:8] if masked else p
        findings += [(label,) + f[1:] for f in by_path + body]
    return files, findings


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=".")
    ap.add_argument("--rev", help="scan this git revision instead of the working tree")
    ap.add_argument("--names", help="names file (default: <root>/%s)" % NAMES_DEFAULT)
    ap.add_argument("--allowlist", help="allowlist file (default: <root>/%s)" % ALLOW_DEFAULT)
    ap.add_argument("--hash", nargs=2, metavar=("NAME", "TYPE"),
                    help="print the names-file line for NAME (TYPE: %s)" % "|".join(NAME_TYPES))
    a = ap.parse_args(argv)
    if a.hash:
        if a.hash[1] not in NAME_TYPES:
            print("TYPE must be one of %s" % ", ".join(NAME_TYPES), file=sys.stderr)
            return 2
        print("%s %s" % (a.hash[1], sha(a.hash[0])))
        return 0
    names = a.names or os.path.join(a.root, NAMES_DEFAULT)
    allow = a.allowlist or os.path.join(a.root, ALLOW_DEFAULT)
    try:
        files, findings = run(a.root, a.rev, names, allow)
    except ConfigError as e:
        print("topology-gate: CONFIG ERROR: %s" % e, file=sys.stderr)
        return 2
    # The matched text is the very thing we are keeping out of public view, and the
    # name dictionary is hashed so it is not restated either: report rule and place.
    for path, ln, rule, what in findings:
        print("%s:%d: [%s]" % (path, ln, rule))
    print("topology-gate: scanned %d files, %d finding(s)" % (len(files), len(findings)))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
